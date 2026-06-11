from pathlib import Path

from web.job_manager import JobManager, JobState
from web.worker import run_job


def _mgr_job(tmp_path, output_type="report"):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="정리", output_type=output_type)
    return mgr, job


def fake_convert(upload_paths, converted_root):
    doc_dir = Path(converted_root) / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    return [doc_dir]


def fake_codex(converted_dir, request_text, report_path, output_type, on_event, **kwargs):
    on_event({"type": "item", "text": "분석 중"})
    Path(report_path).write_text("# 리포트", encoding="utf-8")


def fake_hwpx(report_path, result_path):
    Path(result_path).write_bytes(b"hwpx-bytes")


def test_run_job_success_flow_reaches_done_with_result(tmp_path: Path):
    mgr, job = _mgr_job(tmp_path)
    run_job(job, mgr, convert_fn=fake_convert, codex_fn=fake_codex, hwpx_fn=fake_hwpx)

    assert job.state == JobState.DONE
    assert job.report_path.read_text(encoding="utf-8") == "# 리포트"
    assert job.result_path.read_bytes() == b"hwpx-bytes"
    drained = []
    while not job.events.empty():
        drained.append(job.events.get_nowait())
    assert drained[-1] == {"type": "end"}
    # generating 상태가 SSE 로 중계되었는지
    assert any(e.get("state") == "generating" for e in drained if e.get("type") == "status")


def test_codex_receives_converted_root_and_output_type(tmp_path: Path):
    mgr, job = _mgr_job(tmp_path, output_type="merge")
    seen = {}

    def spy_codex(converted_dir, request_text, report_path, output_type, on_event, **kw):
        seen["converted_dir"] = Path(converted_dir)
        seen["output_type"] = output_type
        Path(report_path).write_text("# r", encoding="utf-8")

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=spy_codex, hwpx_fn=fake_hwpx)
    assert seen["converted_dir"] == job.converted_dir  # 루트(여러 docid 의 부모)
    assert seen["output_type"] == "merge"


def test_run_job_marks_failed_on_convert_error(tmp_path: Path):
    mgr, job = _mgr_job(tmp_path)

    def boom_convert(upload_paths, converted_root):
        raise RuntimeError("kordoc 폭발")

    def unused(*a, **k):
        raise AssertionError("호출되면 안 됨")

    run_job(job, mgr, convert_fn=boom_convert, codex_fn=unused, hwpx_fn=unused)
    assert job.state == JobState.FAILED
    assert "변환 실패" in job.error and "kordoc 폭발" in job.error


def test_run_job_marks_failed_on_codex_error(tmp_path: Path):
    mgr, job = _mgr_job(tmp_path)

    def boom_codex(*a, **k):
        raise RuntimeError("codex 폭발")

    def unused(*a, **k):
        raise AssertionError("호출되면 안 됨")

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=boom_codex, hwpx_fn=unused)
    assert job.state == JobState.FAILED
    assert "분석 실패" in job.error and "codex 폭발" in job.error


def test_hwpx_failure_keeps_report_for_preview(tmp_path: Path):
    mgr, job = _mgr_job(tmp_path)

    def boom_hwpx(report_path, result_path):
        raise RuntimeError("XML 깨짐")

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=fake_codex, hwpx_fn=boom_hwpx)
    assert job.state == JobState.FAILED
    assert "한글 파일 생성 실패" in job.error
    assert "화면에서 확인" in job.error          # 폴백 안내 문구
    assert job.report_path.exists()              # HTML 미리보기는 살아 있음
