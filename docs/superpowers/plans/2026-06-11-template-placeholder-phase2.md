# 양식 + 자리표시자 규약 (2단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 담당자가 `{{본문}}` / `{{추가: 지시}}` / `{{수정시작: 지시}}~{{수정끝}}` 자리표시자가 든 양식 HWPX를 잡과 함께 올리면, 생성 내용이 양식의 지정 위치에 양식 서식으로 채워진 한글 파일을 받는다.

**Architecture:** 신규 모듈 `web/hwpx_template.py`가 양식 zip의 section XML을 문단 단위로 스캔(run 합치기)해 슬롯을 추출하고, Codex의 슬롯 구조 출력(`===SLOT: id===`)을 받아 해당 문단을 렌더된 XML로 치환한다. 임의 양식의 header.xml에는 우리 서식 ID가 없으므로 **우리 charPr 7종 + 표 테두리 borderFill을 양식 header에 추가 주입**하고, 1단계 렌더러를 `StyleIds`로 파라미터화해 주입된 ID로 렌더한다. 본문 일반 텍스트는 자리표시자 문단의 서식을 그대로 상속한다. 스펙: `docs/superpowers/specs/2026-06-11-multi-upload-hwpx-design.md` §3.2~3.4.

**Tech Stack:** 표준 라이브러리만 (zipfile, xml.etree.ElementTree, re). 새 의존성 없음.

**설계 결정 (스펙에 더한 구체화):**
1. **자리표시자는 한 문단(줄)에 단독으로** 있어야 한다. 업로드 검증에서 강제한다(문단에 마커와 다른 텍스트가 섞이면 400). 이 규약 덕분에 치환이 "문단 교체"로 단순해진다.
2. **서식 상속의 범위:** 슬롯의 일반 텍스트는 자리표시자 문단의 charPr/paraPr을 상속한다. 굵게/기울임/제목/표 테두리는 주입된 우리 정의를 쓴다(양식 글꼴과 다를 수 있음 — 문서화된 트레이드오프, 실양식 검증에서 확인).
3. **수정 구간의 원문**은 시작~끝 사이 같은 부모의 직계 문단 텍스트만 수집한다(사이에 낀 표 내부 텍스트는 원문 프롬프트에서 제외되지만 교체 시에는 함께 제거됨 — 한계로 문서화).
4. **ET 재직렬화 리스크:** section XML을 ElementTree로 재직렬화하므로 표준 hwpx 접두사들을 `register_namespace`로 고정한다. 최종 안전망은 kordoc 라운드트립 + 실제 한글 열기 수동 게이트.

**리스크 배치:** 가장 불확실한 것은 "치환된 양식을 한글이 여는가". Task 4에서 kordoc 라운드트립을 통과해야 다음으로 넘어가고, Task 8 끝에 실양식 수동 게이트가 있다.

## File Structure

```
web/
  hwpx_writer.py    (수정) 렌더러를 StyleIds 로 파라미터화, render_blocks/_char_pr/_border 재사용 노출
  hwpx_template.py  (신설) Slot/TemplateError, scan_placeholders, fill_template, 스타일 주입
  codex_runner.py   (수정) build_prompt 양식 변형, parse_slot_output, run_codex 파라미터
  job_manager.py    (수정) template 저장, Job.template_path
  worker.py         (수정) TemplateFns 주입 + 양식 분기
  app.py            (수정) template 업로드 파라미터 + 400 검증 + 배선
  static/index.html (수정) 양식 첨부 input + 규약 안내
tests/
  test_hwpx_template.py (신설)
  (기존 테스트 파일들에 추가/수정)
README.md           (수정) 자리표시자 규약 문서화
```

---

### Task 1: 렌더러 StyleIds 파라미터화 (`hwpx_writer.py`)

임의 양식의 header.xml에서는 charPr id 0~6, borderFill 2가 우리 정의가 아니다. 렌더러가 참조하는 모든 ID를 `StyleIds` 데이터클래스로 묶어 파라미터화한다. 기본값은 내장 스켈레톤의 ID라 기존 동작은 그대로다.

**Files:**
- Modify: `web/hwpx_writer.py` (렌더러 구간, 현재 281~395행 부근)
- Test: `tests/test_hwpx_writer.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_hwpx_writer.py 에 append:

```python
def test_render_blocks_with_custom_style_ids():
    from web.hwpx_writer import StyleIds, render_blocks

    styles = StyleIds(normal=10, bold=11, italic=12, bold_italic=13,
                      h1=14, h2=15, h3=16, table_border_fill=9,
                      para_pr=5, style=7)
    paras = render_blocks(
        "# 제목\n본문 **굵게**\n\n| a |\n| --- |\n| 1 |\n",
        styles=styles, start_id=500,
    )
    xml = "".join(paras)
    assert 'charPrIDRef="14"' in xml      # h1 → 커스텀 id
    assert 'charPrIDRef="11"' in xml      # 굵게 → 커스텀 id
    assert 'charPrIDRef="10"' in xml      # 일반 → 커스텀 id
    assert 'paraPrIDRef="5"' in xml and 'styleIDRef="7"' in xml
    assert 'borderFillIDRef="9"' in xml   # 표 테두리 → 커스텀 id
    assert 'id="500"' in xml              # hp:p id 시작값
    assert 'paraPrIDRef="0"' not in xml   # 기본 id 가 새어 나오지 않음


def test_render_blocks_default_matches_markdown_to_hwpx(tmp_path: Path):
    """기본 StyleIds 렌더가 기존 markdown_to_hwpx 출력과 동일해야 한다(회귀 방지)."""
    from web.hwpx_writer import DEFAULT_STYLE_IDS, markdown_to_hwpx, render_blocks

    md = "# 제목\n본문\n\n- 항목\n"
    out = tmp_path / "d.hwpx"
    markdown_to_hwpx(md, out)
    with zipfile.ZipFile(out) as zf:
        section = zf.read("Contents/section0.xml").decode("utf-8")
    for para in render_blocks(md, styles=DEFAULT_STYLE_IDS, start_id=2):
        assert para in section
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_writer.py -v`
Expected: 2 FAIL — `ImportError: cannot import name 'StyleIds'`

- [ ] **Step 3: 구현 — 렌더러 구간을 다음으로 교체**

`web/hwpx_writer.py` 상단 import에 `from dataclasses import dataclass` 추가. 그리고 `# block renderer` 구간(현재 `_HEADING_CHAR_PR` 정의부터 `_blocks_to_paras` 끝까지)을 다음으로 교체:

