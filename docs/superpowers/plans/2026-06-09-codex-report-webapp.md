# Codex 분석 리포트 웹앱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 담당자가 한글/엑셀 파일을 업로드하고 요청문을 적으면, 기존 kordoc 파이프라인으로 변환한 데이터를 `codex exec`가 읽어 분석 리포트(Markdown)를 만들어 브라우저에 보여주는 로컬 웹앱을 만든다.

**Architecture:** FastAPI 백엔드가 잡(Job) 단위로 업로드 → 변환(`kordoc_pipeline.process_file`) → 분석(`codex exec --json`) 을 백그라운드에서 수행한다. codex 의 JSONL 이벤트는 잡별 큐를 거쳐 SSE 로 브라우저에 실시간 중계되고(C), 완료 시 `report.md` 를 렌더한다. `job_manager`/`codex_runner`/`pipeline_runner`/`worker` 는 FastAPI 를 모르는 순수 모듈이라 웹서버 없이 단위테스트한다.

**Tech Stack:** Python 3.14, FastAPI + uvicorn, asyncio + 스레드풀(블로킹 subprocess 격리), 기존 `scripts/kordoc_pipeline.py` 재사용, codex CLI(`codex exec`), 프론트는 의존성 없는 HTML + `marked.js` CDN. 테스트는 pytest.

설계 spec: `docs/superpowers/specs/2026-06-09-codex-report-webapp-design.md`

---

## 파일 구조

생성:
- `web/__init__.py` — 패키지 마커
- `web/job_manager.py` — 잡 생명주기·상태·디스크 레이아웃·이벤트 큐
- `web/codex_runner.py` — codex 프롬프트 조립 + `codex exec` 호출 + JSONL 이벤트 파싱
- `web/pipeline_runner.py` — 기존 `kordoc_pipeline.process_file` 호출 래퍼
- `web/worker.py` — 한 잡의 변환→분석 오케스트레이션(의존성 주입으로 테스트 가능)
- `web/app.py` — FastAPI 라우트(`POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/report`, `GET /jobs/{id}/events`) + 정적 HTML 서빙
- `web/static/index.html` — 업로드 폼 + 요청문 + SSE 라이브 로그 + 리포트 렌더
- `tests/__init__.py`
- `tests/test_job_manager.py`
- `tests/test_codex_runner.py`
- `tests/test_pipeline_runner.py`
- `tests/test_worker.py`
- `tests/test_app.py`
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

수정:
- `requirements.txt` — fastapi, uvicorn, python-multipart 추가
- `README.md` — 웹앱 실행법 섹션 추가

런타임 산출물(`.gitignore` 에 `jobs/` 이미 포함됨):
```
jobs/<job_id>/
  request.txt  upload/<원본>  converted/<docid>/...  report.md  status.json  codex_log.jsonl
```

---

## Task 1: 의존성 + 패키지 스켈레톤

**Files:**
- Modify: `requirements.txt`
- Create: `web/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: requirements.txt 갱신**

`requirements.txt` 전체를 아래로 교체:

```txt
openpyxl>=3.1.0
fastapi>=0.115.0
uvicorn>=0.30.0
python-multipart>=0.0.9
```

- [ ] **Step 2: 의존성 설치**

Run: `python3 -m pip install -r requirements.txt`
Expected: fastapi, uvicorn, python-multipart, openpyxl 설치 성공.

- [ ] **Step 3: 패키지 마커 생성**

`web/__init__.py` 내용:

```python
"""Codex 분석 리포트 웹앱 패키지."""
```

`tests/__init__.py` 내용:

```python
```

- [ ] **Step 4: import 스모크 확인**

Run: `python3 -c "import fastapi, uvicorn; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt web/__init__.py tests/__init__.py
git commit -m "chore: add web deps and package skeleton"
```

---

## Task 2: job_manager — 잡 상태·디스크·이벤트 큐

**Files:**
- Create: `web/job_manager.py`
- Test: `tests/test_job_manager.py`

잡 상태와 디스크 레이아웃, 스레드 안전 이벤트 큐를 관리한다. FastAPI 를 모른다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_job_manager.py`:

