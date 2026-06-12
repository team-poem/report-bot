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


def test_template_job_runs_scan_parse_fill(tmp_path: Path):
    from web.job_manager import JobManager, JobState
    from web.worker import TemplateFns, run_job

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(
        uploads=[("a.hwp", b"x")], request_text="갱신",
        output_type="report", template=("t.hwpx", b"PK"),
    )

    class FakeSlot:
        def __init__(self, sid):
            self.id, self.kind, self.instruction, self.original_text = sid, "본문", "", ""

    calls = {}

    def fake_convert_t(upload_paths, converted_root):
        # 원본 변환과 양식 변환 두 번 불린다
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.md").write_text("# 양식 md", encoding="utf-8")
        return [doc_dir]

    def spy_codex(converted_dir, request_text, report_path, output_type, on_event,
                  template_md=None, slots=None, **kw):
        calls["template_md"] = template_md
        calls["slot_ids"] = [s.id for s in (slots or [])]
        Path(report_path).write_text("===SLOT: 본문-1===\n내용\n===END===\n", encoding="utf-8")

    fns = TemplateFns(
        scan=lambda path: [FakeSlot("본문-1")],
        parse_slots=lambda md, ids: calls.setdefault("parsed", {"본문-1": "내용"}),
        fill=lambda tpl, contents, out: Path(out).write_bytes(b"filled-hwpx"),
    )

    run_job(job, mgr, convert_fn=fake_convert_t, codex_fn=spy_codex,
            hwpx_fn=fake_hwpx, template_fns=fns)

    assert job.state == JobState.DONE
    assert calls["template_md"] == "# 양식 md"
    assert calls["slot_ids"] == ["본문-1"]
    assert job.result_path.read_bytes() == b"filled-hwpx"   # fill 경로 사용, write_hwpx 아님


def test_template_job_missing_slot_fails_at_analysis(tmp_path: Path):
    from web.job_manager import JobManager, JobState
    from web.worker import TemplateFns, run_job

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r",
                     output_type="report", template=("t.hwpx", b"PK"))

    class FakeSlot:
        def __init__(self, sid):
            self.id, self.kind, self.instruction, self.original_text = sid, "본문", "", ""

    def fake_convert_t(upload_paths, converted_root):
        doc_dir = Path(converted_root) / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.md").write_text("md", encoding="utf-8")
        return [doc_dir]

    def boom_parse(md, ids):
        raise RuntimeError("채워지지 않은 슬롯: 본문-1")

    def unused(*a, **k):
        raise AssertionError("호출되면 안 됨")

    fns = TemplateFns(scan=lambda p: [FakeSlot("본문-1")], parse_slots=boom_parse, fill=unused)

    run_job(job, mgr, convert_fn=fake_convert_t, codex_fn=fake_codex,
            hwpx_fn=unused, template_fns=fns)
    assert job.state == JobState.FAILED
    assert "분석 실패" in job.error and "본문-1" in job.error


def test_non_template_job_ignores_template_fns(tmp_path: Path):
    from web.job_manager import JobState
    from web.worker import TemplateFns, run_job

    mgr, job = _mgr_job(tmp_path)

    def unused(*a, **k):
        raise AssertionError("호출되면 안 됨")

    fns = TemplateFns(scan=unused, parse_slots=unused, fill=unused)
    run_job(job, mgr, convert_fn=fake_convert, codex_fn=fake_codex,
            hwpx_fn=fake_hwpx, template_fns=fns)
    assert job.state == JobState.DONE
    assert job.result_path.read_bytes() == b"hwpx-bytes"