```python
# ------------------------------------------------------------- block renderer

@dataclass(frozen=True)
class StyleIds:
    """렌더러가 참조하는 header.xml 정의 ID 묶음. 기본값은 내장 스켈레톤."""
    normal: int = 0
    bold: int = 1
    italic: int = 2
    bold_italic: int = 3
    h1: int = 4
    h2: int = 5
    h3: int = 6
    table_border_fill: int = 2
    para_pr: int = 0
    style: int = 0


DEFAULT_STYLE_IDS = StyleIds()


def _span_char_pr(span: Span, styles: StyleIds) -> int:
    if span.bold and span.italic:
        return styles.bold_italic
    if span.bold:
        return styles.bold
    if span.italic:
        return styles.italic
    return styles.normal


def _heading_char_pr(level: int, styles: StyleIds) -> int:
    return {1: styles.h1, 2: styles.h2, 3: styles.h3}[level]


class _IdGen:
    """hp:p id 는 문서 안에서 유일하기만 하면 된다. 스켈레톤에서 1은 secPr 문단."""

    def __init__(self, start: int = 2) -> None:
        self._next = start

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


def _runs_xml(spans: list[Span], styles: StyleIds,
              char_pr_override: int | None = None) -> str:
    runs = []
    for span in spans:
        pr = char_pr_override if char_pr_override is not None else _span_char_pr(span, styles)
        runs.append(f'<hp:run charPrIDRef="{pr}"><hp:t>{escape(span.text)}</hp:t></hp:run>')
    return "".join(runs)


def _para_xml(ids: _IdGen, spans: list[Span], styles: StyleIds,
              char_pr_override: int | None = None) -> str:
    return (
        f'<hp:p id="{ids.take()}" paraPrIDRef="{styles.para_pr}" '
        f'styleIDRef="{styles.style}" pageBreak="0" columnBreak="0" merged="0">'
        + _runs_xml(spans, styles, char_pr_override)
        + "</hp:p>"
    )


def _cell_xml(ids: _IdGen, spans: list[Span], col: int, row: int,
              col_width: int, bold_header: bool, styles: StyleIds) -> str:
    override = styles.bold if bold_header else None
    return (
        '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" '
        f'dirty="0" borderFillIDRef="{styles.table_border_fill}">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + _para_xml(ids, spans, styles, override)
        + "</hp:subList>"
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        '<hp:cellSpan colSpan="1" rowSpan="1"/>'
        f'<hp:cellSz width="{col_width}" height="1000"/>'
        '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
        "</hp:tc>"
    )


def _table_xml(ids: _IdGen, table: Table, styles: StyleIds) -> str:
    if not table.rows:
        return ""
    row_cnt = len(table.rows)
    col_cnt = max(len(r) for r in table.rows)
    col_width = PAGE_TEXT_WIDTH // col_cnt
    trs = []
    for r, row in enumerate(table.rows):
        bold_header = table.has_header and r == 0
        tcs = []
        for c in range(col_cnt):
            spans = row[c] if c < len(row) else [Span("")]
            tcs.append(_cell_xml(ids, spans, c, r, col_width, bold_header, styles))
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    tbl = (
        f'<hp:tbl id="{ids.take()}" zOrder="0" numberingType="TABLE" '
        'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'pageBreak="CELL" repeatHeader="1" rowCnt="{row_cnt}" colCnt="{col_cnt}" '
        f'cellSpacing="0" borderFillIDRef="{styles.table_border_fill}" noAdjust="0">'
        f'<hp:sz width="{PAGE_TEXT_WIDTH}" widthRelTo="ABSOLUTE" '
        f'height="{row_cnt * 1000}" heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
        'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
        '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
        + "".join(trs)
        + "</hp:tbl>"
    )
    # 표는 문단의 run 안에 들어가는 인라인 객체다 (treatAsChar)
    return (
        f'<hp:p id="{ids.take()}" paraPrIDRef="{styles.para_pr}" '
        f'styleIDRef="{styles.style}" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{styles.normal}">{tbl}<hp:t/></hp:run></hp:p>'
    )


def render_blocks(md_text: str, styles: StyleIds = DEFAULT_STYLE_IDS,
                  start_id: int = 2) -> list[str]:
    """마크다운을 hp:p XML 조각 목록으로 렌더한다. hwpx_template 이 재사용한다."""
    ids = _IdGen(start_id)
    paras: list[str] = []
    for block in parse_blocks(md_text):
        if isinstance(block, Heading):
            paras.append(_para_xml(ids, block.spans, styles,
                                   _heading_char_pr(block.level, styles)))
        elif isinstance(block, Paragraph):
            paras.append(_para_xml(ids, block.spans, styles))
        elif isinstance(block, ListBlock):
            for n, item in enumerate(block.items, start=1):
                prefix = f"{n}. " if block.ordered else "• "
                # 접두사를 첫 span 에 병합해 "• 텍스트" 가 한 run 에 이어지게 한다
                first = Span(prefix + item[0].text, bold=item[0].bold, italic=item[0].italic)
                paras.append(_para_xml(ids, [first, *item[1:]], styles))
        elif isinstance(block, Table):
            paras.append(_table_xml(ids, block, styles))
    return paras
```

그리고 `markdown_to_hwpx` 의 본문을 새 함수로 위임:

```python
def markdown_to_hwpx(md_text: str, out_path: Path) -> None:
    """마크다운을 기본 서식 HWPX 로 변환해 out_path 에 쓴다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(package_hwpx(render_blocks(md_text)))
```

(`_blocks_to_paras` 는 삭제 — render_blocks 가 대체한다.)

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: 전체 PASS (63 = 기존 61 + 신규 2)

- [ ] **Step 5: Commit**

```bash
git add web/hwpx_writer.py tests/test_hwpx_writer.py
git commit -m "refactor: parameterize renderer with StyleIds, expose render_blocks"
```

---

### Task 2: 자리표시자 스캔 (`hwpx_template.py` — scan)

**Files:**
- Create: `web/hwpx_template.py`
- Test: `tests/test_hwpx_template.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_hwpx_template.py` 생성:

