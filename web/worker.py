"""한 잡의 변환→분석→HWPX 생성 오케스트레이션."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from web.job_manager import Job, JobManager, JobState

ConvertFn = Callable[..., object]   # (upload_paths, converted_root) -> list[doc_dir]
CodexFn = Callable[..., None]       # (converted_dir, request_text, report_path, output_type, on_event, template_md=, slots=, **kw)
HwpxFn = Callable[..., None]        # (report_path, result_path) -> None


@dataclass
class TemplateFns:
    """양식 잡에 필요한 함수 묶음 (테스트에서 페이크로 대체)."""
    scan: Callable[..., list]          # (template_path) -> list[Slot]
    parse_slots: Callable[..., dict]   # (report_md_text, expected_ids) -> {id: md}
    fill: Callable[..., None]          # (template_path, slot_contents, out_path) -> None


def run_job(
    job: Job,
    manager: JobManager,
    convert_fn: ConvertFn,
    codex_fn: CodexFn,
    hwpx_fn: HwpxFn,
    template_fns: TemplateFns | None = None,
) -> None:
    """블로킹 함수. 웹 레이어는 스레드풀에서 호출한다."""
    use_template = job.template_path is not None and template_fns is not None
    slots = None
    template_md = ""

    try:
        manager.set_state(job, JobState.CONVERTING, step="문서 변환 중")
        convert_fn(job.upload_paths, job.converted_dir)
        if use_template:
            slots = template_fns.scan(job.template_path)
            [tdir] = convert_fn([job.template_path], job.dir / "template_converted")
            template_md = (Path(tdir) / "document.md").read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 단계별 실패를 잡 상태로 기록
        manager.set_state(job, JobState.FAILED, error=f"변환 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.ANALYZING, step="codex 분석 중")
        codex_fn(
            converted_dir=job.converted_dir,   # 루트: 그 아래 docid 폴더 N개
            request_text=job.request_text,
            report_path=job.report_path,
            output_type=job.output_type,
            on_event=lambda event: manager.push_event(job, event),
            template_md=template_md or None,
            slots=slots,
        )
        if use_template:
            report_md = job.report_path.read_text(encoding="utf-8")
            slot_contents = template_fns.parse_slots(report_md, [s.id for s in slots])
    except Exception as exc:  # noqa: BLE001
        manager.set_state(job, JobState.FAILED, error=f"분석 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.GENERATING, step="한글 파일 생성 중")
        if use_template:
            template_fns.fill(job.template_path, slot_contents, job.result_path)
        else:
            hwpx_fn(job.report_path, job.result_path)
    except Exception as exc:  # noqa: BLE001
        manager.set_state(
            job,
            JobState.FAILED,
            error=f"한글 파일 생성 실패: {exc} — 리포트 내용은 화면에서 확인할 수 있습니다.",
        )
        job.events.put({"type": "end"})
        return

    manager.set_state(job, JobState.DONE, step="완료")
    job.events.put({"type": "end"})
