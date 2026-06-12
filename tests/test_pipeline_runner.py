from pathlib import Path

import pytest

import web.pipeline_runner as pr
from web.pipeline_runner import convert, PipelineError


def test_convert_returns_doc_dir(tmp_path: Path, monkeypatch):
    upload = tmp_path / "요람.hwp"
    upload.write_bytes(b"dummy")
    converted_root = tmp_path / "converted"

    def fake_process_file(file_path, input_root, out_dir, pages):
        doc_id = pr._doc_id(file_path)
        doc_dir = Path(out_dir) / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.md").write_text("# 변환됨", encoding="utf-8")
        return [], []

    monkeypatch.setattr(pr.kordoc_pipeline, "process_file", fake_process_file)

    doc_dir = convert(upload, converted_root)
    assert (doc_dir / "document.md").read_text(encoding="utf-8") == "# 변환됨"


def test_convert_raises_when_no_markdown(tmp_path: Path, monkeypatch):
    upload = tmp_path / "a.hwp"
    upload.write_bytes(b"dummy")
    converted_root = tmp_path / "converted"

    def fake_process_file(file_path, input_root, out_dir, pages):
        # document.md 를 만들지 않음 → 변환 실패로 간주
        return [], []

    monkeypatch.setattr(pr.kordoc_pipeline, "process_file", fake_process_file)

    with pytest.raises(PipelineError):
        convert(upload, converted_root)


def test_convert_many_converts_each_and_returns_doc_dirs(tmp_path, monkeypatch):
    import web.pipeline_runner as pr

    calls = []

    def fake_convert(upload_path, converted_root):
        calls.append(upload_path)
        doc_dir = Path(converted_root) / Path(upload_path).stem
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    monkeypatch.setattr(pr, "convert", fake_convert)

    a, b = tmp_path / "a.hwp", tmp_path / "b.hwp"
    a.write_bytes(b"x"); b.write_bytes(b"y")
    doc_dirs = pr.convert_many([a, b], tmp_path / "converted")

    assert calls == [a, b]
    assert [d.name for d in doc_dirs] == ["a", "b"]


def test_convert_many_failure_includes_filename(tmp_path, monkeypatch):
    import pytest
    import web.pipeline_runner as pr

    def boom(upload_path, converted_root):
        raise RuntimeError("kordoc 폭발")

    monkeypatch.setattr(pr, "convert", boom)
    f = tmp_path / "요람.hwp"
    f.write_bytes(b"x")

    with pytest.raises(pr.PipelineError) as exc_info:
        pr.convert_many([f], tmp_path / "converted")
    assert "요람.hwp" in str(exc_info.value)
