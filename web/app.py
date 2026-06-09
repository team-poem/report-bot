"""FastAPI 앱: 업로드/상태/리포트/SSE 라우트 + 정적 HTML."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from web.codex_runner import run_codex
from web.job_manager import JobManager
from web.job_runner import JobRunner
from web.pipeline_runner import convert
from web.report_renderer import render_html
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


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/jobs")
async def create_job(file: UploadFile, request_text: str = Form(...)) -> dict:
    manager = get_manager()
    data = await file.read()
    # 클라이언트가 보낸 파일명은 신뢰하지 않는다(경로 탈출 방지): 마지막 경로 요소만 사용.
    safe_name = Path(file.filename or "upload.bin").name or "upload.bin"
    job = manager.create(
        upload_filename=safe_name,
        file_bytes=data,
        request_text=request_text,
    )

    get_runner().submit(run_job, job, manager, convert, run_codex)
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = get_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    return {"id": job.id, "state": job.state.value, "step": job.step, "error": job.error}


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
def get_report(job_id: str) -> HTMLResponse:
    job = get_manager().get(job_id)
    if job is None or not job.report_path.exists():
        raise HTTPException(status_code=404, detail="리포트가 아직 없습니다.")
    md_text = job.report_path.read_text(encoding="utf-8")
    return HTMLResponse(render_html(md_text))


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
