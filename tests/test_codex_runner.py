import os
import stat
import sys
from pathlib import Path

import pytest

from web.codex_runner import build_prompt, run_codex, CodexError


def test_build_prompt_includes_request_and_instructions():
    prompt = build_prompt("학과별 입학정원 추이를 표로 정리해줘")
    assert "학과별 입학정원 추이를 표로 정리해줘" in prompt
    assert "converted/" in prompt
    assert "Markdown" in prompt


def _write_fake_codex(path: Path, body: str) -> str:
    """sys.argv 를 스캔해 -o 경로에 리포트를 쓰고 JSONL 을 내보내는 가짜 codex."""
    script = path / "fake_codex.py"
    script.write_text(
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "out = argv[argv.index('-o') + 1] if '-o' in argv else None\n"
        "print(json.dumps({'type': 'item', 'text': 'reading document.md'}))\n"
        "print(json.dumps({'type': 'item', 'text': 'writing report'}))\n"
        "sys.stdout.flush()\n"
        f"open(out, 'w', encoding='utf-8').write({body!r})\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def test_run_codex_streams_events_and_writes_report(tmp_path: Path):
    converted = tmp_path / "converted"
    converted.mkdir()
    report = tmp_path / "report.md"
    log = tmp_path / "codex_log.jsonl"
    codex_cmd = _write_fake_codex(tmp_path, "# 분석 리포트\n표 내용")

    events: list[dict] = []
    run_codex(
        converted_dir=converted,
        request_text="정리해줘",
        report_path=report,
        on_event=events.append,
        codex_cmd=codex_cmd,
    )

    assert report.read_text(encoding="utf-8") == "# 분석 리포트\n표 내용"
    assert any(e.get("text") == "reading document.md" for e in events)
    assert len(events) == 2


def test_run_codex_raises_on_nonzero_exit(tmp_path: Path):
    converted = tmp_path / "converted"
    converted.mkdir()
    report = tmp_path / "report.md"
    fail_script = tmp_path / "fail.py"
    fail_script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    codex_cmd = f"{sys.executable} {fail_script}"

    with pytest.raises(CodexError):
        run_codex(
            converted_dir=converted,
            request_text="x",
            report_path=report,
            on_event=lambda e: None,
            codex_cmd=codex_cmd,
        )
