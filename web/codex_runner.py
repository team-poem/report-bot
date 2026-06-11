"""codex exec 호출: 프롬프트 조립 + JSONL 이벤트 스트리밍 + report.md 생성."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable

SYSTEM_INSTRUCTION = (
    "너는 현재 폴더 아래 문서 폴더들(각 폴더의 document.md, facts.json, "
    "tables_long.csv, table_*.csv)을 읽고 분석 리포트를 작성하는 어시스턴트다. "
    "문서 폴더가 여러 개면 모두 읽어라. 추측하지 말고 데이터 근거를 표·수치로 제시하라. "
    "근거가 없으면 \"데이터에서 확인 불가\"라고 명시하라. "
    "아래 담당자 요청에 맞춰 한국어 Markdown 리포트를 작성하라."
)

MERGE_INSTRUCTION = (
    "너는 현재 폴더 아래 여러 문서 폴더(각 폴더의 document.md, facts.json, "
    "tables_long.csv, table_*.csv)를 읽고, 여러 문서의 내용을 하나의 새 문서로 "
    "취합·정리하는 어시스턴트다. 모든 문서 폴더를 빠짐없이 읽어라. "
    "추측하지 말고 원문 데이터 근거를 표·수치로 제시하고, 근거가 없으면 "
    "\"데이터에서 확인 불가\"라고 명시하라. "
    "아래 담당자 요청에 맞춰 취합된 한국어 Markdown 문서를 작성하라."
)


TEMPLATE_OUTPUT_SPEC = (
    "출력 형식: 아래 모든 슬롯에 대해, 반드시 다음 형식의 블록만 출력하라.\n"
    "===SLOT: <슬롯ID>===\n"
    "(해당 슬롯에 들어갈 한국어 Markdown 내용)\n"
    "===END===\n"
    "모든 슬롯을 빠짐없이 채우고, 블록 밖에는 어떤 텍스트도 쓰지 마라."
)


class CodexError(RuntimeError):
    pass


class SlotOutputError(RuntimeError):
    pass


def build_prompt(
    request_text: str,
    output_type: str = "report",
    template_md: str | None = None,
    slots: list | None = None,
) -> str:
    instruction = MERGE_INSTRUCTION if output_type == "merge" else SYSTEM_INSTRUCTION
    parts = [instruction]
    if slots:
        parts.append("[양식 문서 구조]\n" + (template_md or "(양식 변환 결과 없음)"))
        lines = []
        for s in slots:
            line = f"- {s.id}: {s.instruction or '맥락에 맞는 내용 작성'}"
            if s.kind == "수정" and s.original_text:
                line += f"\n  [기존 내용]\n  {s.original_text}"
            lines.append(line)
        parts.append("[채울 슬롯]\n" + "\n".join(lines))
        parts.append(TEMPLATE_OUTPUT_SPEC)
    parts.append(f"[담당자 요청]\n{request_text}")
    return "\n\n".join(parts) + "\n"


_SLOT_OUTPUT_RE = re.compile(r"===SLOT:\s*(.+?)\s*===\n(.*?)===END===", re.S)


def parse_slot_output(md_text: str, expected_ids: list[str]) -> dict[str, str]:
    """codex 의 슬롯 구조 출력을 {슬롯ID: 마크다운} 으로 파싱하고 누락을 검증한다."""
    contents = {sid.strip(): body.strip() for sid, body in _SLOT_OUTPUT_RE.findall(md_text)}
    missing = [sid for sid in expected_ids if sid not in contents]
    if missing:
        raise SlotOutputError(f"채워지지 않은 슬롯: {', '.join(missing)}")
    return contents


def run_codex(
    converted_dir: Path,
    request_text: str,
    report_path: Path,
    on_event: Callable[[dict], None],
    output_type: str = "report",
    template_md: str | None = None,
    slots: list | None = None,
    codex_cmd: str = "codex",
    timeout: int = 1800,
) -> None:
    """codex exec 를 실행해 report_path 에 리포트를 쓰고, JSONL 이벤트를 on_event 로 흘린다."""
    prompt = build_prompt(request_text, output_type, template_md=template_md, slots=slots)
    cmd = [
        *shlex.split(codex_cmd),
        "exec",
        prompt,
        "-C", str(converted_dir),
        "-s", "read-only",
        "--skip-git-repo-check",
        "--json",
        "-o", str(report_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"type": "raw", "text": line}
        on_event(event)

    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise CodexError(f"codex 시간 초과({timeout}s)") from exc

    if proc.returncode != 0:
        raise CodexError(stderr.strip() or f"codex 비정상 종료(코드 {proc.returncode})")
    if not report_path.exists():
        raise CodexError("codex 가 리포트 파일을 생성하지 못했습니다.")
