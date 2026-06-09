# 동시실행 잡 큐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 담당자 여러 명이 동시에 업로드해도 codex/kordoc 잡이 한꺼번에 폭주하지 않도록, 동시 실행을 N개로 제한하고 초과분은 대기시키는 인프로세스 바운디드 잡 큐를 추가한다.

**Architecture:** 잡 전용 `ThreadPoolExecutor(max_workers=N)`를 감싼 `JobRunner`를 두고, `POST /jobs`가 기존 fire-and-forget `run_in_executor(None, ...)` 대신 이 실행기에 `run_job`을 submit한다. N 초과 잡은 실행기 내부 큐에 FIFO로 대기(상태 `queued`)하다가 슬롯이 나면 시작된다. n8n·Redis·컨테이너 분리 없이 단일 컨테이너 유지, 순수 모듈(convert/run_codex/worker/job_manager)은 불변.

**Tech Stack:** Python 3.14, FastAPI, `concurrent.futures.ThreadPoolExecutor`. 테스트는 pytest. 모든 python/pytest는 프로젝트 venv(`./venv/bin/python`)로 실행.

설계 spec: `docs/superpowers/specs/2026-06-09-concurrent-job-queue-design.md`
확장 로드맵: `docs/architecture/scaling-roadmap.md`

---

## 파일 구조

생성:
- `web/job_runner.py` — 잡 전용 바운디드 실행기(`JobRunner`). 동시성 상한·정리 훅·Tier 2 교체 seam.
- `tests/test_job_runner.py`

수정:
- `web/app.py` — `JobRunner` 지연 생성(`get_runner`/`reset_runner`/`_max_concurrency`), `create_job`이 submit, 종료 핸들러.
- `tests/test_app.py` — `reset_runner()` 사용 + 동시성 상한 잡 완료 테스트.
- `web/static/index.html` — 제출 직후 "대기 중" 표시.
- `docker-compose.yml` — `REPORT_BOT_MAX_CONCURRENCY` 환경변수.
- `README.md` — 환경변수 한 줄 설명.

런타임 동작: 동시 실행 잡 ≤ N → 동시 codex 프로세스 ≤ N → RAM·API rate limit 통제.

---

## Task 1: JobRunner — 바운디드 잡 실행기

**Files:**
- Create: `web/job_runner.py`
- Test: `tests/test_job_runner.py`

`ThreadPoolExecutor`를 얇게 감싸 동시성 상한을 둔다. FastAPI/순수 모듈과 무관하게 단독 테스트.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_job_runner.py`:

```python
import threading

from web.job_runner import JobRunner


def test_one_worker_runs_tasks_serially():
    runner = JobRunner(max_workers=1)
    try:
        release = threading.Event()
        task1_running = threading.Event()
        started2 = threading.Event()

        def task1():
            task1_running.set()
            release.wait(2)

        def task2():
            started2.set()

        f1 = runner.submit(task1)
        assert task1_running.wait(2)          # task1 이 실제로 실행 중
        runner.submit(task2)
        # 워커가 1개뿐이므로 task1 이 점유하는 동안 task2 는 시작되면 안 된다
        assert not started2.wait(0.3)
        release.set()                          # task1 해제 → task2 차례
        f1.result(2)
        assert started2.wait(2)
    finally:
        runner.shutdown(wait=True)


def test_two_workers_run_concurrently():
    runner = JobRunner(max_workers=2)
    try:
        # Barrier(2): 두 작업이 동시에 도달해야 통과, 직렬이면 timeout→BrokenBarrierError
        barrier = threading.Barrier(2, timeout=2)

        def task():
            barrier.wait()

        f1 = runner.submit(task)
        f2 = runner.submit(task)
        f1.result(3)
        f2.result(3)                           # 동시 실행이 아니면 여기서 예외가 재발생
    finally:
        runner.shutdown(wait=True)


def test_max_workers_below_one_is_clamped():
    runner = JobRunner(max_workers=0)
    try:
        assert runner.max_workers == 1
        assert runner.submit(lambda: 42).result(2) == 42
    finally:
        runner.shutdown(wait=True)
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_job_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.job_runner'`

- [ ] **Step 3: 구현**

`web/job_runner.py`:

```python
"""잡 전용 바운디드 실행기 — 동시 N개까지만 실행, 초과분은 FIFO 대기."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class JobRunner:
    """ThreadPoolExecutor 를 감싸 동시 실행 수를 max_workers 로 제한한다.

    max_workers 를 초과해 submit 된 작업은 내부 큐에 FIFO 로 쌓였다가
    워커가 비면 실행된다. 별도 큐 구현 없이 "N개 동시 + 나머지 대기"가 보장된다.
    """

    def __init__(self, max_workers: int):
        if max_workers < 1:
            max_workers = 1
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        return self._executor.submit(fn, *args)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_job_runner.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add web/job_runner.py tests/test_job_runner.py
git commit -m "feat: add JobRunner with bounded concurrency"
```