```python
import io
import zipfile
from pathlib import Path

import pytest

from web.hwpx_writer import markdown_to_hwpx, package_hwpx


def make_template(tmp_path: Path, md: str, name: str = "t.hwpx") -> Path:
    """마커가 든 마크다운으로 양식 HWPX 픽스처를 만든다(우리 writer 재사용)."""
    out = tmp_path / name
    markdown_to_hwpx(md, out)
    return out


def test_scan_finds_three_marker_kinds_in_order(tmp_path: Path):
    from web.hwpx_template import scan_placeholders

    t = make_template(tmp_path, (
        "서문입니다\n\n{{본문}}\n\n{{추가: 2026년 실적 요약}}\n\n"
        "{{수정시작: 최신 수치로 갱신}}\n\n기존 문단 하나\n\n기존 문단 둘\n\n{{수정끝}}\n"
    ))
    slots = scan_placeholders(t)
    assert [s.id for s in slots] == ["본문-1", "추가-2", "수정-3"]
    assert [s.kind for s in slots] == ["본문", "추가", "수정"]
    assert slots[1].instruction == "2026년 실적 요약"
    assert slots[2].instruction == "최신 수치로 갱신"
    assert "기존 문단 하나" in slots[2].original_text
    assert "기존 문단 둘" in slots[2].original_text


def test_scan_merges_split_runs(tmp_path: Path):
    """한글이 마커 텍스트를 여러 run 으로 쪼개 저장해도 찾아야 한다."""
    from web.hwpx_template import scan_placeholders

    split_para = (
        '<hp:p id="2" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"><hp:t>{{본</hp:t></hp:run>'
        '<hp:run charPrIDRef="0"><hp:t>문}}</hp:t></hp:run></hp:p>'
    )
    out = tmp_path / "split.hwpx"
    out.write_bytes(package_hwpx([split_para]))
    slots = scan_placeholders(out)
    assert [s.id for s in slots] == ["본문-1"]


def test_scan_accepts_bytes_io(tmp_path: Path):
    from web.hwpx_template import scan_placeholders

    t = make_template(tmp_path, "{{본문}}\n")
    slots = scan_placeholders(io.BytesIO(t.read_bytes()))
    assert slots[0].id == "본문-1"


def test_scan_error_no_markers(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "마커 없는 문서\n")
    with pytest.raises(TemplateError, match="자리표시자가 없습니다"):
        scan_placeholders(t)


def test_scan_error_marker_not_alone(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "여기에 {{본문}} 이 섞여 있다\n")
    with pytest.raises(TemplateError, match="단독"):
        scan_placeholders(t)


def test_scan_error_unclosed_fix_region(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "{{수정시작: x}}\n\n내용\n")
    with pytest.raises(TemplateError, match="수정끝"):
        scan_placeholders(t)


def test_scan_error_end_without_start(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "{{수정끝}}\n")
    with pytest.raises(TemplateError, match="수정시작"):
        scan_placeholders(t)


def test_scan_error_nested_marker_in_fix_region(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "{{수정시작: x}}\n\n{{추가: y}}\n\n{{수정끝}}\n")
    with pytest.raises(TemplateError, match="안에 다른 자리표시자"):
        scan_placeholders(t)
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.hwpx_template'`

- [ ] **Step 3: 구현** — `web/hwpx_template.py` 생성:

```python
"""양식 HWPX 의 자리표시자 스캔·치환.

규약: {{본문}} / {{추가: 지시}} / {{수정시작: 지시}} ~ {{수정끝}}.
자리표시자는 한 문단에 단독으로 있어야 한다(업로드 시 검증).
슬롯 ID 는 문서 등장 순서로 자동 부여한다: 본문-1, 추가-2, 수정-3 …
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from xml.etree import ElementTree as ET

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
}

_HP_P = f"{{{NS['hp']}}}p"
_HP_T = f"{{{NS['hp']}}}t"

_MARKER_RE = re.compile(r"^\{\{(본문|추가|수정시작|수정끝)(?::\s*(.*?))?\}\}$")
_LOOSE_MARKER_RE = re.compile(r"\{\{(본문|추가|수정시작|수정끝)")
_SECTION_RE = re.compile(r"Contents/section\d+\.xml")


class TemplateError(ValueError):
    pass


@dataclass
class Slot:
    id: str            # 예: "추가-2" (등장 순서 기반)
    kind: str          # "본문" | "추가" | "수정"
    instruction: str   # 콜론 뒤 지시문 ("" 가능)
    original_text: str = ""   # 수정 구간의 기존 문단 텍스트(\n 결합)


@dataclass
class _Found:
    """스캔 결과 + 치환에 필요한 엘리먼트 참조."""
    slot: Slot
    entry: str                    # zip 안의 section 파일명
    parent: ET.Element
    start: ET.Element             # 마커 문단 (수정이면 {{수정시작}} 문단)
    end: ET.Element | None = None # {{수정끝}} 문단 (수정 전용)


def _register_namespaces() -> None:
    """ET 재직렬화 시 ns0: 같은 임의 접두사가 생기지 않게 표준 접두사를 고정."""
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)


def _para_text(p: ET.Element) -> str:
    """문단의 모든 run 텍스트를 합친다(마커가 run 으로 쪼개진 경우 대비)."""
    return "".join(t.text or "" for t in p.iter(_HP_T))


def _read_sections(source: Path | IO[bytes]) -> dict[str, ET.Element]:
    try:
        with zipfile.ZipFile(source) as zf:
            names = sorted(n for n in zf.namelist() if _SECTION_RE.fullmatch(n))
            if not names:
                raise TemplateError("HWPX 안에 Contents/section*.xml 이 없습니다.")
            return {n: ET.fromstring(zf.read(n)) for n in names}
    except zipfile.BadZipFile as exc:
        raise TemplateError("HWPX(zip) 형식이 아닙니다.") from exc
    except ET.ParseError as exc:
        raise TemplateError(f"양식 XML 파싱 실패: {exc}") from exc


def _collect_slots(trees: dict[str, ET.Element]) -> list[_Found]:
    found: list[_Found] = []
    n = 0
    for entry, root in trees.items():
        parent_map = {child: parent for parent in root.iter() for child in parent}
        open_fix: _Found | None = None
        fix_body: list[ET.Element] = []
        for p in root.iter(_HP_P):
            text = _para_text(p).strip()
            m = _MARKER_RE.fullmatch(text)
            if m is None:
                if _LOOSE_MARKER_RE.search(text):
                    raise TemplateError(
                        f"자리표시자는 한 문단에 단독으로 있어야 합니다: {text[:60]}"
                    )
                if open_fix is not None and parent_map.get(p) is open_fix.parent:
                    fix_body.append(p)
                continue

            kind, instruction = m.group(1), (m.group(2) or "").strip()
            parent = parent_map.get(p)
            if parent is None:
                raise TemplateError("자리표시자 문단의 부모를 찾을 수 없습니다.")

            if kind == "수정시작":
                if open_fix is not None:
                    raise TemplateError("{{수정시작}} 구간 안에 다른 자리표시자를 둘 수 없습니다.")
                n += 1
                open_fix = _Found(
                    Slot(id=f"수정-{n}", kind="수정", instruction=instruction),
                    entry, parent, p,
                )
                fix_body = []
                continue

            if kind == "수정끝":
                if open_fix is None:
                    raise TemplateError("{{수정끝}} 에 대응하는 {{수정시작}} 이 없습니다.")
                if parent is not open_fix.parent:
                    raise TemplateError(
                        "{{수정시작}}~{{수정끝}} 은 같은 영역(본문 또는 같은 표 칸) 안에 있어야 합니다."
                    )
                open_fix.end = p
                open_fix.slot.original_text = "\n".join(
                    _para_text(b).strip() for b in fix_body
                ).strip()
                found.append(open_fix)
                open_fix = None
                continue

            # 본문 / 추가
            if open_fix is not None:
                raise TemplateError("{{수정시작}} 구간 안에 다른 자리표시자를 둘 수 없습니다.")
            n += 1
            found.append(_Found(
                Slot(id=f"{kind}-{n}", kind=kind, instruction=instruction),
                entry, parent, p,
            ))

        if open_fix is not None:
            raise TemplateError("{{수정시작}} 이 {{수정끝}} 없이 끝났습니다.")

    if not found:
        raise TemplateError(
            "자리표시자가 없습니다. 양식에 {{본문}}, {{추가: 지시}} 또는 "
            "{{수정시작: 지시}}~{{수정끝}} 을 넣어 주세요."
        )
    return found


def scan_placeholders(source: Path | IO[bytes]) -> list[Slot]:
    """양식에서 슬롯 목록을 추출한다. 규약 위반이면 TemplateError."""
    return [f.slot for f in _collect_slots(_read_sections(source))]
```

