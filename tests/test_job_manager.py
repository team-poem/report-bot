import json
from pathlib import Path

from web.job_manager import JobManager, JobState


def test_create_writes_upload_request_and_status(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("요람.hwp", b"hello")], request_text="학과별 정원 정리")

    assert job.state == JobState.QUEUED
    assert job.upload_paths[0].read_bytes() == b"hello"
    assert (job.dir / "request.txt").read_text(encoding="utf-8") == "학과별 정원 정리"

    status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "queued"
    assert mgr.get(job.id) is job


def test_set_state_persists_and_emits_event(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")

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
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")

    mgr.set_state(job, JobState.FAILED, error="변환 실패: kordoc 오류")

    status = json.loads((job.dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error"] == "변환 실패: kordoc 오류"


def test_push_event_appends_to_log_and_queue(tmp_path: Path):
    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")

    mgr.push_event(job, {"type": "codex", "msg": "reading document.md"})

    line = (job.dir / "codex_log.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line) == {"type": "codex", "msg": "reading document.md"}
    assert job.events.get_nowait() == {"type": "codex", "msg": "reading document.md"}


def test_create_with_multiple_uploads_and_output_type(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(
        uploads=[("a.hwp", b"x"), ("b.xlsx", b"y")],
        request_text="취합해줘",
        output_type="merge",
    )
    assert [p.name for p in job.upload_paths] == ["a.hwp", "b.xlsx"]
    assert all(p.read_bytes() in (b"x", b"y") for p in job.upload_paths)
    assert job.output_type == "merge"
    assert job.result_path == job.dir / "result.hwpx"


def test_generating_state_exists():
    from web.job_manager import JobState
    assert JobState.GENERATING.value == "generating"


def test_create_with_template_saves_file(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(
        uploads=[("a.hwp", b"x")],
        request_text="r",
        output_type="report",
        template=("양식.hwpx", b"PK-bytes"),
    )
    assert job.template_path is not None
    assert job.template_path.read_bytes() == b"PK-bytes"
    assert job.template_path.parent == job.dir / "template"


def test_create_without_template_defaults_none(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")
    assert job.template_path is None