---

## Task 2: app.py 에 JobRunner 연결 + 종료 정리

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_app.py`

`create_job`이 기본 executor 대신 `JobRunner`에 submit하도록 바꾸고, 동시성을 env로 설정한다.

- [ ] **Step 1: 실패 테스트 작성 (app 테스트에 동시성 잡 완료 검증 추가)**

`tests/test_app.py`의 happy-path 테스트에서 `app_module.reset_manager()` 호출 바로 다음 줄에 `app_module.reset_runner()`를 추가한다. 즉 다음 블록을:

```python
    monkeypatch.setattr(app_module, "convert", fake_convert)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    app_module.reset_manager()
```

다음으로 바꾼다:

```python
    monkeypatch.setattr(app_module, "convert", fake_convert)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    app_module.reset_manager()
    app_module.reset_runner()
```

그리고 `tests/test_app.py` 맨 끝에 아래 테스트를 추가한다:

```python
def test_concurrency_limit_one_still_completes_all_jobs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setenv("REPORT_BOT_MAX_CONCURRENCY", "1")

    def fake_convert(upload_path, converted_root):
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def fake_codex(converted_dir, request_text, report_path, on_event, **kwargs):
        Path(report_path).write_text("# 리포트", encoding="utf-8")

    monkeypatch.setattr(app_module, "convert", fake_convert)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    app_module.reset_manager()
    app_module.reset_runner()  # env=1 을 반영한 새 runner

    client = TestClient(app_module.app)
    try:
        ids = []
        for _ in range(2):
            resp = client.post(
                "/jobs",
                files={"file": ("a.hwp", b"dummy", "application/octet-stream")},
                data={"request_text": "정리"},
            )
            assert resp.status_code == 200
            ids.append(resp.json()["job_id"])

        # 동시성 1이라 직렬 처리되더라도 두 잡 모두 결국 done 에 도달해야 한다
        for job_id in ids:
            assert _wait_state(client, job_id, "done") == "done"
    finally:
        app_module.reset_runner()  # 다음 테스트를 위해 기본 동시성 runner 로 되돌림
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_app.py -q`
Expected: FAIL — `AttributeError: module 'web.app' has no attribute 'reset_runner'`

- [ ] **Step 3: 구현 — app.py 수정 (3곳)**

**(3a)** import 추가. `web/app.py` 상단의

```python
import asyncio
import json
from pathlib import Path
```

를 다음으로 바꾼다:

```python
import asyncio
import json
import os
from pathlib import Path
```

그리고

```python
from web.codex_runner import run_codex
from web.job_manager import JobManager
from web.pipeline_runner import convert
from web.report_renderer import render_html
from web.worker import run_job
```

를 다음으로 바꾼다(`JobRunner` import 추가):

```python
from web.codex_runner import run_codex
from web.job_manager import JobManager
from web.job_runner import JobRunner
from web.pipeline_runner import convert
from web.report_renderer import render_html
from web.worker import run_job
```

**(3b)** runner 지연 생성 훅 추가. `reset_manager` 함수 정의 블록:

```python
def reset_manager() -> None:
    """테스트에서 JOBS_DIR 변경 후 매니저를 다시 만들기 위한 훅."""
    global _manager
    _manager = None
```

바로 다음에 아래를 추가한다:

```python


_runner: JobRunner | None = None


def _max_concurrency() -> int:
    """동시 실행 잡 수. 환경변수 REPORT_BOT_MAX_CONCURRENCY, 기본 3, 잘못된 값은 3으로 폴백."""
    try:
        n = int(os.environ.get("REPORT_BOT_MAX_CONCURRENCY", "3"))
    except ValueError:
        return 3
    return n if n >= 1 else 3


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner(max_workers=_max_concurrency())
    return _runner


def reset_runner() -> None:
    """테스트에서 동시성/runner 를 다시 만들기 위한 훅. 기존 runner 는 정리한다."""
    global _runner
    if _runner is not None:
        _runner.shutdown(wait=False)
    _runner = None


@app.on_event("shutdown")
def _shutdown_runner() -> None:
    if _runner is not None:
        _runner.shutdown(wait=False)
```

**(3c)** `create_job`이 runner 에 submit하도록 변경. 현재:

```python
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None, run_job, job, manager, convert, run_codex
    )
    return {"job_id": job.id}
```

를 다음으로 바꾼다:

```python
    get_runner().submit(run_job, job, manager, convert, run_codex)
    return {"job_id": job.id}
```

> 참고: `asyncio` import 는 그대로 둔다 — `job_events`의 SSE 스트림에서 여전히 사용한다.
> SSE 엔드포인트는 변경하지 않는다(잡이 전용 풀로 빠져 기본 풀과 더는 경합하지 않음).

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_app.py -q`
Expected: PASS (test_concurrency_limit_one_still_completes_all_jobs 포함 전부 통과)

