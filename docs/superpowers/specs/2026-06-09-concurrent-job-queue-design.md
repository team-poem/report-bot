# 동시실행 잡 큐 — 설계 (Design Spec)

작성일: 2026-06-09
상태: 승인 대기 (검토 후 구현 계획 단계로)

## 1. 목적과 배경

담당자 여러 명이 각자 문서를 업로드하면 변환(kordoc) + 분석(codex) 잡이 **동시에** 들어온다.
현재 구현은 동시 실행 상한이 없어, 동시에 여러 명이 올리면 codex 프로세스가 한꺼번에 떠서
RAM·codex API rate limit·CPU가 위험해질 수 있다.

이 작업은 **소규모(동시 1~5명)** 를 대상으로, n8n·Redis·컨테이너 분리 없이 **현재 단일
FastAPI 컨테이너 안에서** "동시 N개까지만 실행하고 나머지는 대기시키는 바운디드 잡 큐"를
추가한다. (확장 경로는 `docs/architecture/scaling-roadmap.md`의 Tier 2/3 참고. 이번은 Tier 1.)

## 2. 현재 상태

`web/app.py`의 `create_job`은 잡을 만든 뒤 다음과 같이 백그라운드 실행한다:

```python
loop = asyncio.get_running_loop()
loop.run_in_executor(None, run_job, job, manager, convert, run_codex)
```

문제점:
- `None`(기본 executor)에 fire-and-forget → **동시 실행 상한 없음.** 10명이 올리면 잡 10개가
  동시에 시작되어 codex 프로세스 10개가 뜬다.
- 기본 executor를 **SSE 드레이닝(`job.events.get`)과 공유** → 장시간 잡이 풀 스레드를 점유하면
  SSE와 경합.

순수 모듈(`pipeline_runner.convert`, `codex_runner.run_codex`, `worker.run_job`,
`job_manager`)은 그대로 둔다. 바꾸는 것은 **dispatch 계층뿐**이다.

## 3. 범위

포함:
- 잡 전용 바운디드 실행기(`JobRunner`, 내부 `ThreadPoolExecutor(max_workers=N)`).
- `create_job`이 이 실행기에 `run_job`을 submit. N 초과 잡은 **queued 상태로 대기** 후
  슬롯이 나면 자동 시작(ThreadPoolExecutor의 FIFO 큐잉 활용).
- 동시성 N을 환경변수 `REPORT_BOT_MAX_CONCURRENCY`로 설정(기본 3).
- 프론트: 제출 직후 "대기 중" 표시(첫 SSE 이벤트가 오면 실제 상태로 갱신).
- 앱 종료 시 실행기 정리(shutdown 핸들러).
- compose/README에 `REPORT_BOT_MAX_CONCURRENCY` 문서화.

제외(이번 아님):
- Redis/외부 큐, web/worker 컨테이너 분리, 잡 영속(재시작 보존), 우선순위/재시도.
  → scaling-roadmap Tier 2/3.
- 대기열 순번을 화면에 숫자로 노출하는 것(YAGNI; 상태 "대기 중"만 표시).

## 4. 아키텍처 / 컴포넌트

```
POST /jobs ─► JobManager.create(job)            (state=queued, jobs/<id>/ 기록)
           └► JobRunner.submit(run_job, job, manager, convert, run_codex)
                         │
                         ▼
            ThreadPoolExecutor(max_workers=N)    ← 동시 N개만 실행, 초과분 FIFO 대기
                         │  (슬롯 확보 시 시작)
                         ▼
            worker.run_job → convert → run_codex → done/failed
                         │  (이벤트는 job.events 큐로)
                         ▼
            SSE /events (기본 executor에서 드레이닝, 잡 풀과 분리됨)
```

### 새 단위: `web/job_runner.py`

`ThreadPoolExecutor`를 얇게 감싸 (a) 동시성 상한 설정 지점, (b) 테스트용 정리 훅,
(c) Tier 2에서 큐로 교체할 seam 을 제공한다.

