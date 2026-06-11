from pathlib import Path

from web.job_manager import JobManager, JobState
from web.worker import run_job


def test_run_job_success_flow(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="정리")

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
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")

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
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")

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
