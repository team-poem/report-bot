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


# ---------------------------------------------------------------------------
# Module-level fakes shared across tests
# ---------------------------------------------------------------------------

def fake_convert_many(upload_paths, converted_root):
    doc_dir = Path(converted_root) / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    return [doc_dir]


def fake_codex(converted_dir, request_text, report_path, output_type, on_event, **kwargs):
    on_event({"type": "item", "text": "분석 중"})
    Path(report_path).write_text("# 분석 리포트\n결과", encoding="utf-8")


def fake_hwpx(report_path, result_path):
    Path(result_path).write_bytes(b"PK-fake-hwpx")


# ---------------------------------------------------------------------------
# Existing tests (updated)
# ---------------------------------------------------------------------------

def test_post_job_then_report_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("요람.hwp", b"dummy", "application/octet-stream"))],
        data={"request_text": "정리해줘", "output_type": "report"},
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
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("../../evil.hwp", b"dummy", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "report"},
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


def test_concurrency_limit_one_still_completes_all_jobs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setenv("REPORT_BOT_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()  # env=1 을 반영한 새 runner

    client = TestClient(app_module.app)
    try:
        ids = []
        for _ in range(2):
            resp = client.post(
                "/jobs",
                files=[("files", ("a.hwp", b"dummy", "application/octet-stream"))],
                data={"request_text": "정리", "output_type": "report"},
            )
            assert resp.status_code == 200
            ids.append(resp.json()["job_id"])

        # 동시성 1이라 직렬 처리되더라도 두 잡 모두 결국 done 에 도달해야 한다
        for job_id in ids:
            assert _wait_state(client, job_id, "done") == "done"
    finally:
        app_module.reset_runner()  # 다음 테스트를 위해 기본 동시성 runner 로 되돌림


# ---------------------------------------------------------------------------
# New tests
# ---------------------------------------------------------------------------

def test_post_multiple_files_all_saved(tmp_path: Path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(app_module, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("files", ("b.xlsx", b"2", "application/octet-stream")),
        ],
        data={"request_text": "취합", "output_type": "merge"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    upload_dir = jobs_dir / job_id / "upload"
    assert (upload_dir / "a.hwp").exists() and (upload_dir / "b.xlsx").exists()


def test_duplicate_filenames_are_suffixed(tmp_path: Path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(app_module, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("같은이름.hwp", b"1", "application/octet-stream")),
            ("files", ("같은이름.hwp", b"2", "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 200
    upload_dir = jobs_dir / resp.json()["job_id"] / "upload"
    assert (upload_dir / "같은이름.hwp").exists()
    assert (upload_dir / "같은이름_2.hwp").exists()


def test_invalid_output_type_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("a.hwp", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "pptx"},
    )
    assert resp.status_code == 400


def test_unsupported_extension_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("virus.exe", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 400
    assert "virus.exe" in resp.json()["detail"]


def test_hwpx_download_after_done(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("a.hwp", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "report"},
    )
    job_id = resp.json()["job_id"]
    assert _wait_state(client, job_id, "done") == "done"

    dl = client.get(f"/jobs/{job_id}/hwpx")
    assert dl.status_code == 200
    assert dl.content == b"PK-fake-hwpx"
    assert ".hwpx" in dl.headers["content-disposition"]


def test_hwpx_404_before_generated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    assert client.get("/jobs/nope/hwpx").status_code == 404


def _make_template_bytes() -> bytes:
    from web.hwpx_writer import package_hwpx, render_blocks
    return package_hwpx(render_blocks("{{본문}}\n"))


def test_template_upload_accepted_and_saved(tmp_path: Path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(app_module, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("template", ("양식.hwpx", _make_template_bytes(), "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert (jobs_dir / job_id / "template" / "양식.hwpx").exists()


def test_template_without_markers_rejected_400(tmp_path: Path, monkeypatch):
    from web.hwpx_writer import package_hwpx, render_blocks

    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    no_marker = package_hwpx(render_blocks("마커 없음\n"))
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("template", ("양식.hwpx", no_marker, "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 400
    assert "자리표시자" in resp.json()["detail"]


def test_template_wrong_extension_rejected_400(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("template", ("양식.hwp", b"PK", "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 400
    assert ".hwpx" in resp.json()["detail"]


def test_template_not_a_zip_rejected_400(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("template", ("양식.hwpx", b"not-a-zip", "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 400


def test_template_filename_traversal_sanitized(tmp_path: Path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(app_module, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[
            ("files", ("a.hwp", b"1", "application/octet-stream")),
            ("template", ("../../evil.hwpx", _make_template_bytes(), "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert (jobs_dir / job_id / "template" / "evil.hwpx").exists()
    assert not (tmp_path / "evil.hwpx").exists()