```python
import json
from pathlib import Path

from web.job_manager import JobManager, JobState


def test_create_writes_upload_request_and_status(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="요람.hwp", file_bytes=b"hello", request_text="학과별 정원 정리")

    assert job.state == JobState.QUEUED
    assert (job.dir / "upload" / "요람.hwp").read_bytes() == b"hello"
    assert (job.dir / "request.txt").read_text(encoding="utf-8") == "학과별 정원 정리"

    status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "queued"
    assert mgr.get(job.id) is job


def test_set_state_persists_and_emits_event(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="r")

    mgr.set_state(job, JobState.ANALYZING, step="codex 실행 중")

    status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "analyzing"
    assert status["step"] == "codex 실행 중"

    event = job.events.get_nowait()
    assert event["type"] == "status"
    assert event["state"] == "analyzing"
    assert event["step"] == "codex 실행 중"


def test_set_failed_records_error(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="r")

    mgr.set_state(job, JobState.FAILED, error="변환 실패: kordoc 오류")

    status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error"] == "변환 실패: kordoc 오류"


def test_push_event_appends_to_log_and_queue(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="r")

    mgr.push_event(job, {"type": "codex", "msg": "reading document.md"})

    line = (job.dir / "codex_log.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line) == {"type": "codex", "msg": "reading document.md"}
    assert job.events.get_nowait() == {"type": "codex", "msg": "reading document.md"}
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_job_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.job_manager'`

- [ ] **Step 3: 구현**

`web/job_manager.py`:

```python
"""잡 생명주기, 디스크 레이아웃, 스레드 안전 이벤트 큐."""
from __future__ import annotations

import json
import queue
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class JobState(str, Enum):
    QUEUED = "queued"
    CONVERTING = "converting"
    ANALYZING = "analyzing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    dir: Path
    upload_path: Path
    request_text: str
    state: JobState = JobState.QUEUED
    step: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    events: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)

    @property
    def converted_dir(self) -> Path:
        return self.dir / "converted"

    @property
    def report_path(self) -> Path:
        return self.dir / "report.md"

    @property
    def log_path(self) -> Path:
        return self.dir / "codex_log.jsonl"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}

    def create(self, upload_filename: str, file_bytes: bytes, request_text: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.base_dir / job_id
        (job_dir / "upload").mkdir(parents=True, exist_ok=True)

        upload_path = job_dir / "upload" / upload_filename
        upload_path.write_bytes(file_bytes)
        (job_dir / "request.txt").write_text(request_text, encoding="utf-8")

        now = _now()
        job = Job(
            id=job_id,
            dir=job_dir,
            upload_path=upload_path,
            request_text=request_text,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job
        self._write_status(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def set_state(self, job: Job, state: JobState, step: str = "", error: str = "") -> None:
        job.state = state
        if step:
            job.step = step
        if error:
            job.error = error
        job.updated_at = _now()
        self._write_status(job)
        job.events.put({"type": "status", "state": state.value, "step": job.step, "error": job.error})

    def push_event(self, job: Job, event: dict[str, Any]) -> None:
        with job.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        job.events.put(event)

    def _write_status(self, job: Job) -> None:
        status = {
            "id": job.id,
            "state": job.state.value,
            "step": job.step,
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        (job.dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/test_job_manager.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add web/job_manager.py tests/test_job_manager.py
git commit -m "feat: add job_manager with state machine and event queue"
```

---

## Task 3: codex_runner — 프롬프트 조립 + codex 호출

**Files:**
- Create: `web/codex_runner.py`
- Test: `tests/test_codex_runner.py`

`codex exec` 를 동기 subprocess 로 실행하며 stdout JSONL 을 한 줄씩 콜백으로 흘린다(웹 레이어가 이 콜백을 잡 큐에 연결). 비동기는 웹 레이어에서 스레드풀로 격리하므로 여기서는 동기로 구현해 테스트가 단순하다. 테스트는 진짜 codex 대신 가짜 codex 스크립트를 주입한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_codex_runner.py`:

```python
import os
import stat
import sys
from pathlib import Path

import pytest

from web.codex_runner import build_prompt, run_codex, CodexError


def test_build_prompt_includes_request_and_instructions():
    prompt = build_prompt("학과별 입학정원 추이를 표로 정리해줘")
    assert "학과별 입학정원 추이를 표로 정리해줘" in prompt
    assert "converted/" in prompt
    assert "Markdown" in prompt


