"""codex exec 호출: 프롬프트 조립 + JSONL 이벤트 스트리밍 + report.md 생성."""
from __future__ import annotations

import json
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


class CodexError(RuntimeError):
    pass


def build_prompt(request_text: str, output_type: str = "report") -> str:
    instruction = MERGE_INSTRUCTION if output_type == "merge" else SYSTEM_INSTRUCTION
    return f"{instruction}\n\n[담당자 요청]\n{request_text}\n"


def run_codex(
    converted_dir: Path,
    request_text: str,
    report_path: Path,
    output_type: str = "report",
    on_event: Callable[[dict], None] = lambda e: None,
    codex_cmd: str = "codex",
    timeout: int = 1800,
) -> None:
    """codex exec 를 실행해 report_path 에 리포트를 쓰고, JSONL 이벤트를 on_event 로 흘린다."""
    prompt = build_prompt(request_text, output_type)
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
