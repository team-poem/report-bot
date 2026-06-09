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