def _write_fake_codex(path: Path, body: str) -> str:
    """sys.argv 를 스캔해 -o 경로에 리포트를 쓰고 JSONL 을 내보내는 가짜 codex."""
    script = path / "fake_codex.py"
    script.write_text(
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "out = argv[argv.index('-o') + 1] if '-o' in argv else None\n"
        "print(json.dumps({'type': 'item', 'text': 'reading document.md'}))\n"
        "print(json.dumps({'type': 'item', 'text': 'writing report'}))\n"
        "sys.stdout.flush()\n"
        f"open(out, 'w', encoding='utf-8').write({body!r})\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def test_run_codex_streams_events_and_writes_report(tmp_path: Path):
    converted = tmp_path / "converted"
    converted.mkdir()
    report = tmp_path / "report.md"
    log = tmp_path / "codex_log.jsonl"
    codex_cmd = _write_fake_codex(tmp_path, "# 분석 리포트\n표 내용")

    events: list[dict] = []
    run_codex(
        converted_dir=converted,
        request_text="정리해줘",
        report_path=report,
        on_event=events.append,
        codex_cmd=codex_cmd,
    )

    assert report.read_text(encoding="utf-8") == "# 분석 리포트\n표 내용"
    assert any(e.get("text") == "reading document.md" for e in events)
    assert len(events) == 2


def test_run_codex_raises_on_nonzero_exit(tmp_path: Path):
    converted = tmp_path / "converted"
    converted.mkdir()
    report = tmp_path / "report.md"
    fail_script = tmp_path / "fail.py"
    fail_script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    codex_cmd = f"{sys.executable} {fail_script}"

    with pytest.raises(CodexError):
        run_codex(
            converted_dir=converted,
            request_text="x",
            report_path=report,
            on_event=lambda e: None,
            codex_cmd=codex_cmd,
        )
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_codex_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.codex_runner'`

- [ ] **Step 3: 구현**

`web/codex_runner.py`:

```python
"""codex exec 호출: 프롬프트 조립 + JSONL 이벤트 스트리밍 + report.md 생성."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Callable

SYSTEM_INSTRUCTION = (
    "너는 `converted/` 폴더에 있는 한글 문서 변환 데이터"
    "(document.md, facts.json, tables_long.csv, table_*.csv)를 읽고 "
    "분석 리포트를 작성하는 어시스턴트다. 추측하지 말고 데이터 근거를 표·수치로 제시하라. "
    "근거가 없으면 \"데이터에서 확인 불가\"라고 명시하라. "
    "아래 담당자 요청에 맞춰 한국어 Markdown 리포트를 작성하라."
)


class CodexError(RuntimeError):
    pass


def build_prompt(request_text: str) -> str:
    return f"{SYSTEM_INSTRUCTION}\n\n[담당자 요청]\n{request_text}\n"


def run_codex(
    converted_dir: Path,
    request_text: str,
    report_path: Path,
    on_event: Callable[[dict], None],
    codex_cmd: str = "codex",
    timeout: int = 1800,
) -> None:
    """codex exec 를 실행해 report_path 에 리포트를 쓰고, JSONL 이벤트를 on_event 로 흘린다."""
    prompt = build_prompt(request_text)
    cmd = [
        *shlex.split(codex_cmd),
        "exec",
        prompt,
        "-C", str(converted_dir),
        "-s", "read-only",
        "--skip-git-repo-check",
        "--json",
        "-o", str(report_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"type": "raw", "text": line}
        on_event(event)

    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise CodexError(f"codex 시간 초과({timeout}s)") from exc

    if proc.returncode != 0:
        raise CodexError(stderr.strip() or f"codex 비정상 종료(코드 {proc.returncode})")
    if not report_path.exists():
        raise CodexError("codex 가 리포트 파일을 생성하지 못했습니다.")
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/test_codex_runner.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add web/codex_runner.py tests/test_codex_runner.py
git commit -m "feat: add codex_runner with prompt assembly and event streaming"
```

---

## Task 4: pipeline_runner — 기존 변환 파이프라인 래퍼

**Files:**
- Create: `web/pipeline_runner.py`
- Test: `tests/test_pipeline_runner.py`

`scripts/kordoc_pipeline.py` 를 import 해 `process_file` 을 호출하고, 변환된 문서 폴더 경로를 돌려준다. 진짜 kordoc 실행은 느리고 npx 의존이라 테스트에서는 `process_file` 을 몽키패치한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_pipeline_runner.py`:

```python
from pathlib import Path

import pytest

import web.pipeline_runner as pr
from web.pipeline_runner import convert, PipelineError


def test_convert_returns_doc_dir(tmp_path: Path, monkeypatch):
    upload = tmp_path / "요람.hwp"
    upload.write_bytes(b"dummy")
    converted_root = tmp_path / "converted"

    def fake_process_file(file_path, input_root, out_dir, pages):
        doc_id = pr._doc_id(file_path)
        doc_dir = Path(out_dir) / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.md").write_text("# 변환됨", encoding="utf-8")
        return [], []

    monkeypatch.setattr(pr.kordoc_pipeline, "process_file", fake_process_file)

    doc_dir = convert(upload, converted_root)
    assert (doc_dir / "document.md").read_text(encoding="utf-8") == "# 변환됨"


def test_convert_raises_when_no_markdown(tmp_path: Path, monkeypatch):
    upload = tmp_path / "a.hwp"
    upload.write_bytes(b"dummy")
    converted_root = tmp_path / "converted"

    def fake_process_file(file_path, input_root, out_dir, pages):
        # document.md 를 만들지 않음 → 변환 실패로 간주
        return [], []

    monkeypatch.setattr(pr.kordoc_pipeline, "process_file", fake_process_file)

    with pytest.raises(PipelineError):
        convert(upload, converted_root)
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_pipeline_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.pipeline_runner'`

- [ ] **Step 3: 구현**

`web/pipeline_runner.py`:

```python
"""기존 kordoc 변환 파이프라인(scripts/kordoc_pipeline.py) 래퍼."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import kordoc_pipeline  # noqa: E402


class PipelineError(RuntimeError):
    pass


def _doc_id(upload_path: Path) -> str:
    # safe_doc_id(path, root): root.is_file() 이면 path.stem 기반 id 를 만든다.
    return kordoc_pipeline.safe_doc_id(upload_path, upload_path)


def convert(upload_path: Path, converted_root: Path) -> Path:
    """업로드 파일을 변환하고 변환 결과 폴더(converted_root/<docid>)를 돌려준다."""
    upload_path = Path(upload_path)
    converted_root = Path(converted_root)
    converted_root.mkdir(parents=True, exist_ok=True)

    kordoc_pipeline.process_file(upload_path, upload_path, converted_root, None)

    doc_dir = converted_root / _doc_id(upload_path)
    if not (doc_dir / "document.md").exists():
        raise PipelineError(
            f"변환 실패: {upload_path.name} 에서 document.md 를 만들지 못했습니다."
        )
    return doc_dir
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/test_pipeline_runner.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add web/pipeline_runner.py tests/test_pipeline_runner.py
git commit -m "feat: add pipeline_runner wrapping kordoc_pipeline.process_file"
```

---

## Task 5: worker — 한 잡의 변환→분석 오케스트레이션

**Files:**
- Create: `web/worker.py`
- Test: `tests/test_worker.py`

한 잡을 받아 `converting → analyzing → done/failed` 상태머신을 돈다. convert/codex 함수를 인자로 주입받아(의존성 주입) 웹·codex·kordoc 없이 테스트한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_worker.py`:

```python
from pathlib import Path

from web.job_manager import JobManager, JobState
from web.worker import run_job


def test_run_job_success_flow(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="정리")

    def fake_convert(upload_path, converted_root):
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def fake_codex(converted_dir, request_text, report_path, on_event, **kwargs):
        on_event({"type": "item", "text": "분석 중"})
        Path(report_path).write_text("# 리포트", encoding="utf-8")

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=fake_codex)

    assert job.state == JobState.DONE
    assert job.report_path.read_text(encoding="utf-8") == "# 리포트"
    # 마지막 이벤트는 종료 센티넬
    drained = []
    while not job.events.empty():
        drained.append(job.events.get_nowait())
    assert drained[-1] == {"type": "end"}
    assert any(e.get("text") == "분석 중" for e in drained)


