#!/usr/bin/env python3
"""
kordoc 기반 HWP/HWPX 문서 변환 파이프라인.

입력 폴더의 한글/문서 파일을 kordoc CLI로 Markdown/JSON으로 변환하고,
표와 기본 수치/날짜/담당자 후보를 Excel/CSV 친화 데이터로 추출합니다.

사용 예:
  python3 scripts/kordoc_pipeline.py ./input_hwp -o ./output
  python3 scripts/kordoc_pipeline.py ./input_hwp/사업계획서.hwp -o ./output --pages 1-3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_EXTENSIONS = {
    ".hwp",
    ".hwpx",
    ".hwpml",
    ".pdf",
    ".xls",
    ".xlsx",
    ".docx",
}


@dataclass
class ExtractedTable:
    table_index: int
    page_number: int | None
    rows: list[list[str]]
    has_header: bool


def ensure_kordoc_available() -> None:
    if not shutil.which("npx"):
        raise RuntimeError("npx를 찾을 수 없습니다. Node.js/npm 설치가 필요합니다.")


def discover_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in SUPPORTED_EXTENSIONS else []

    files: list[Path] = []
    for path in input_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    return sorted(files)


def safe_doc_id(path: Path, root: Path | None = None) -> str:
    if root is None or root.is_file():
        raw = path.stem
    else:
        raw = str(path.relative_to(root).with_suffix(""))
    raw = unicodedata.normalize("NFC", raw)
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", raw).strip("_")
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def run_kordoc(file_path: Path, pages: str | None = None) -> dict[str, Any]:
    cmd = ["npx", "-y", "kordoc", str(file_path), "--format", "json", "--silent"]
    if pages:
        cmd.extend(["--pages", pages])

    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "kordoc 실패")

    stdout = completed.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kordoc JSON 파싱 실패: {exc}\nstdout 앞부분: {stdout[:500]}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]] | list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        if not rows:
            f.write("")
            return
        first = rows[0]
        if isinstance(first, dict):
            fieldnames: list[str] = []
            for row in rows:  # keep stable first-seen order
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)  # type: ignore[arg-type]
        else:
            writer = csv.writer(f)
            writer.writerows(rows)  # type: ignore[arg-type]


def cell_text(cell: Any) -> str:
    if isinstance(cell, dict):
        return str(cell.get("text", "")).strip()
    return str(cell or "").strip()


def expand_table_grid(table: dict[str, Any]) -> list[list[str]]:
    """kordoc table.cells를 CSV 친화 2차원 문자열 배열로 변환.

    rowspan/colspan은 원본 위치에 값을 넣고 span 영역에는 빈 문자열을 채웁니다.
    병합 구조 자체는 tables.json에 보존됩니다.
    """
    cells = table.get("cells") or []
    rows_count = int(table.get("rows") or len(cells) or 0)
    cols_count = int(table.get("cols") or 0)
    if not cols_count:
        cols_count = max((len(row) for row in cells if isinstance(row, list)), default=0)

    grid = [["" for _ in range(cols_count)] for _ in range(rows_count)]
    occupied = [[False for _ in range(cols_count)] for _ in range(rows_count)]

    for r, row in enumerate(cells):
        if r >= rows_count or not isinstance(row, list):
            continue
        c = 0
        for cell in row:
            while c < cols_count and occupied[r][c]:
                c += 1
            if c >= cols_count:
                break
            text = cell_text(cell)
            col_span = 1
            row_span = 1
            if isinstance(cell, dict):
                col_span = max(1, int(cell.get("colSpan") or 1))
                row_span = max(1, int(cell.get("rowSpan") or 1))
            grid[r][c] = text
            for rr in range(r, min(rows_count, r + row_span)):
                for cc in range(c, min(cols_count, c + col_span)):
                    occupied[rr][cc] = True
            c += col_span
    return grid


def extract_tables(blocks: list[dict[str, Any]]) -> list[ExtractedTable]:
    tables: list[ExtractedTable] = []
    for block in blocks:
        if block.get("type") != "table":
            continue
        table = block.get("table") or {}
        tables.append(
            ExtractedTable(
                table_index=len(tables) + 1,
                page_number=block.get("pageNumber"),
                rows=expand_table_grid(table),
                has_header=bool(table.get("hasHeader")),
            )
        )
    return tables


def table_long_rows(doc_id: str, source_file: Path, tables: list[ExtractedTable]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in tables:
        headers = table.rows[0] if table.has_header and table.rows else []
        for r_idx, row in enumerate(table.rows, start=1):
            for c_idx, value in enumerate(row, start=1):
                if not value:
                    continue
                rows.append(
                    {
                        "doc_id": doc_id,
                        "source_file": str(source_file),
                        "table_index": table.table_index,
                        "page_number": table.page_number or "",
                        "row": r_idx,
                        "col": c_idx,
                        "header": headers[c_idx - 1] if c_idx - 1 < len(headers) else "",
                        "value": value,
                    }
                )
    return rows


MONEY_RE = re.compile(r"(?P<amount>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>억원|천만원|백만원|만원|천원|원)")
PERCENT_RE = re.compile(r"(?P<value>[+-]?\d[\d,]*(?:\.\d+)?)\s*%")
DATE_RE = re.compile(
    r"(?:\d{4}[.\-/년]\s*\d{1,2}(?:[.\-/월]\s*\d{1,2})?\s*(?:일)?)|(?:\d{1,2}[.\-/]\d{1,2})"
)
PHONE_RE = re.compile(r"(?:\d{2,3}-\d{3,4}-\d{4})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NUMBER_WITH_UNIT_RE = re.compile(r"(?P<value>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>명|건|개|회|식|대|쪽|페이지|시간|일|개월|년)")


def normalize_number(value: str) -> float | int | None:
    try:
        n = float(value.replace(",", ""))
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


def normalize_money(amount: str, unit: str) -> int | float | None:
    n = normalize_number(amount)
    if n is None:
        return None
    multiplier = {
        "원": 1,
        "천원": 1_000,
        "만원": 10_000,
        "백만원": 1_000_000,
        "천만원": 10_000_000,
        "억원": 100_000_000,
    }[unit]
    return n * multiplier


def nearby_label(text: str, start: int) -> str:
    prefix = text[max(0, start - 40) : start]
    prefix = re.sub(r"[\n\r\t]+", " ", prefix)
    # 마지막 콜론/공백 주변을 우선 라벨로 추정
    m = re.search(r"([가-힣A-Za-z0-9·/()\s]{2,30})[:：]?\s*$", prefix)
    return m.group(1).strip() if m else ""


def add_fact(facts: list[dict[str, Any]], **kwargs: Any) -> None:
    kwargs.setdefault("confidence", 0.55)
    facts.append(kwargs)


def extract_facts(doc_id: str, source_file: Path, markdown: str, tables: list[ExtractedTable]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    # 1) 2열 key-value 표를 우선 추출
    for table in tables:
        data_rows = table.rows[1:] if table.has_header else table.rows
        for r_idx, row in enumerate(data_rows, start=2 if table.has_header else 1):
            non_empty = [x for x in row if x]
            if len(row) >= 2 and row[0] and row[1] and len(non_empty) <= 3:
                add_fact(
                    facts,
                    doc_id=doc_id,
                    source_file=str(source_file),
                    fact_type="table_key_value",
                    label=row[0],
                    value=row[1],
                    unit="",
                    normalized_value="",
                    source_text=" | ".join(row),
                    page_number=table.page_number or "",
                    table_index=table.table_index,
                    row=r_idx,
                    confidence=0.75,
                )

    # 2) 본문에서 금액/퍼센트/날짜/연락처/수량 후보 추출
    patterns = [
        ("money", MONEY_RE),
        ("percent", PERCENT_RE),
        ("date", DATE_RE),
        ("phone", PHONE_RE),
        ("email", EMAIL_RE),
        ("number_with_unit", NUMBER_WITH_UNIT_RE),
    ]
    for fact_type, regex in patterns:
        for match in regex.finditer(markdown):
            source = match.group(0)
            label = nearby_label(markdown, match.start())
            unit = ""
            normalized: Any = ""
            if fact_type == "money":
                unit = match.group("unit")
                normalized = normalize_money(match.group("amount"), unit)
            elif fact_type == "percent":
                unit = "%"
                normalized = normalize_number(match.group("value"))
            elif fact_type == "number_with_unit":
                unit = match.group("unit")
                normalized = normalize_number(match.group("value"))

            add_fact(
                facts,
                doc_id=doc_id,
                source_file=str(source_file),
                fact_type=fact_type,
                label=label,
                value=source,
                unit=unit,
                normalized_value=normalized,
                source_text=markdown[max(0, match.start() - 60) : match.end() + 60].replace("\n", " ").strip(),
                page_number="",
                table_index="",
                row="",
                confidence=0.6 if label else 0.45,
            )

    return facts


def _xlsx_cell(value: Any) -> Any:
    """openpyxl 은 dict/list 셀 값에서 죽는다(kordoc 이 NEEDS_OCR 같은
    에러 객체를 표 안에 끼워 넣는 경우). 스칼라가 아니면 JSON 문자열로 강등."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]] | list[list[Any]]]) -> bool:
    try:
        from openpyxl import Workbook
    except ImportError:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    default = wb.active
    wb.remove(default)

    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name[:31])
        if not rows:
            continue
        first = rows[0]
        if isinstance(first, dict):
            headers: list[str] = []
            for row in rows:  # type: ignore[assignment]
                for key in row.keys():
                    if key not in headers:
                        headers.append(key)
            ws.append(headers)
            for row in rows:  # type: ignore[assignment]
                ws.append([_xlsx_cell(row.get(h, "")) for h in headers])
        else:
            for row in rows:  # type: ignore[assignment]
                ws.append([_xlsx_cell(cell) for cell in row])
    wb.save(path)
    return True