주의: `_collect_slots` 의 수정 구간 본문 수집은 시작 마커 **뒤에 등장하는** 같은 부모의 문단만 모은다. `root.iter`는 문서 순서를 보장하므로 위 구현이 그 동작을 만족한다.

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add web/hwpx_template.py tests/test_hwpx_template.py
git commit -m "feat: scan placeholder slots in template HWPX with run merging"
```

---

### Task 3: 스타일 주입 (`hwpx_template.py` — header)

양식의 header.xml에 우리 charPr 7종(일반/굵게/기울임/굵게기울임/h1/h2/h3)과 표 테두리 borderFill 1종을 **문자열 삽입**으로 추가한다(전체 재직렬화 회피). 새 ID는 기존 최대 ID + 1부터.

**Files:**
- Modify: `web/hwpx_template.py`
- Test: `tests/test_hwpx_template.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가:**

```python
def test_inject_styles_appends_defs_and_returns_ids(tmp_path: Path):
    from web.hwpx_template import _inject_styles

    t = make_template(tmp_path, "{{본문}}\n")
    with zipfile.ZipFile(t) as zf:
        header = zf.read("Contents/header.xml").decode("utf-8")

    new_header, ids = _inject_styles(header)
    # 스켈레톤은 charPr 0~6, borderFill 1~2 → 주입 후 7~13, 3
    assert ids.normal == 7 and ids.bold == 8 and ids.h3 == 13
    assert ids.table_border_fill == 3
    assert f'<hh:charPr id="{ids.h1}"' in new_header
    assert f'<hh:borderFill id="3"' in new_header
    # itemCnt 갱신: 7 → 14, 2 → 3
    assert 'itemCnt="14"' in new_header
    assert '<hh:borderFills itemCnt="3"' in new_header
    # 기존 정의는 그대로
    assert '<hh:charPr id="0"' in new_header
    # XML 정합성 유지
    from xml.etree import ElementTree as ET
    ET.fromstring(new_header)
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py::test_inject_styles_appends_defs_and_returns_ids -v`
Expected: FAIL — `ImportError: cannot import name '_inject_styles'`

- [ ] **Step 3: 구현** — `web/hwpx_template.py` 에 추가 (상단 import 에 `from web.hwpx_writer import StyleIds, _border, _char_pr` 추가):

```python
# ------------------------------------------------------------- style 주입

_CHAR_PR_ID_RE = re.compile(r'<hh:charPr\b[^>]*\bid="(\d+)"')
_BORDER_FILL_ID_RE = re.compile(r'<hh:borderFill\b[^>]*\bid="(\d+)"')
_CHAR_PROPS_CNT_RE = re.compile(r'(<hh:charProperties\b[^>]*\bitemCnt=")(\d+)(")')
_BORDER_FILLS_CNT_RE = re.compile(r'(<hh:borderFills\b[^>]*\bitemCnt=")(\d+)(")')


def _inject_styles(header_text: str) -> tuple[str, StyleIds]:
    """양식 header.xml 에 우리 서식 정의를 추가하고 새 ID 매핑을 돌려준다.

    문자열 삽입만 한다(재직렬화 없음) — 기존 정의는 바이트 그대로 유지된다.
    """
    char_ids = [int(x) for x in _CHAR_PR_ID_RE.findall(header_text)]
    fill_ids = [int(x) for x in _BORDER_FILL_ID_RE.findall(header_text)]
    if not char_ids or "</hh:charProperties>" not in header_text:
        raise TemplateError("양식 header.xml 에 charProperties 가 없습니다.")
    if not fill_ids or "</hh:borderFills>" not in header_text:
        raise TemplateError("양식 header.xml 에 borderFills 가 없습니다.")

    base = max(char_ids) + 1
    fill_id = max(fill_ids) + 1

    # hwpx_writer 의 정의 순서와 동일: 일반/굵게/기울임/굵게기울임/h1/h2/h3
    new_chars = "".join([
        _char_pr(base + 0, 1000),
        _char_pr(base + 1, 1000, bold=True),
        _char_pr(base + 2, 1000, italic=True),
        _char_pr(base + 3, 1000, bold=True, italic=True),
        _char_pr(base + 4, 1600, bold=True),
        _char_pr(base + 5, 1400, bold=True),
        _char_pr(base + 6, 1200, bold=True),
    ])
    new_fill = (
        f'<hh:borderFill id="{fill_id}" threeD="0" shadow="0" centerLine="NONE" '
        f'breakCellSeparateLine="0">{_border("SOLID")}</hh:borderFill>'
    )

    out = header_text.replace("</hh:charProperties>", new_chars + "</hh:charProperties>")
    out = out.replace("</hh:borderFills>", new_fill + "</hh:borderFills>")
    out = _CHAR_PROPS_CNT_RE.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + 7}{m.group(3)}", out, count=1)
    out = _BORDER_FILLS_CNT_RE.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}", out, count=1)

    ids = StyleIds(
        normal=base, bold=base + 1, italic=base + 2, bold_italic=base + 3,
        h1=base + 4, h2=base + 5, h3=base + 6, table_border_fill=fill_id,
    )
    return out, ids
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add web/hwpx_template.py tests/test_hwpx_template.py
git commit -m "feat: inject renderer style defs into template header.xml"
```