- [ ] **Step 5: 전체 스위트 회귀 확인**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS (기존 19 + job_runner 3 + app 동시성 1 = 23 passed)

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_app.py
git commit -m "feat: route jobs through bounded JobRunner with env-configurable concurrency"
```

---

## Task 3: 프론트 — 제출 직후 "대기 중" 표시

**Files:**
- Modify: `web/static/index.html`

대기열에 들어간 동안 화면이 비어 보이지 않도록, 제출 직후 상태를 "대기 중"으로 표시한다.
첫 `status` SSE 이벤트(`converting`)가 오면 기존 onmessage 로직이 실제 상태로 덮어쓴다.

- [ ] **Step 1: 구현 — index.html 의 제출 핸들러 한 줄 추가**

`web/static/index.html`에서 다음 블록을 찾는다:

```javascript
      const resp = await fetch('/jobs', { method: 'POST', body: fd });
      if (!resp.ok) { statusEl.textContent = '업로드 실패'; statusEl.className = 'status err'; return; }
      const { job_id } = await resp.json();
```

다음으로 바꾼다(`job_id` 수신 직후 "대기 중" 표시 추가):

```javascript
      const resp = await fetch('/jobs', { method: 'POST', body: fd });
      if (!resp.ok) { statusEl.textContent = '업로드 실패'; statusEl.className = 'status err'; return; }
      const { job_id } = await resp.json();
      statusEl.textContent = '대기 중 — 분석 차례를 기다리는 중…';
```

- [ ] **Step 2: 정적 페이지 회귀 확인 (전체 스위트, index 서빙 포함)**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS (23 passed — test_index_served 포함, HTML 은 여전히 "분석" 포함)

- [ ] **Step 3: 인라인 JS 파싱 확인**

Run: `node -e "const fs=require('fs');const h=fs.readFileSync('web/static/index.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];const js=m.map(s=>s.replace(/<\/?script>/g,'')).join('\n');new Function(js);console.log('JS OK');"`
Expected: `JS OK`

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html
git commit -m "feat: show queued status immediately after submit"
```

---

## Task 4: 배포 설정 — compose 환경변수 + README

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: docker-compose.yml 에 환경변수 추가**

`docker-compose.yml`의

```yaml
    environment:
      - CODEX_HOME=/root/.codex
```

를 다음으로 바꾼다:

```yaml
    environment:
      - CODEX_HOME=/root/.codex
      - REPORT_BOT_MAX_CONCURRENCY=3
```

- [ ] **Step 2: README 에 환경변수 설명 추가**

`README.md`에서 다음 문장을 찾는다:

```markdown
산출물은 `jobs/<job_id>/`(converted/, report.md, codex_log.jsonl, status.json)에 저장됩니다.
```

다음으로 바꾼다(환경변수 설명 추가):

```markdown
산출물은 `jobs/<job_id>/`(converted/, report.md, codex_log.jsonl, status.json)에 저장됩니다.

동시 실행 잡 수는 환경변수 `REPORT_BOT_MAX_CONCURRENCY`로 정합니다(기본 3). 초과분은 대기열에서 차례를 기다립니다.
```

- [ ] **Step 3: 변경 확인**

Run: `git diff --stat`
Expected: `docker-compose.yml` 와 `README.md` 만 변경됨.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml README.md
git commit -m "docs: document REPORT_BOT_MAX_CONCURRENCY env var"
```

---

## Self-Review (작성자 점검 완료)

- **Spec 커버리지:** 바운디드 실행기(Task 1) · env 동시성 N(Task 2: `_max_concurrency`) · create_job submit 교체(Task 2: 3c) · 종료 정리(Task 2: `_shutdown_runner`) · reset_runner 정리 훅(Task 2: 3b) · 프론트 "대기 중"(Task 3) · compose/README 문서화(Task 4) · SSE 불변(Task 2 참고 노트) · 순수 모듈 불변(이 계획은 dispatch 계층만 수정) — spec 모든 항목 대응. 제외 항목(Redis/컨테이너 분리/잡 영속/대기열 순번 숫자)은 계획에 포함하지 않음(정상).
- **플레이스홀더:** 모든 코드 스텝에 완전한 코드/정확한 find-replace 포함. TBD 없음.
- **타입/시그니처 일관성:** `JobRunner(max_workers).submit(fn, *args)`/`shutdown(wait)`/`.max_workers` — Task 1 정의와 Task 2 사용 일치. `get_runner`/`reset_runner`/`_max_concurrency`/`_shutdown_runner` 이름이 app.py 내·테스트(`app_module.reset_runner`)와 일치. `create_job`의 submit 인자 `(run_job, job, manager, convert, run_codex)`는 기존 `run_job(job, manager, convert_fn, codex_fn)` 시그니처와 일치.
- **알려진 주의:** `@app.on_event("shutdown")`은 최신 FastAPI에서 deprecated(lifespan 권장)지만 현 버전에서 정상 동작하며 이 규모에선 충분. 추후 lifespan 전환은 별도 작업.
