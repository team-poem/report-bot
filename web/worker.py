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