---

### Task 4: 슬롯 치환 (`hwpx_template.py` — fill_template)

**Files:**
- Modify: `web/hwpx_template.py`
- Test: `tests/test_hwpx_template.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가:**

```python
def _section_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("Contents/section0.xml").decode("utf-8")


def test_fill_replaces_body_slot_and_keeps_surroundings(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, "서문\n\n{{본문}}\n\n맺음말\n")
    out = tmp_path / "filled.hwpx"
    fill_template(t, {"본문-1": "# 채운 제목\n채운 본문 **강조**\n"}, out)

    xml = _section_text(out)
    assert "{{본문}}" not in xml
    assert "채운 제목" in xml and "채운 본문" in xml
    assert "서문" in xml and "맺음말" in xml          # 주변 문단 보존
    from web.hwpx_writer import validate_hwpx
    validate_hwpx(out)


def test_fill_inherits_marker_paragraph_format_for_normal_text(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, "{{본문}}\n")
    out = tmp_path / "filled.hwpx"
    fill_template(t, {"본문-1": "일반 텍스트\n"}, out)
    xml = _section_text(out)
    # 스켈레톤 마커 문단의 charPr 은 0 → 일반 텍스트 run 은 0 을 상속
    assert '<hp:run charPrIDRef="0"><hp:t>일반 텍스트</hp:t></hp:run>' in xml


def test_fill_uses_injected_ids_for_bold_and_heading(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, "{{본문}}\n")
    out = tmp_path / "filled.hwpx"
    fill_template(t, {"본문-1": "# 제목\n**굵게**\n"}, out)
    xml = _section_text(out)
    # 스켈레톤 charPr 0~6 → 주입 베이스 7: 굵게=8, h1=11
    assert 'charPrIDRef="8"' in xml
    assert 'charPrIDRef="11"' in xml


def test_fill_replaces_fix_region_inclusive(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, (
        "유지되는 문단\n\n{{수정시작: 갱신}}\n\n낡은 내용 1\n\n낡은 내용 2\n\n{{수정끝}}\n\n뒤 문단\n"
    ))
    out = tmp_path / "filled.hwpx"
    fill_template(t, {"수정-1": "새 내용\n"}, out)
    xml = _section_text(out)
    assert "낡은 내용" not in xml and "수정시작" not in xml and "수정끝" not in xml
    assert "새 내용" in xml
    assert "유지되는 문단" in xml and "뒤 문단" in xml


def test_fill_missing_slot_raises(tmp_path: Path):
    from web.hwpx_template import TemplateError, fill_template

    t = make_template(tmp_path, "{{본문}}\n\n{{추가: x}}\n")
    with pytest.raises(TemplateError, match="추가-2"):
        fill_template(t, {"본문-1": "내용"}, tmp_path / "o.hwpx")


def test_fill_preserves_other_zip_entries(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, "{{본문}}\n")
    out = tmp_path / "filled.hwpx"
    fill_template(t, {"본문-1": "x"}, out)
    with zipfile.ZipFile(t) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        assert b.infolist()[0].filename == "mimetype"
        assert b.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert a.read("version.xml") == b.read("version.xml")  # 미변경 엔트리 보존


import shutil
import subprocess


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx 없음 — 로컬에서만 실행")
def test_filled_template_roundtrips_through_kordoc(tmp_path: Path):
    from web.hwpx_template import fill_template

    t = make_template(tmp_path, "양식 서문\n\n{{본문}}\n")
    out = tmp_path / "rt.hwpx"
    fill_template(t, {"본문-1": "# 치환 제목\n치환 본문 텍스트\n"}, out)
    completed = subprocess.run(
        ["npx", "-y", "kordoc", str(out), "--format", "json", "--silent"],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "양식 서문" in completed.stdout
    assert "치환 본문 텍스트" in completed.stdout
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py -v`
Expected: 신규 7건 FAIL — `ImportError: cannot import name 'fill_template'`

- [ ] **Step 3: 구현** — `web/hwpx_template.py` 에 추가 (상단 import 에 `import dataclasses`, `import io`, `from web.hwpx_writer import render_blocks, validate_hwpx` 추가):

```python
# ------------------------------------------------------------- fill

def _inherited_styles(marker_para: ET.Element, injected: StyleIds) -> StyleIds:
    """일반 텍스트는 마커 문단의 서식을 상속, 나머지는 주입된 정의를 쓴다."""
    para_pr = int(marker_para.get("paraPrIDRef", "0"))
    style_id = int(marker_para.get("styleIDRef", "0"))
    run = marker_para.find(f"{{{NS['hp']}}}run")
    char_pr = int(run.get("charPrIDRef", "0")) if run is not None else injected.normal
    return dataclasses.replace(injected, normal=char_pr, para_pr=para_pr, style=style_id)


def _fragment_to_elements(paras_xml: list[str]) -> list[ET.Element]:
    wrapper = f'<w xmlns:hp="{NS["hp"]}" xmlns:hc="{NS["hc"]}">{"".join(paras_xml)}</w>'
    return list(ET.fromstring(wrapper))


def _serialize_section(root: ET.Element) -> bytes:
    xml = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml).encode("utf-8")


