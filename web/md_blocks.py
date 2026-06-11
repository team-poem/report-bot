"""마크다운 부분집합 파서: 제목(h1-h3)/문단/표/목록/굵게·기울임.

hwpx_writer 가 소비하는 중간 표현만 만든다. XML 은 모른다.
지원 범위 밖 요소(h4+, 코드펜스 등)는 내용을 보존한 채 문단으로 강등한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False


@dataclass
class Heading:
    level: int  # 1-3
    spans: list[Span]


@dataclass
class Paragraph:
    spans: list[Span]


@dataclass
class ListBlock:
    ordered: bool
    items: list[list[Span]]


@dataclass
class Table:
    rows: list[list[list[Span]]]  # rows → cells → spans
    has_header: bool = True


Block = Heading | Paragraph | ListBlock | Table

_INLINE_RE = re.compile(r"\*\*\*([^*]+)\*\*\*|\*\*([^*]+)\*\*|\*([^*]+)\*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}.*$")


def parse_inline(text: str) -> list[Span]:
    spans: list[Span] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos:m.start()]))
        if m.group(1) is not None:
            spans.append(Span(m.group(1), bold=True, italic=True))
        elif m.group(2) is not None:
            spans.append(Span(m.group(2), bold=True))
        else:
            spans.append(Span(m.group(3), italic=True))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:]))
    return spans or [Span("")]


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_blocks(md_text: str) -> list[Block]:
    blocks: list[Block] = []
    para_lines: list[str] = []
    lines = md_text.splitlines()
    i = 0

    def flush_para() -> None:
        if para_lines:
            blocks.append(Paragraph(parse_inline(" ".join(para_lines))))
            para_lines.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        # 코드펜스: 서식 없이 줄 단위 문단으로 강등
        if stripped.startswith("```"):
            flush_para()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                if lines[i].strip():
                    blocks.append(Paragraph([Span(lines[i].rstrip())]))
                i += 1
            i += 1  # 닫는 펜스 건너뜀
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            spans = parse_inline(m.group(2).strip())
            if level <= 3:
                blocks.append(Heading(level, spans))
            else:
                blocks.append(Paragraph(spans))
            i += 1
            continue

        if stripped.startswith("|"):
            flush_para()
            rows: list[list[list[Span]]] = []
            has_header = False
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_line = lines[i].strip()
                if _TABLE_SEP_RE.match(row_line) and len(rows) == 1:
                    has_header = True
                else:
                    rows.append([parse_inline(c) for c in _split_table_row(row_line)])
                i += 1
            blocks.append(Table(rows=rows, has_header=has_header))
            continue

        ul = _UL_RE.match(stripped)
        ol = _OL_RE.match(stripped)
        if ul or ol:
            flush_para()
            ordered = ol is not None
            items: list[list[Span]] = []
            while i < len(lines):
                s = lines[i].strip()
                m_item = (_OL_RE if ordered else _UL_RE).match(s)
                if not m_item:
                    break
                items.append(parse_inline(m_item.group(1).strip()))
                i += 1
            blocks.append(ListBlock(ordered=ordered, items=items))
            continue

        para_lines.append(stripped)
        i += 1

    flush_para()
    return blocks