```python
"""잡 전용 바운디드 실행기 — 동시 N개까지만 실행, 초과분은 대기."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class JobRunner:
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

> 동작 핵심: `ThreadPoolExecutor`는 `max_workers`를 초과해 submit된 작업을 내부 큐에
> FIFO로 쌓아두고 워커가 비면 꺼내 실행한다. 따라서 "N개 동시 + 나머지 대기"가 별도
> 큐 구현 없이 보장된다. 대기 중인 잡은 `run_job`이 아직 시작 안 됐으므로 상태가 `queued`로
> 남는다(슬롯 확보 시 `converting`으로 전이).

## 5. `web/app.py` 변경

- 모듈 상수/헬퍼:
  - `import os` 추가.
  - `def _max_concurrency() -> int:` → `int(os.environ.get("REPORT_BOT_MAX_CONCURRENCY", "3"))`,
    파싱 실패나 <1 이면 3으로 폴백.
  - `get_manager()`와 동일 패턴으로 지연 생성되는 `_runner` + `get_runner()` + `reset_runner()`.
    `get_runner()`는 `JobRunner(max_workers=_max_concurrency())` 생성.
    `reset_runner()`는 테스트 간 스레드 누수를 막기 위해 기존 `_runner`가 있으면
    `shutdown(wait=False)` 후 `None`으로 비운다.
- `create_job`: 기존 `loop.run_in_executor(None, run_job, ...)` 두 줄을
  `get_runner().submit(run_job, job, manager, convert, run_codex)` 로 교체.
  (`convert`/`run_codex`는 여전히 모듈 전역 참조 → 테스트 몽키패치 유지.)
- 종료 정리: `@app.on_event("shutdown")` 핸들러에서 `if _runner: _runner.shutdown(wait=False)`.
- SSE 엔드포인트는 변경 없음(잡이 전용 풀로 빠져 기본 풀과 더는 경합하지 않음).

## 6. 프론트(`web/static/index.html`) 변경

- 제출 후(`/jobs` 응답 수신 직후) 상태를 **"대기 중 — 분석 차례를 기다리는 중…"** 으로 표시.
  첫 `status` SSE 이벤트(`converting`)가 오면 기존 로직이 실제 상태로 덮어쓴다.
- 그 외 동작(SSE 구독, iframe 리포트 렌더) 불변.

## 7. 데이터 흐름 / 상태

- 잡 생성 시 `queued`. 실행기 슬롯이 있으면 즉시 `run_job` 시작 → `converting`.
  슬롯이 없으면 `queued`로 머물며 실행기 내부 큐에서 대기(상태 변화 없음).
- `GET /jobs/{id}`는 대기 중이면 `queued` 반환. SSE는 잡이 시작되면 이벤트가 흐른다.
- 동시 실행 잡 수 ≤ N → 동시 codex 프로세스 수 ≤ N → rate limit·RAM 통제.

## 8. 에러 처리

- 동시성 동작은 `run_job`의 기존 단계별 실패 처리(변환/분석 실패 → `failed` + end 센티넬)를
  그대로 사용. 한 잡의 실패가 다른 잡·실행기에 영향 없음.
- `REPORT_BOT_MAX_CONCURRENCY` 파싱 실패 → 기본 3으로 폴백(서버 기동 실패 금지).

## 9. 테스트 전략

`tests/test_job_runner.py`:
- **동시성 상한:** `JobRunner(max_workers=1)`로 두 작업 제출. 작업1은 `threading.Event`에서
  블록. 작업1이 도는 동안 작업2가 **시작되지 않음**을 확인(작업2의 "시작됨" 플래그가 False).
  작업1 해제 후 작업2가 실행되고 두 future 모두 완료됨을 확인.
- **여러 워커:** `max_workers=2`로 두 블로킹 작업이 **동시에** 시작됨을 확인(둘 다 시작 플래그
  True가 될 때까지 짧은 타임아웃 내 도달).
- `max_workers=0` 방어 → 1로 보정되어 동작.

`tests/test_app.py`:
- 기존 happy-path를 `reset_runner()` 호출 포함하도록 보강(잡이 전용 실행기로도 정상 완료).
- (선택) `REPORT_BOT_MAX_CONCURRENCY` 환경변수를 monkeypatch로 1로 설정 후 두 잡 제출 →
  둘 다 최종 `done` 도달(직렬 처리되어도 완료) 확인. 가짜 convert/codex는 즉시 완료형.

기존 순수 모듈 테스트(job_manager/codex_runner/pipeline_runner/worker/report_renderer)는 변경 없음.

## 10. 배포 메모

- `docker-compose.yml`의 `report-bot.environment`에 `REPORT_BOT_MAX_CONCURRENCY=3` 추가.
- README 웹앱 섹션에 환경변수 한 줄 설명 추가("동시 실행 잡 수, 기본 3").
- codex CLI를 이미지에 설치하는 실제 배포는 여전히 후속(scaling-roadmap 참고).

## 11. 결정 기록

- 엔진: **인프로세스 바운디드 ThreadPoolExecutor**(A안). n8n·Redis·컨테이너 분리는
  소규모에 불필요 → scaling-roadmap Tier 2/3로 미룸.
- 동시성 기본값 **N=3**, env로 조절.
- 대기열 순번 숫자 노출은 하지 않음(YAGNI). 상태 "대기 중"만 표시.
- 순수 모듈 인터페이스 불변 — dispatch 계층(`run_in_executor` → `JobRunner.submit`)만 교체.