def _rewrite_zip(src: Path, out_path: Path, replacements: dict[str, bytes]) -> None:
    """원본 엔트리 순서를 유지하며 일부 엔트리만 교체한 zip 을 새로 쓴다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = replacements.get(info.filename, zin.read(info.filename))
            if info.filename == "mimetype":
                zout.writestr(zipfile.ZipInfo("mimetype"), data,
                              compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(info.filename, data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buf.getvalue())


# 템플릿 기존 hp:p id 와의 충돌을 피하는 높은 시작값. 슬롯마다 1000 칸씩 띈다.
_SLOT_ID_BASE = 900001
_SLOT_ID_STRIDE = 1000


def fill_template(template_path: Path, slot_contents: dict[str, str], out_path: Path) -> None:
    """슬롯 내용(마크다운)을 양식의 자리표시자 위치에 치환해 out_path 에 쓴다."""
    _register_namespaces()
    template_path = Path(template_path)

    trees = _read_sections(template_path)
    found = _collect_slots(trees)

    missing = [f.slot.id for f in found if f.slot.id not in slot_contents]
    if missing:
        raise TemplateError(f"채워지지 않은 슬롯: {', '.join(missing)}")

    with zipfile.ZipFile(template_path) as zf:
        header_text = zf.read("Contents/header.xml").decode("utf-8")
    new_header, injected = _inject_styles(header_text)

    for i, f in enumerate(found):
        styles = _inherited_styles(f.start, injected)
        paras_xml = render_blocks(
            slot_contents[f.slot.id],
            styles=styles,
            start_id=_SLOT_ID_BASE + i * _SLOT_ID_STRIDE,
        )
        elements = _fragment_to_elements(paras_xml)

        children = list(f.parent)
        i0 = children.index(f.start)
        if f.end is not None:  # 수정 구간: 시작~끝 문단을 통째로 제거
            i1 = children.index(f.end)
            for el in children[i0:i1 + 1]:
                f.parent.remove(el)
        else:
            f.parent.remove(f.start)
        for offset, el in enumerate(elements):
            f.parent.insert(i0 + offset, el)

    replacements: dict[str, bytes] = {"Contents/header.xml": new_header.encode("utf-8")}
    for entry, root in trees.items():
        replacements[entry] = _serialize_section(root)

    _rewrite_zip(template_path, out_path, replacements)
    validate_hwpx(out_path)
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/test_hwpx_template.py -v` → 전부 PASS (라운드트립 포함)
Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS

- [ ] **Step 5: 실파일 수동 검증 (필수 게이트)**

```bash
./venv/bin/python -c "
from pathlib import Path
from web.hwpx_writer import markdown_to_hwpx
from web.hwpx_template import fill_template
markdown_to_hwpx('보고서 양식 서문\n\n{{본문}}\n\n끝.\n', Path('/tmp/t2_template.hwpx'))
fill_template(Path('/tmp/t2_template.hwpx'), {'본문-1': '# 제목\n표 포함:\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n'}, Path('/tmp/t2_filled.hwpx'))
print('OK: /tmp/t2_filled.hwpx')
"
npx -y kordoc /tmp/t2_filled.hwpx --format json --silent | head -c 300
```

Expected: OK + kordoc `"success": true`. 가능하면 `/tmp/t2_filled.hwpx` 를 한글에서 열어 확인. **이 게이트를 통과하기 전에 다음 Task 로 넘어가지 않는다.** 안 열리면: 치환 전/후 section XML 을 `unzip -p ... | xmllint --format -` 으로 diff 해 ET 재직렬화가 깨뜨린 부분(접두사/속성)을 찾는다.

- [ ] **Step 6: Commit**

```bash
git add web/hwpx_template.py tests/test_hwpx_template.py
git commit -m "feat: fill template slots with rendered markdown, preserving zip"
```

---

### Task 5: 슬롯 프롬프트와 출력 파싱 (`codex_runner.py`)

**Files:**
- Modify: `web/codex_runner.py`
- Test: `tests/test_codex_runner.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_codex_runner.py 에 append:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_codex_runner.py -v`
Expected: 신규 FAIL — `cannot import name 'parse_slot_output'` 등

- [ ] **Step 3: 구현** — web/codex_runner.py 수정:

`build_prompt` 를 다음으로 교체하고 상수·함수 추가:

```python
TEMPLATE_OUTPUT_SPEC = (
    "출력 형식: 아래 모든 슬롯에 대해, 반드시 다음 형식의 블록만 출력하라.\n"
    "===SLOT: <슬롯ID>===\n"
    "(해당 슬롯에 들어갈 한국어 Markdown 내용)\n"
    "===END===\n"
    "모든 슬롯을 빠짐없이 채우고, 블록 밖에는 어떤 텍스트도 쓰지 마라."
)


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


class SlotOutputError(RuntimeError):
    pass


_SLOT_OUTPUT_RE = re.compile(r"===SLOT:\s*(.+?)\s*===\n(.*?)===END===", re.S)


def parse_slot_output(md_text: str, expected_ids: list[str]) -> dict[str, str]:
    """codex 의 슬롯 구조 출력을 {슬롯ID: 마크다운} 으로 파싱하고 누락을 검증한다."""
    contents = {sid.strip(): body.strip() for sid, body in _SLOT_OUTPUT_RE.findall(md_text)}
    missing = [sid for sid in expected_ids if sid not in contents]
    if missing:
        raise SlotOutputError(f"채워지지 않은 슬롯: {', '.join(missing)}")
    return contents
```

(파일 상단에 `import re` 가 없으면 추가.) `run_codex` 시그니처에 `template_md: str | None = None, slots: list | None = None` 를 `output_type` 다음에 추가하고, 첫 줄을 `prompt = build_prompt(request_text, output_type, template_md=template_md, slots=slots)` 로 변경. 다른 동작 변경 없음.

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add web/codex_runner.py tests/test_codex_runner.py
git commit -m "feat: slot-structured prompt and output parsing for template jobs"
```

---

### Task 6: `job_manager` 양식 보관 + `worker` 양식 분기

**Files:**
- Modify: `web/job_manager.py`, `web/worker.py`
- Test: `tests/test_job_manager.py`, `tests/test_worker.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가**

tests/test_job_manager.py 에 append:

```python
def test_create_with_template_saves_file(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(
        uploads=[("a.hwp", b"x")],
        request_text="r",
        output_type="report",
        template=("양식.hwpx", b"PK-bytes"),
    )
    assert job.template_path is not None
    assert job.template_path.read_bytes() == b"PK-bytes"
    assert job.template_path.parent == job.dir / "template"


def test_create_without_template_defaults_none(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(uploads=[("a.hwp", b"x")], request_text="r")
    assert job.template_path is None
```

tests/test_worker.py 에 append (기존 페이크들 아래):

```python
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

    def fake_convert(upload_paths, converted_root):
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

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=spy_codex,
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

    def boom_parse(md, ids):
        raise RuntimeError("채워지지 않은 슬롯: 본문-1")

    def unused(*a, **k):
        raise AssertionError("호출되면 안 됨")

    fns = TemplateFns(scan=lambda p: [FakeSlot("본문-1")], parse_slots=boom_parse, fill=unused)

    run_job(job, mgr, convert_fn=fake_convert, codex_fn=fake_codex,
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
```

주의: 기존 `fake_codex` 페이크는 `**kwargs` 를 받으므로 새 키워드(template_md/slots)가 넘어와도 깨지지 않는다. 확인 후 필요하면 시그니처에 `template_md=None, slots=None` 을 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_job_manager.py tests/test_worker.py -v`
Expected: 신규 FAIL (`create() got an unexpected keyword argument 'template'`, `cannot import name 'TemplateFns'`)

- [ ] **Step 3: job_manager 구현**

`Job` 데이터클래스에 `template_path: Path | None = None` 추가 (output_type 다음). `create` 시그니처에 `template: tuple[str, bytes] | None = None` 추가하고, 본문에서 request.txt 저장 다음에:

```python
        template_path: Path | None = None
        if template is not None:
            t_name, t_bytes = template
            template_dir = job_dir / "template"
            template_dir.mkdir(parents=True, exist_ok=True)
            template_path = template_dir / t_name
            template_path.write_bytes(t_bytes)
```

`Job(...)` 생성에 `template_path=template_path,` 추가.

- [ ] **Step 4: worker 구현** — `web/worker.py` 전체를 다음으로 교체:

```python
"""한 잡의 변환→분석→HWPX 생성 오케스트레이션."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from web.job_manager import Job, JobManager, JobState