def test_run_job_marks_failed_on_convert_error(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="r")

    def boom_convert(upload_path, converted_root):
        raise RuntimeError("kordoc 폭발")

    def unused_codex(*a, **k):
        raise AssertionError("호출되면 안 됨")

    run_job(job, mgr, convert_fn=boom_convert, codex_fn=unused_codex)

    assert job.state == JobState.FAILED
    assert "변환 실패" in job.error
    assert "kordoc 폭발" in job.error


def test_run_job_marks_failed_on_codex_error(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="r")

    def fake_convert(upload_path, converted_root):
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def boom_codex(*a, **k):
        raise RuntimeError("codex 폭발")

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=boom_codex)

    assert job.state == JobState.FAILED
    assert "분석 실패" in job.error
    assert "codex 폭발" in job.error
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.worker'`

- [ ] **Step 3: 구현**

`web/worker.py`:

```python
"""한 잡의 변환→분석 오케스트레이션."""
from __future__ import annotations

from typing import Callable

from web.job_manager import Job, JobManager, JobState

ConvertFn = Callable[..., object]   # (upload_path, converted_root) -> doc_dir
CodexFn = Callable[..., None]       # (converted_dir, request_text, report_path, on_event, **kw)


def run_job(job: Job, manager: JobManager, convert_fn: ConvertFn, codex_fn: CodexFn) -> None:
    """블로킹 함수. 웹 레이어는 스레드풀에서 호출한다."""
    try:
        manager.set_state(job, JobState.CONVERTING, step="문서 변환 중")
        doc_dir = convert_fn(job.upload_path, job.converted_dir)
    except Exception as exc:  # noqa: BLE001 - 단계별 실패를 잡 상태로 기록
        manager.set_state(job, JobState.FAILED, error=f"변환 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.ANALYZING, step="codex 분석 중")
        codex_fn(
            converted_dir=doc_dir,
            request_text=job.request_text,
            report_path=job.report_path,
            on_event=lambda event: manager.push_event(job, event),
        )
    except Exception as exc:  # noqa: BLE001
        manager.set_state(job, JobState.FAILED, error=f"분석 실패: {exc}")
        job.events.put({"type": "end"})
        return

    manager.set_state(job, JobState.DONE, step="완료")
    job.events.put({"type": "end"})
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/test_worker.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add web/worker.py tests/test_worker.py
git commit -m "feat: add worker orchestrating convert and codex with state transitions"
```

