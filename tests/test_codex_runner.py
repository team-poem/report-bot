import os
import stat
import sys
from pathlib import Path

import pytest

from web.codex_runner import build_prompt, run_codex, CodexError


def test_build_prompt_includes_request_and_instructions():
    prompt = build_prompt("학과별 입학정원 추이를 표로 정리해줘")
    assert "학과별 입학정원 추이를 표로 정리해줘" in prompt
    assert "document.md" in prompt
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


def test_build_prompt_report_uses_report_instruction():
    from web.codex_runner import SYSTEM_INSTRUCTION, build_prompt

    prompt = build_prompt("정리해줘", output_type="report")
    assert SYSTEM_INSTRUCTION in prompt
    assert "정리해줘" in prompt


def test_build_prompt_merge_uses_merge_instruction():
    from web.codex_runner import MERGE_INSTRUCTION, build_prompt

    prompt = build_prompt("하나로 취합해줘", output_type="merge")
    assert MERGE_INSTRUCTION in prompt
    assert "취합" in prompt


def test_build_prompt_default_is_report():
    from web.codex_runner import SYSTEM_INSTRUCTION, build_prompt
    assert SYSTEM_INSTRUCTION in build_prompt("x")


def _slots():
    from web.hwpx_template import Slot
    return [
        Slot(id="본문-1", kind="본문", instruction=""),
        Slot(id="수정-2", kind="수정", instruction="최신 수치로", original_text="작년 내용"),
    ]


def test_build_prompt_with_template_lists_slots_and_format():
    from web.codex_runner import build_prompt

    prompt = build_prompt("갱신해줘", output_type="report",
                          template_md="# 양식 구조", slots=_slots())
    assert "# 양식 구조" in prompt
    assert "본문-1" in prompt and "수정-2" in prompt
    assert "최신 수치로" in prompt
    assert "작년 내용" in prompt           # 수정 구간 원문 포함
    assert "===SLOT:" in prompt            # 출력 형식 명세
    assert "갱신해줘" in prompt


def test_build_prompt_without_slots_unchanged():
    from web.codex_runner import SYSTEM_INSTRUCTION, build_prompt

    prompt = build_prompt("정리해줘")
    assert SYSTEM_INSTRUCTION in prompt
    assert "===SLOT:" not in prompt


def test_parse_slot_output_happy_path():
    from web.codex_runner import parse_slot_output

    md = (
        "===SLOT: 본문-1===\n# 내용\n본문이다\n===END===\n\n"
        "===SLOT: 수정-2===\n고친 내용\n===END===\n"
    )
    contents = parse_slot_output(md, ["본문-1", "수정-2"])
    assert contents["본문-1"].startswith("# 내용")
    assert contents["수정-2"] == "고친 내용"


def test_parse_slot_output_missing_slot_raises():
    import pytest
    from web.codex_runner import SlotOutputError, parse_slot_output

    md = "===SLOT: 본문-1===\nx\n===END===\n"
    with pytest.raises(SlotOutputError, match="수정-2"):
        parse_slot_output(md, ["본문-1", "수정-2"])


def test_sandbox_mode_defaults_to_read_only(monkeypatch):
    from web.codex_runner import _sandbox_mode

    monkeypatch.delenv("REPORT_BOT_CODEX_SANDBOX", raising=False)
    assert _sandbox_mode() == "read-only"


def test_sandbox_mode_overridden_by_env(monkeypatch):
    from web.codex_runner import _sandbox_mode

    monkeypatch.setenv("REPORT_BOT_CODEX_SANDBOX", "danger-full-access")
    assert _sandbox_mode() == "danger-full-access"