def process_file(file_path: Path, input_root: Path, out_dir: Path, pages: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    doc_id = safe_doc_id(file_path, input_root)
    doc_dir = out_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    result = run_kordoc(file_path, pages=pages)
    write_json(doc_dir / "parse_result.json", result)

    if not result.get("success", False):
        write_json(doc_dir / "error.json", result)
        return [], []

    markdown = result.get("markdown") or ""
    blocks = result.get("blocks") or []
    metadata = result.get("metadata") or {}
    warnings = result.get("warnings") or []

    (doc_dir / "document.md").write_text(markdown, encoding="utf-8")
    write_json(doc_dir / "blocks.json", blocks)
    write_json(doc_dir / "metadata.json", metadata)
    write_json(doc_dir / "warnings.json", warnings)

    tables = extract_tables(blocks)
    tables_json = [
        {
            "table_index": t.table_index,
            "page_number": t.page_number,
            "has_header": t.has_header,
            "rows": t.rows,
        }
        for t in tables
    ]
    write_json(doc_dir / "tables.json", tables_json)

    for table in tables:
        write_csv(doc_dir / f"table_{table.table_index:02d}.csv", table.rows)

    table_rows = table_long_rows(doc_id, file_path, tables)
    facts = extract_facts(doc_id, file_path, markdown, tables)

    write_csv(doc_dir / "tables_long.csv", table_rows)
    write_json(doc_dir / "facts.json", facts)
    write_csv(doc_dir / "facts.csv", facts)

    xlsx_ok = write_xlsx(
        doc_dir / "document_data.xlsx",
        {
            "facts": facts,
            "tables_long": table_rows,
            "metadata": [{"key": k, "value": json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v} for k, v in metadata.items()],
            "warnings": [{"warning": w} for w in warnings],
        },
    )
    if not xlsx_ok:
        (doc_dir / "README_xlsx.txt").write_text(
            "openpyxl이 설치되어 있지 않아 document_data.xlsx를 만들지 못했습니다.\n"
            "pip install openpyxl 후 다시 실행하거나 CSV 파일을 사용하세요.\n",
            encoding="utf-8",
        )

    return facts, table_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="kordoc으로 문서를 Markdown/JSON/CSV/XLSX 산출물로 변환합니다.")
    parser.add_argument("input", help="입력 파일 또는 폴더")
    parser.add_argument("-o", "--out-dir", default="output", help="출력 폴더 (기본: output)")
    parser.add_argument("--pages", help="kordoc 페이지/섹션 범위, 예: 1-3 또는 1,3,5")
    args = parser.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not input_path.exists():
        print(f"입력 경로가 없습니다: {input_path}", file=sys.stderr)
        return 2

    try:
        ensure_kordoc_available()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    files = discover_files(input_path)
    if not files:
        print(f"지원 문서 파일이 없습니다: {input_path}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    all_facts: list[dict[str, Any]] = []
    all_table_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    print(f"대상 문서 {len(files)}개")
    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] {file_path}")
        try:
            facts, table_rows = process_file(file_path, input_path, out_dir, args.pages)
            all_facts.extend(facts)
            all_table_rows.extend(table_rows)
        except Exception as exc:  # noqa: BLE001 - 배치 처리에서는 문서별 실패 기록 필요
            print(f"  실패: {exc}", file=sys.stderr)
            errors.append({"source_file": str(file_path), "error": str(exc)})

    aggregate = out_dir / "_aggregate"
    write_json(aggregate / "facts_all.json", all_facts)
    write_csv(aggregate / "facts_all.csv", all_facts)
    write_csv(aggregate / "tables_all.csv", all_table_rows)
    write_json(aggregate / "errors.json", errors)
    write_xlsx(
        aggregate / "all_documents.xlsx",
        {
            "facts_all": all_facts,
            "tables_all": all_table_rows,
            "errors": errors,
        },
    )

    print("완료")
    print(f"- 출력 폴더: {out_dir}")
    print(f"- 통합 facts: {aggregate / 'facts_all.csv'}")
    print(f"- 통합 Excel: {aggregate / 'all_documents.xlsx'}")
    if errors:
        print(f"- 실패 {len(errors)}건: {aggregate / 'errors.json'}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
