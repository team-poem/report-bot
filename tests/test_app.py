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


def test_upload_filename_is_sanitized_against_traversal(tmp_path: Path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(app_module, "JOBS_DIR", jobs_dir)
    # 백그라운드 변환/분석은 무력화(파일 저장 검증만 목적)
    monkeypatch.setattr(app_module, "convert", lambda *a, **k: tmp_path / "noop")
    monkeypatch.setattr(app_module, "run_codex", lambda *a, **k: None)
    app_module.reset_manager()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files={"file": ("../../evil.hwp", b"dummy", "application/octet-stream")},
        data={"request_text": "x"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # 정제된 파일명으로 잡 폴더 안에 저장되고, 상위로 탈출하지 않아야 한다.
    assert (jobs_dir / job_id / "upload" / "evil.hwp").exists()
    assert not (tmp_path / "evil.hwp").exists()


def test_index_served(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "분석" in resp.text
