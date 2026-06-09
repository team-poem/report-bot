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
