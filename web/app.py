"""FastAPI 앱: 업로드/상태/리포트/SSE 라우트 + 정적 HTML."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from web.codex_runner import run_codex
from web.hwpx_writer import write_hwpx
from web.job_manager import JobManager
from web.job_runner import JobRunner
from web.pipeline_runner import SUPPORTED_EXTENSIONS, convert_many
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
async def create_job(
    files: list[UploadFile] = File(...),
    request_text: str = Form(...),
    output_type: str = Form("report"),
) -> dict:
    if output_type not in ("report", "merge"):
        raise HTTPException(status_code=400, detail="output_type 은 report 또는 merge 여야 합니다.")
    if not files:
        raise HTTPException(status_code=400, detail="파일을 1개 이상 올려 주세요.")

    uploads: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for f in files:
        # 클라이언트가 보낸 파일명은 신뢰하지 않는다(경로 탈출 방지): 마지막 경로 요소만 사용.
        safe_name = Path(f.filename or "upload.bin").name or "upload.bin"
        if Path(safe_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"지원하지 않는 파일 형식입니다: {safe_name}",
            )
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        n = 2
        while safe_name in seen:
            safe_name = f"{stem}_{n}{suffix}"
            n += 1
        seen.add(safe_name)
        uploads.append((safe_name, await f.read()))

    job = get_manager().create(
        uploads=uploads, request_text=request_text, output_type=output_type
    )
    get_runner().submit(run_job, job, get_manager(), convert_many, run_codex, write_hwpx)
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


@app.get("/jobs/{job_id}/hwpx")
def get_hwpx(job_id: str) -> FileResponse:
    job = get_manager().get(job_id)
    if job is None or not job.result_path.exists():
        raise HTTPException(status_code=404, detail="한글 파일이 아직 없습니다.")
    filename = "취합문서.hwpx" if job.output_type == "merge" else "분석리포트.hwpx"
    return FileResponse(job.result_path, filename=filename, media_type="application/hwp+zip")


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