ConvertFn = Callable[..., object]   # (upload_paths, converted_root) -> list[doc_dir]
CodexFn = Callable[..., None]       # (converted_dir, request_text, report_path, output_type, on_event, template_md=, slots=, **kw)
HwpxFn = Callable[..., None]        # (report_path, result_path) -> None


@dataclass
class TemplateFns:
    """양식 잡에 필요한 함수 묶음 (테스트에서 페이크로 대체)."""
    scan: Callable[..., list]          # (template_path) -> list[Slot]
    parse_slots: Callable[..., dict]   # (report_md_text, expected_ids) -> {id: md}
    fill: Callable[..., None]          # (template_path, slot_contents, out_path) -> None


def run_job(
    job: Job,
    manager: JobManager,
    convert_fn: ConvertFn,
    codex_fn: CodexFn,
    hwpx_fn: HwpxFn,
    template_fns: TemplateFns | None = None,
) -> None:
    """블로킹 함수. 웹 레이어는 스레드풀에서 호출한다."""
    use_template = job.template_path is not None and template_fns is not None
    slots = None
    template_md = ""

    try:
        manager.set_state(job, JobState.CONVERTING, step="문서 변환 중")
        convert_fn(job.upload_paths, job.converted_dir)
        if use_template:
            slots = template_fns.scan(job.template_path)
            [tdir] = convert_fn([job.template_path], job.dir / "template_converted")
            template_md = (Path(tdir) / "document.md").read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 단계별 실패를 잡 상태로 기록
        manager.set_state(job, JobState.FAILED, error=f"변환 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.ANALYZING, step="codex 분석 중")
        codex_fn(
            converted_dir=job.converted_dir,   # 루트: 그 아래 docid 폴더 N개
            request_text=job.request_text,
            report_path=job.report_path,
            output_type=job.output_type,
            on_event=lambda event: manager.push_event(job, event),
            template_md=template_md or None,
            slots=slots,
        )
        if use_template:
            report_md = job.report_path.read_text(encoding="utf-8")
            slot_contents = template_fns.parse_slots(report_md, [s.id for s in slots])
    except Exception as exc:  # noqa: BLE001
        manager.set_state(job, JobState.FAILED, error=f"분석 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.GENERATING, step="한글 파일 생성 중")
        if use_template:
            template_fns.fill(job.template_path, slot_contents, job.result_path)
        else:
            hwpx_fn(job.report_path, job.result_path)
    except Exception as exc:  # noqa: BLE001
        manager.set_state(
            job,
            JobState.FAILED,
            error=f"한글 파일 생성 실패: {exc} — 리포트 내용은 화면에서 확인할 수 있습니다.",
        )
        job.events.put({"type": "end"})
        return

    manager.set_state(job, JobState.DONE, step="완료")
    job.events.put({"type": "end"})
```

주의: 비양식 잡에서 `codex_fn` 에 `template_md=None, slots=None` 키워드가 추가로 넘어간다 — 기존 테스트 페이크들은 `**kwargs` 를 받아 문제없지만, 실패하는 페이크가 있으면 `**kwargs` 를 추가한다(테스트 로직은 그대로).

- [ ] **Step 5: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS

- [ ] **Step 6: Commit**

```bash
git add web/job_manager.py web/worker.py tests/test_job_manager.py tests/test_worker.py
git commit -m "feat: store template per job and branch worker through TemplateFns"
```

---

### Task 7: `app.py` 양식 업로드 + 검증

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_app.py` (추가)

- [ ] **Step 1: 실패하는 테스트 추가** — tests/test_app.py 에 append:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest tests/test_app.py -v`
Expected: 신규 4건 FAIL (template 필드가 무시되거나 422)

- [ ] **Step 3: 구현** — web/app.py 수정:

import 추가:

```python
import io

from web.hwpx_template import TemplateError, fill_template, scan_placeholders
from web.codex_runner import parse_slot_output, run_codex
from web.worker import TemplateFns, run_job
```

모듈 레벨에 배선 추가 (get_runner 정의 아래):

```python
TEMPLATE_FNS = TemplateFns(scan=scan_placeholders, parse_slots=parse_slot_output, fill=fill_template)
```

`create_job` 시그니처에 `template: UploadFile | None = File(None),` 추가 (files 다음). 본문의 `job = get_manager().create(...)` 직전에:

```python
    template_tuple: tuple[str, bytes] | None = None
    if template is not None and template.filename:
        t_name = Path(template.filename).name or "template.hwpx"
        if not t_name.lower().endswith(".hwpx"):
            raise HTTPException(status_code=400, detail="양식은 .hwpx 파일이어야 합니다.")
        t_bytes = await template.read()
        try:
            scan_placeholders(io.BytesIO(t_bytes))
        except TemplateError as exc:
            raise HTTPException(status_code=400, detail=f"양식 검증 실패: {exc}")
        template_tuple = (t_name, t_bytes)
```

create 호출과 submit 을 다음으로 교체:

```python
    job = get_manager().create(
        uploads=uploads, request_text=request_text,
        output_type=output_type, template=template_tuple,
    )
    get_runner().submit(
        run_job, job, get_manager(), convert_many, run_codex, write_hwpx, TEMPLATE_FNS
    )
```

주의(모킹 경계): 기존 테스트는 `app_module.convert_many` 등을 monkeypatch 한다. `TEMPLATE_FNS` 는 실제 함수를 묶지만, 비양식 잡에서는 호출되지 않으므로(worker 의 `use_template` 분기) 기존 테스트에 영향이 없다. 양식 잡의 라우트 테스트(`test_template_upload_accepted_and_saved`)는 페이크 codex 가 슬롯 형식이 아닌 일반 마크다운을 쓰므로 잡 자체는 분석 단계에서 실패할 수 있지만, 이 테스트는 200 응답과 파일 저장만 검증한다 — done 까지 기다리지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_app.py
git commit -m "feat: optional template upload with placeholder validation at submit"
```

---

### Task 8: 프런트엔드 + README + E2E 최종 검증

**Files:**
- Modify: `web/static/index.html`, `README.md`

- [ ] **Step 1: 폼에 양식 첨부 추가** — `index.html` 의 산출물 종류 라디오 블록 다음에:

```html
    <label>양식 파일 (선택, .hwpx)
      <input type="file" id="template" accept=".hwpx" />
    </label>
    <small style="display:block;color:#666;margin-top:0.25rem">
      양식 안에 <code>{{본문}}</code>, <code>{{추가: 지시}}</code>,
      <code>{{수정시작: 지시}}</code> ~ <code>{{수정끝}}</code> 자리표시자를
      한 줄에 하나씩 넣어 두면 그 위치에 생성 내용이 채워집니다.
    </small>
```

submit 핸들러의 FormData 구간에 추가:

```javascript
      const tpl = document.getElementById('template').files[0];
      if (tpl) fd.append('template', tpl);
```

- [ ] **Step 2: README 에 규약 문서화** — 웹앱 사용법 섹션에 추가:

```markdown
### 양식(.hwpx) 자리표시자 규약

양식 파일을 함께 올리면 생성 내용이 양식의 지정 위치에 채워집니다.
자리표시자는 **한 줄(문단)에 하나씩 단독으로** 입력합니다.

| 마커 | 동작 |
| --- | --- |
| `{{본문}}` | 그 자리에 생성 본문 전체 삽입 |
| `{{추가: 지시}}` | 그 자리에 지시에 맞는 새 내용 삽입 |
| `{{수정시작: 지시}}` … `{{수정끝}}` | 사이의 기존 내용을 지시대로 다시 써서 통째로 교체 |

서식은 자리표시자 문단의 글자/문단 모양을 상속합니다(굵게·제목·표 테두리는
기본 서식). 규약 위반(마커 없음, 짝 안 맞는 수정 마커 등)은 업로드 즉시
한국어 메시지로 반려됩니다.
```

- [ ] **Step 3: 전체 테스트 + 정적 점검**

Run: `./venv/bin/python -m pytest tests/ -v` → 전체 PASS.
index.html 재독: 모든 getElementById 대상 존재, 중복 id 없음 확인.

- [ ] **Step 4: E2E 수동 검증 (2단계 완료 조건)**

codex 인증이 있는 로컬에서:

```bash
./venv/bin/python -m uvicorn web.app:app --port 8000 --reload
```

1. 한글에서 실제 부서 양식(또는 빈 문서)에 `{{본문}}` 과 `{{수정시작: …}}~{{수정끝}}` 을 입력해 .hwpx 로 저장
2. 브라우저에서 원본 파일들 + 이 양식 업로드 → done 도달 확인
3. 다운로드한 HWPX 를 **실제 한글에서 열어** ① 양식의 기존 서식·내용 보존 ② 슬롯 위치에 내용 삽입 ③ 수정 구간 교체를 확인
4. 마커 없는 양식, `{{수정끝}}` 없는 양식을 올려 한국어 400 메시지 확인

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html README.md
git commit -m "feat: template attachment UI and placeholder convention docs"
```

---

## Self-Review 결과

- **스펙 커버리지(§3.2~3.4, §5 2단계, §6, §7):** 자리표시자 3종 + 단독 문단 규약(Task 2), run 합치기(Task 2 split-run 테스트), 슬롯 ID 등장 순서 부여(Task 2), 양식 맥락 마크다운 제공(Task 6 worker가 convert_fn 재사용), 슬롯 구조 출력·누락 검증(Task 5, 분석 단계 실패 처리 Task 6), fill_template 서식 상속(Task 4), 업로드 시점 400 검증 — 마커 0개/짝 불일치/비 HWPX(Task 2 에러 + Task 7 라우트), 양식↔산출물 직교(worker 분기가 output_type 과 독립), zip 정합성 자체 검증(Task 4 의 validate_hwpx 호출), kordoc 라운드트립(Task 4)·실양식 수동 게이트(Task 8). 스펙의 "표 칸 안 마커"는 _collect_slots 가 부모 무관하게 동작하므로 지원됨(수정 구간은 같은 부모 강제).
- **타입 일관성:** `Slot(id, kind, instruction, original_text)` — Task 2 정의를 5(프롬프트)·6(worker)·7(app) 이 동일하게 사용. `TemplateFns(scan, parse_slots, fill)` 시그니처가 Task 6 정의·테스트·Task 7 배선에서 일치. `render_blocks(md, styles, start_id)` — Task 1 정의를 Task 4 가 동일 시그니처로 호출. `fill_template(template_path, slot_contents, out_path)` 일관.
- **알려진 리스크:** ET 재직렬화가 실제 한글 양식의 미등록 네임스페이스 접두사를 바꿀 수 있음 — Task 4 의 kordoc 게이트와 Task 8 의 실양식 수동 게이트가 안전망. 실양식에서 깨지면 누락 접두사를 `NS` 에 추가하는 것이 1차 대응.