---

## Task 6: FastAPI 앱 — 라우트 + SSE

**Files:**
- Create: `web/app.py`
- Test: `tests/test_app.py`

라우트: `POST /jobs`(업로드+요청 → 잡 생성, 백그라운드 워커 시작), `GET /jobs/{id}`(상태 JSON), `GET /jobs/{id}/report`(report.md), `GET /jobs/{id}/events`(SSE), `GET /`(정적 HTML). 테스트는 실제 convert/codex 를 가짜로 주입해 happy path 를 검증한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_app.py`:

```python
import time
from pathlib import Path

from fastapi.testclient import TestClient

import web.app as app_module
from web.job_manager import JobState


def _wait_state(client, job_id, target, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/jobs/{job_id}").json()["state"]
        if state in (target, "failed"):
            return state
        time.sleep(0.02)
    return client.get(f"/jobs/{job_id}").json()["state"]


def test_post_job_then_report_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")

    def fake_convert(upload_path, converted_root):
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def fake_codex(converted_dir, request_text, report_path, on_event, **kwargs):
        on_event({"type": "item", "text": "분석 중"})
        Path(report_path).write_text("# 분석 리포트\n결과", encoding="utf-8")

    monkeypatch.setattr(app_module, "convert", fake_convert)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    app_module.reset_manager()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files={"file": ("요람.hwp", b"dummy", "application/octet-stream")},
        data={"request_text": "정리해줘"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    assert _wait_state(client, job_id, "done") == "done"

    report = client.get(f"/jobs/{job_id}/report")
    assert report.status_code == 200
    assert "분석 리포트" in report.text


def test_get_unknown_job_returns_404(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    assert client.get("/jobs/nope").status_code == 404


def test_index_served(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "분석" in resp.text
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.app'`

- [ ] **Step 3: 구현**

`web/app.py`:

```python
"""FastAPI 앱: 업로드/상태/리포트/SSE 라우트 + 정적 HTML."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from web.codex_runner import run_codex
from web.job_manager import JobManager
from web.pipeline_runner import convert
from web.worker import run_job

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
JOBS_DIR = BASE_DIR.parent / "jobs"

app = FastAPI(title="Codex 분석 리포트")

_manager: JobManager | None = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager(base_dir=JOBS_DIR)
    return _manager


def reset_manager() -> None:
    """테스트에서 JOBS_DIR 변경 후 매니저를 다시 만들기 위한 훅."""
    global _manager
    _manager = None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/jobs")
async def create_job(file: UploadFile, request_text: str = Form(...)) -> dict:
    manager = get_manager()
    data = await file.read()
    job = manager.create(
        upload_filename=file.filename or "upload.bin",
        file_bytes=data,
        request_text=request_text,
    )

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None, run_job, job, manager, convert, run_codex
    )
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = get_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    return {"id": job.id, "state": job.state.value, "step": job.step, "error": job.error}


@app.get("/jobs/{job_id}/report", response_class=FileResponse)
def get_report(job_id: str) -> FileResponse:
    job = get_manager().get(job_id)
    if job is None or not job.report_path.exists():
        raise HTTPException(status_code=404, detail="리포트가 아직 없습니다.")
    return FileResponse(job.report_path, media_type="text/markdown; charset=utf-8")


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = get_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")

    async def event_stream():
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, job.events.get)
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "end":
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/test_app.py -q`
Expected: PASS (3 passed) — `index.html` 이 아직 없으면 `test_index_served` 만 실패하므로 Task 7 의 HTML 을 먼저 만들어도 된다. 순서상 여기서는 HTML 임시 파일을 만들지 말고 Task 7 직후 전체 재실행한다.

> 주의: `test_index_served` 는 `web/static/index.html` 을 요구한다. 이 테스트만 Task 7 완료 후 통과한다. 나머지 2개는 지금 통과해야 한다. 지금은 `python3 -m pytest tests/test_app.py -q -k "not index"` 로 확인:
> Run: `python3 -m pytest tests/test_app.py -q -k "not index"`
> Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_app.py
git commit -m "feat: add FastAPI routes with background worker and SSE"
```

---

## Task 7: 프론트엔드 — 업로드 + SSE 라이브 로그 + 리포트 렌더

**Files:**
- Create: `web/static/index.html`

의존성 없는 단일 HTML. `marked.js` CDN 으로 마크다운 렌더, `EventSource` 로 SSE 구독.

- [ ] **Step 1: index.html 작성**

`web/static/index.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>한글 문서 분석 리포트</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    body { font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; max-width: 880px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
    h1 { font-size: 1.4rem; }
    textarea { width: 100%; height: 5rem; box-sizing: border-box; }
    button { padding: 0.5rem 1rem; font-size: 1rem; cursor: pointer; }
    #log { background: #111; color: #9fe; font-family: monospace; font-size: 0.8rem; padding: 0.75rem; height: 9rem; overflow-y: auto; white-space: pre-wrap; border-radius: 6px; margin-top: 1rem; }
    #report { border-top: 2px solid #eee; margin-top: 1.5rem; padding-top: 1rem; }
    .status { font-weight: 600; margin-top: 0.5rem; }
    .err { color: #c00; }
    label { display:block; margin-top: 0.75rem; font-weight:600; }
  </style>
</head>
<body>
  <h1>한글 문서 분석 리포트</h1>
  <p>한글/엑셀 파일을 올리고, 원하는 분석을 적으면 리포트를 만들어 드립니다.</p>

  <form id="form">
    <label>문서 파일
      <input type="file" id="file" name="file" required />
    </label>
    <label>요청 내용
      <textarea id="request_text" placeholder="예: 학과별 입학정원 추이를 표로 정리해줘"></textarea>
    </label>
    <button type="submit">분석 시작</button>
  </form>

  <div class="status" id="status"></div>
  <div id="log" hidden></div>
  <div id="report"></div>

  <script>
    const form = document.getElementById('form');
    const statusEl = document.getElementById('status');
    const logEl = document.getElementById('log');
    const reportEl = document.getElementById('report');

    function appendLog(line) {
      logEl.hidden = false;
      logEl.textContent += line + '\n';
      logEl.scrollTop = logEl.scrollHeight;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      reportEl.innerHTML = '';
      logEl.textContent = '';
      statusEl.className = 'status';
      statusEl.textContent = '업로드 중…';

      const fd = new FormData();
      fd.append('file', document.getElementById('file').files[0]);
      fd.append('request_text', document.getElementById('request_text').value);

      const resp = await fetch('/jobs', { method: 'POST', body: fd });
      if (!resp.ok) { statusEl.textContent = '업로드 실패'; statusEl.className = 'status err'; return; }
      const { job_id } = await resp.json();

      const es = new EventSource(`/jobs/${job_id}/events`);
      es.onmessage = async (ev) => {
        const data = JSON.parse(ev.data);
        if (data.type === 'status') {
          statusEl.textContent = '상태: ' + data.state + (data.step ? ' — ' + data.step : '');
          if (data.state === 'failed') {
            statusEl.className = 'status err';
            statusEl.textContent = '실패: ' + (data.error || '알 수 없는 오류');
          }
        } else if (data.type === 'end') {
          es.close();
          await loadReport(job_id);
        } else {
          appendLog(data.text || JSON.stringify(data));
        }
      };
      es.onerror = () => { es.close(); };
    });

    async function loadReport(jobId) {
      const statusResp = await fetch(`/jobs/${jobId}`);
      const status = await statusResp.json();
      if (status.state !== 'done') return;
      const md = await (await fetch(`/jobs/${jobId}/report`)).text();
      reportEl.innerHTML = marked.parse(md);
      const dl = document.createElement('a');
      dl.href = `/jobs/${jobId}/report`;
      dl.textContent = '⬇ report.md 다운로드';
      dl.setAttribute('download', 'report.md');
      reportEl.prepend(dl);
    }
  </script>
</body>
</html>
```

- [ ] **Step 2: 전체 앱 테스트 통과 확인**

Run: `python3 -m pytest tests/test_app.py -q`
Expected: PASS (3 passed) — 이제 `test_index_served` 포함 전부 통과.

- [ ] **Step 3: Commit**

```bash
git add web/static/index.html
git commit -m "feat: add single-page frontend with SSE log and report render"
```

---

## Task 8: 로컬 수동 검증 (실제 codex + 소형 문서)

**Files:** 없음(검증 단계)

실제 `codex` 와 `kordoc` 으로 end-to-end 동작을 한 번 확인한다. 단위테스트는 전부 모킹이므로 이 단계가 실제 연동 검증이다.

- [ ] **Step 1: 전체 단위테스트 통과 확인**

Run: `python3 -m pytest -q`
Expected: PASS (전체 통과)

- [ ] **Step 2: 서버 기동**

Run: `python3 -m uvicorn web.app:app --reload --port 8000`
Expected: `Uvicorn running on http://127.0.0.1:8000`

- [ ] **Step 3: 브라우저 검증**

브라우저에서 `http://127.0.0.1:8000` 접속 → 소형 HWP/HWPX(또는 `2025년 요람…hwp`) 업로드 + "표 개수와 주요 표 제목을 정리해줘" 입력 → 분석 시작.
확인 항목:
- 상태가 `converting → analyzing → done` 으로 바뀐다.
- 로그 영역에 codex 진행 이벤트가 흐른다.
- 완료 후 리포트가 렌더되고 `report.md` 다운로드가 된다.
- `jobs/<id>/` 에 `converted/`, `report.md`, `codex_log.jsonl`, `status.json` 이 생긴다.

> codex 인증이 안 되어 있으면 `codex login` 선행 필요(터미널에서 `! codex login`). 변환이 느린 대형 파일은 `kordoc --pages` 동작과 무관하게 시간이 걸릴 수 있다.

- [ ] **Step 4: 검증 메모를 README 에 반영(다음 Task 에서)**

검증 중 발견한 실제 codex 이벤트 JSON 형태가 `data.text` 와 다르면, `index.html` 의 `appendLog` 분기와 `codex_log.jsonl` 표시를 실제 필드명에 맞춰 조정한다(예: codex 이벤트가 `{"type":"item","item":{...}}` 형태면 그 경로를 사용). 조정 시 별도 커밋.

---

## Task 9: Docker 파일 (배포 대비)

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`

이번 MVP 의 1차 검증은 로컬이지만, 배포 대비 컨테이너 정의를 함께 둔다. 빌드/배포 자체는 후속.

- [ ] **Step 1: .dockerignore 작성**

`.dockerignore`:

```txt
.git
jobs/
output/
output_test/
input_hwp/
*.hwp
*.hwpx
__pycache__/
*.pyc
.venv/
venv/
.ralph/
.DS_Store
docs/
```

- [ ] **Step 2: Dockerfile 작성**

`Dockerfile`:

```dockerfile
# Python + Node(npx kordoc 용). codex 는 별도 설치 또는 마운트.
FROM python:3.12-slim

# Node.js (kordoc 을 npx 로 실행)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts
COPY web ./web

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 주의: `codex` CLI 는 이 이미지에 포함하지 않는다(설치 방식·인증이 환경마다 다름). compose 에서 호스트 codex 바이너리와 `~/.codex` 인증을 마운트하거나, 후속에 이미지에 설치 단계를 추가한다.

- [ ] **Step 3: docker-compose.yml 작성**

`docker-compose.yml`:

```yaml
services:
  report-bot:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./jobs:/app/jobs                       # 산출물 영속
      - ${HOME}/.codex:/root/.codex:ro         # codex 인증(후속: 이미지에 codex 설치 시)
    environment:
      - CODEX_HOME=/root/.codex
    restart: unless-stopped
```

- [ ] **Step 4: 빌드 가능성만 확인(선택, 네트워크 필요)**

Run: `docker build -t report-bot . || echo "도커 빌드는 후속 단계에서 검증"`
Expected: 빌드 성공 또는 후속 메모. (오프라인이면 스킵)

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "chore: add Dockerfile and compose for later deployment"
```

---

## Task 10: README 갱신

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 웹앱 실행 섹션 추가**

`README.md` 끝에 아래 섹션을 추가:

```markdown
## 웹앱 (업로드 → Codex 분석 리포트)

담당자가 문서를 업로드하고 요청을 적으면 codex 가 분석 리포트를 만들어 보여줍니다.

준비:

```bash
python3 -m pip install -r requirements.txt   # fastapi, uvicorn, python-multipart
codex login                                  # codex 인증(최초 1회)
```

실행:

```bash
python3 -m uvicorn web.app:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000` 접속 → 파일 업로드 + 요청 입력 → 분석 시작.
산출물은 `jobs/<job_id>/`(converted/, report.md, codex_log.jsonl, status.json)에 저장됩니다.

테스트:

```bash
python3 -m pytest -q
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add web app usage to README"
```

---

## Self-Review (작성자 점검 완료)

- **Spec 커버리지:** 업로드/요청(Task 6,7) · 잡 기반 비동기 A(Task 2,5,6) · SSE 라이브 C(Task 6,7) · codex 연동(Task 3) · 파이프라인 재사용(Task 4) · 리포트 렌더/다운로드(Task 7) · 에러 단계 구분(Task 5) · 테스트 전략(Task 2~6) · Docker(Task 9) — 모두 대응됨. 엑셀/한글 생성은 spec 의 제외 범위라 미포함(정상).
- **플레이스홀더:** 모든 코드 스텝에 완전한 코드 포함, TBD 없음. Task 8 Step 4 의 "실제 이벤트 필드 조정"은 실측 기반 조정 지시로, 플레이스홀더가 아니라 검증 후속 작업.
- **타입/시그니처 일관성:** `JobManager.create/get/set_state/push_event`, `Job.converted_dir/report_path/log_path`, `run_codex(converted_dir, request_text, report_path, on_event, codex_cmd)`, `convert(upload_path, converted_root)`, `run_job(job, manager, convert_fn, codex_fn)`, `app` 의 `JOBS_DIR/convert/run_codex/reset_manager` — Task 간 이름·인자 일치 확인.
- **알려진 위험:** codex `--json` 이벤트의 실제 필드명은 환경에서 확인 필요(Task 8 에서 실측 후 `index.html` 조정). 단위테스트는 가짜 codex 라 이 위험을 커버하지 못함 — Task 8 수동 검증이 필수.
```
