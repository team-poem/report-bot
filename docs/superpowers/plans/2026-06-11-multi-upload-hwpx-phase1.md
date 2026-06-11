# 다중 업로드 + 기본 서식 HWPX (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 여러 파일을 한 잡에 업로드하고, 산출물 종류(분석 리포트/취합 문서)를 선택하면, 결과를 HTML 미리보기 + 한글에서 열리는 HWPX 파일로 받는다.

**Architecture:** Codex는 지금처럼 마크다운만 출력하고, 신규 결정적 변환기(`web/hwpx_writer.py`)가 마크다운 → OWPML XML → zip(.hwpx)을 만든다. 잡 상태 머신에 `generating` 단계가 추가되고(`queued → converting → analyzing → generating → done/failed`), HWPX 생성이 실패해도 HTML 미리보기는 유지된다. 스펙: `docs/superpowers/specs/2026-06-11-multi-upload-hwpx-design.md` (이 계획은 1단계만 다룬다. 양식/자리표시자는 2단계 계획에서).

**Tech Stack:** FastAPI, pytest, 표준 라이브러리만으로 HWPX 생성(zipfile + 문자열 XML 템플릿). 새 의존성 없음.

**스펙과 다른 구현 디테일 1건:** 스펙의 `web/assets/skeleton.hwpx`(바이너리 자산)는 **코드 내장 XML 템플릿**으로 구현한다. 같은 역할이며, diff 가능하고 테스트하기 쉽다.

**리스크와 배치:** 가장 불확실한 부분은 "우리가 만든 HWPX를 한글이 여는가"이다. 그래서 Task 1이 빈 HWPX 패키지 생성이고, 그 시점에 kordoc 라운드트립 + (가능하면) 실제 한글 열기로 검증한다. 안 열리면 한글에서 빈 문서를 .hwpx로 저장한 파일과 `unzip -d`로 풀어 XML을 diff해 부족한 요소를 채운다 — 이 디버깅은 Task 1 안에서 끝내고 넘어간다.

## File Structure

```
web/
  md_blocks.py        (신설) 마크다운 부분집합 파서 — 블록/인라인 구조만, XML 무지
  hwpx_writer.py      (신설) 블록 → OWPML XML → zip. validate_hwpx, write_hwpx, CLI
  pipeline_runner.py  (수정) convert_many 추가, SUPPORTED_EXTENSIONS 재노출
  job_manager.py      (수정) upload_paths 복수화, output_type, GENERATING, result_path
  codex_runner.py     (수정) 프롬프트 report/merge 분기
  worker.py           (수정) generating 단계 + 폴백
  app.py              (수정) 다중 업로드, 검증, /hwpx 다운로드
  static/index.html   (수정) 다중 선택, 산출물 라디오, HWPX 다운로드 버튼
tests/
  test_md_blocks.py   (신설)
  test_hwpx_writer.py (신설)
  (기존 테스트들은 시그니처 변경에 맞춰 수정)
```

---

### Task 1: HWPX 패키지 골격 — 빈 문서 생성

**Files:**
- Create: `web/hwpx_writer.py`
- Test: `tests/test_hwpx_writer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_hwpx_writer.py
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from web.hwpx_writer import package_hwpx, validate_hwpx

REQUIRED_ENTRIES = [
    "mimetype",
    "version.xml",
    "META-INF/container.xml",
    "META-INF/manifest.xml",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
    "settings.xml",
]


def _empty_doc(tmp_path: Path) -> Path:
    out = tmp_path / "empty.hwpx"
    out.write_bytes(package_hwpx(body_paras=[]))
    return out


def test_package_zip_layout(tmp_path: Path):
    out = _empty_doc(tmp_path)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        for entry in REQUIRED_ENTRIES:
            assert entry in names
        # OCF 규약: mimetype 이 첫 엔트리이고 비압축(STORED)이어야 한다
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/hwp+zip"


def test_all_xml_entries_are_well_formed(tmp_path: Path):
    out = _empty_doc(tmp_path)
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            if name.endswith((".xml", ".hpf")):
                ET.fromstring(zf.read(name))  # 깨진 XML 이면 예외


def test_header_defines_char_properties_0_to_6(tmp_path: Path):
    out = _empty_doc(tmp_path)
    with zipfile.ZipFile(out) as zf:
        root = ET.fromstring(zf.read("Contents/header.xml"))
    ns = {"hh": "http://www.hancom.co.kr/hwpml/2011/head"}
    ids = {pr.get("id") for pr in root.findall(".//hh:charPr", ns)}
    assert ids == {"0", "1", "2", "3", "4", "5", "6"}


def test_validate_hwpx_accepts_good_and_rejects_broken(tmp_path: Path):
    out = _empty_doc(tmp_path)
    validate_hwpx(out)  # 정상 파일은 예외 없음

    broken = tmp_path / "broken.hwpx"
    broken.write_bytes(b"not a zip")
    import pytest
    from web.hwpx_writer import HwpxError
    with pytest.raises(HwpxError):
        validate_hwpx(broken)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_hwpx_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.hwpx_writer'`

- [ ] **Step 3: 구현 — 템플릿 + 패키징**

`web/hwpx_writer.py` 생성:

```python
"""마크다운 → HWPX(OWPML zip) 변환기.

스켈레톤은 바이너리 자산 없이 코드 내장 XML 템플릿으로 관리한다.
지원 요소: 제목(h1-h3), 문단, 표, 목록(텍스트 접두사 렌더), 굵게/기울임.
이 범위를 벗어나는 마크다운은 일반 텍스트로 강등하되 내용은 보존한다.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


class HwpxError(RuntimeError):
    pass


MIMETYPE = b"application/hwp+zip"

_VERSION_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
    'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" buildNumber="0" '
    'os="1" xmlVersion="1.4" application="report-bot" appVersion="1.0"/>\n'
)

_CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <ocf:rootfiles>\n'
    '    <ocf:rootfile full-path="Contents/content.hpf" '
    'media-type="application/hwpml-package+xml"/>\n'
    '  </ocf:rootfiles>\n'
    '</ocf:container>\n'
)

_MANIFEST_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">\n'
    '  <odf:file-entry odf:full-path="/" odf:media-type="application/hwp+zip"/>\n'
    '  <odf:file-entry odf:full-path="version.xml" odf:media-type="text/xml"/>\n'
    '  <odf:file-entry odf:full-path="Contents/header.xml" odf:media-type="text/xml"/>\n'
    '  <odf:file-entry odf:full-path="Contents/section0.xml" odf:media-type="text/xml"/>\n'
    '  <odf:file-entry odf:full-path="settings.xml" odf:media-type="text/xml"/>\n'
    '</odf:manifest>\n'
)

_CONTENT_HPF = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="" '
    'unique-identifier="" id="">\n'
    '  <opf:metadata><opf:title>report-bot 문서</opf:title>'
    '<opf:language>ko</opf:language></opf:metadata>\n'
    '  <opf:manifest>\n'
    '    <opf:item id="settings" href="settings.xml" media-type="application/xml"/>\n'
    '    <opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>\n'
    '    <opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>\n'
    '  </opf:manifest>\n'
    '  <opf:spine>\n'
    '    <opf:itemref idref="header" linear="yes"/>\n'
    '    <opf:itemref idref="section0" linear="yes"/>\n'
    '  </opf:spine>\n'
    '</opf:package>\n'
)

_SETTINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app">\n'
    '  <ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/>\n'
    '</ha:HWPApplicationSetting>\n'
)

# ---------------------------------------------------------------- header.xml

def _font_faces() -> str:
    langs = ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
    faces = "".join(
        f'<hh:fontface lang="{lang}" fontCnt="1">'
        '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/>'
        "</hh:fontface>"
        for lang in langs
    )
    return f'<hh:fontfaces itemCnt="{len(langs)}">{faces}</hh:fontfaces>'


def _border(kind: str) -> str:
    return (
        f'<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
        f'<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        f'<hh:leftBorder type="{kind}" width="0.12 mm" color="#000000"/>'
        f'<hh:rightBorder type="{kind}" width="0.12 mm" color="#000000"/>'
        f'<hh:topBorder type="{kind}" width="0.12 mm" color="#000000"/>'
        f'<hh:bottomBorder type="{kind}" width="0.12 mm" color="#000000"/>'
        f'<hh:diagonal type="SOLID" width="0.12 mm" color="#000000"/>'
    )


def _border_fills() -> str:
    return (
        '<hh:borderFills itemCnt="2">'
        f'<hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE" '
        f'breakCellSeparateLine="0">{_border("NONE")}</hh:borderFill>'
        f'<hh:borderFill id="2" threeD="0" shadow="0" centerLine="NONE" '
        f'breakCellSeparateLine="0">{_border("SOLID")}</hh:borderFill>'
        "</hh:borderFills>"
    )


def _char_pr(pr_id: int, height: int, bold: bool = False, italic: bool = False) -> str:
    marks = ("<hh:bold/>" if bold else "") + ("<hh:italic/>" if italic else "")
    return (
        f'<hh:charPr id="{pr_id}" height="{height}" textColor="#000000" shadeColor="none" '
        'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">'
        '<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" '
        'symbol="100" user="100"/>'
        '<hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" '
        'symbol="100" user="100"/>'
        '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        f"{marks}</hh:charPr>"
    )


# charPr id 배정표 — md_blocks 의 Span/Heading 과 매핑된다 (Task 3)
#   0 본문 / 1 굵게 / 2 기울임 / 3 굵게+기울임 / 4 h1 / 5 h2 / 6 h3
def _char_properties() -> str:
    prs = [
        _char_pr(0, 1000),
        _char_pr(1, 1000, bold=True),
        _char_pr(2, 1000, italic=True),
        _char_pr(3, 1000, bold=True, italic=True),
        _char_pr(4, 1600, bold=True),
        _char_pr(5, 1400, bold=True),
        _char_pr(6, 1200, bold=True),
    ]
    return f'<hh:charProperties itemCnt="{len(prs)}">{"".join(prs)}</hh:charProperties>'


def _para_properties() -> str:
    return (
        '<hh:paraProperties itemCnt="1">'
        '<hh:paraPr id="0" tabPrIDRef="0" condense="0" fontLineHeight="0" '
        'snapToGrid="1" suppressLineNumbers="0" checked="0">'
        '<hh:align horizontal="JUSTIFY" vertical="BASELINE"/>'
        '<hh:heading type="NONE" idRef="0" level="0"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" '
        'widowOrphan="0" keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
        '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
        '<hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
        '<hc:left value="0" unit="HWPUNIT"/><hc:right value="0" unit="HWPUNIT"/>'
        '<hc:prev value="0" unit="HWPUNIT"/><hc:next value="0" unit="HWPUNIT"/></hh:margin>'
        '<hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>'
        '<hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" '
        'offsetTop="0" offsetBottom="0" connect="0" ignoreMargin="0"/>'
        "</hh:paraPr></hh:paraProperties>"
    )


def _header_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" version="1.4" secCnt="1">'
        '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>'
        "<hh:refList>"
        + _font_faces()
        + _border_fills()
        + _char_properties()
        + _para_properties()
        + '<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" '
        'engName="Normal" paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" '
        'langID="1042" lockForm="0"/></hh:styles>'
        "</hh:refList></hh:head>"
    )


# --------------------------------------------------------------- section0.xml

# A4 세로. HWPUNIT = pt/100. 본문 폭 = 59528 - 8504*2 = 42520
PAGE_TEXT_WIDTH = 42520

_SEC_PR = (
    '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" '
    'tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" '
    'textVerticalWidthHead="0" masterPageCnt="0">'
    '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" '
    'border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" '
    'showLineNumber="0"/>'
    '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504" '
    'top="5668" bottom="4252"/></hp:pagePr>'
    "</hp:secPr>"
)

_FIRST_PARA = (
    '<hp:p id="1" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="0">'
    + _SEC_PR
    + '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
    'sameSz="1" sameGap="0"/></hp:ctrl>'
    "<hp:t/></hp:run></hp:p>"
)


def _section_xml(body_paras: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">'
        + _FIRST_PARA
        + "".join(body_paras)
        + "</hs:sec>"
    )


# ------------------------------------------------------------------ packaging

def package_hwpx(body_paras: list[str]) -> bytes:
    """본문 문단 XML 조각들을 받아 완전한 HWPX zip 바이트를 만든다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # OCF 규약: mimetype 은 첫 엔트리 + 비압축
        zf.writestr(
            zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED
        )
        zf.writestr("version.xml", _VERSION_XML)
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("META-INF/manifest.xml", _MANIFEST_XML)
        zf.writestr("Contents/content.hpf", _CONTENT_HPF)
        zf.writestr("Contents/header.xml", _header_xml())
        zf.writestr("Contents/section0.xml", _section_xml(body_paras))
        zf.writestr("settings.xml", _SETTINGS_XML)
    return buf.getvalue()


def validate_hwpx(path: Path) -> None:
    """zip 구조와 XML 정합성을 검증한다. 문제가 있으면 HwpxError."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise HwpxError(f"{path.name}: zip 형식이 아닙니다.")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        required = [
            "mimetype", "version.xml", "META-INF/container.xml",
            "Contents/content.hpf", "Contents/header.xml", "Contents/section0.xml",
        ]
        missing = [n for n in required if n not in names]
        if missing:
            raise HwpxError(f"필수 엔트리 누락: {', '.join(missing)}")
        if zf.read("mimetype") != MIMETYPE:
            raise HwpxError("mimetype 내용이 올바르지 않습니다.")
        for name in names:
            if name.endswith((".xml", ".hpf")):
                try:
                    ET.fromstring(zf.read(name))
                except ET.ParseError as exc:
                    raise HwpxError(f"{name}: XML 파싱 실패 — {exc}") from exc
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_hwpx_writer.py -v`
Expected: 4 PASS

- [ ] **Step 5: kordoc 라운드트립으로 빈 문서 검증 (수동, 필수)**

```bash
python -c "
from pathlib import Path
from web.hwpx_writer import package_hwpx
Path('/tmp/empty.hwpx').write_bytes(package_hwpx([]))
print('written /tmp/empty.hwpx')
"
npx -y kordoc /tmp/empty.hwpx --format json --silent | head -c 500
```

Expected: kordoc이 에러 없이 JSON을 출력(내용은 비어 있어도 됨). 가능하면 `/tmp/empty.hwpx`를 한글(또는 한컴독스)에서 열어 확인.

**안 열리는 경우의 디버깅 절차:** 한글에서 빈 문서를 `.hwpx`로 저장 → `unzip -d ref ref.hwpx; unzip -d ours /tmp/empty.hwpx` → 파일 목록과 `xmllint --format`으로 정돈한 XML을 diff → 부족한 엔트리/속성을 템플릿에 보충하고 Step 4부터 반복. 이 검증을 통과하기 전에는 다음 Task로 넘어가지 않는다.

- [ ] **Step 6: Commit**

```bash
git add web/hwpx_writer.py tests/test_hwpx_writer.py
git commit -m "feat: add HWPX package skeleton with empty document generation"
```

---

### Task 2: 마크다운 블록 파서 (`md_blocks.py`)

**Files:**
- Create: `web/md_blocks.py`
- Test: `tests/test_md_blocks.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_md_blocks.py
from web.md_blocks import (
    Heading, ListBlock, Paragraph, Span, Table, parse_blocks, parse_inline,
)


def test_parse_inline_bold_italic_mix():
    spans = parse_inline("일반 **굵게** 와 *기울임* 그리고 ***둘다***")
    assert spans == [
        Span("일반 "),
        Span("굵게", bold=True),
        Span(" 와 "),
        Span("기울임", italic=True),
        Span(" 그리고 "),
        Span("둘다", bold=True, italic=True),
    ]


def test_headings_h1_to_h3_and_h4_degrades_to_paragraph():
    blocks = parse_blocks("# 제목1\n## 제목2\n### 제목3\n#### 제목4\n")
    assert blocks[0] == Heading(1, [Span("제목1")])
    assert blocks[1] == Heading(2, [Span("제목2")])
    assert blocks[2] == Heading(3, [Span("제목3")])
    # h4 이하는 지원 범위 밖 → 내용 보존하며 문단으로 강등
    assert blocks[3] == Paragraph([Span("제목4")])


def test_paragraph_joins_adjacent_lines_until_blank():
    blocks = parse_blocks("첫 줄\n둘째 줄\n\n새 문단\n")
    assert blocks == [
        Paragraph([Span("첫 줄 둘째 줄")]),
        Paragraph([Span("새 문단")]),
    ]


def test_unordered_and_ordered_lists():
    blocks = parse_blocks("- 하나\n- 둘\n\n1. 첫째\n2. 둘째\n")
    assert blocks[0] == ListBlock(ordered=False, items=[[Span("하나")], [Span("둘")]])
    assert blocks[1] == ListBlock(ordered=True, items=[[Span("첫째")], [Span("둘째")]])


def test_table_with_header_separator():
    md = "| 학과 | 정원 |\n| --- | --- |\n| 컴공 | 40 |\n| 수학 | 30 |\n"
    blocks = parse_blocks(md)
    assert blocks == [
        Table(rows=[
            [[Span("학과")], [Span("정원")]],
            [[Span("컴공")], [Span("40")]],
            [[Span("수학")], [Span("30")]],
        ], has_header=True)
    ]


def test_code_fence_degrades_to_plain_paragraphs():
    blocks = parse_blocks("```python\nx = 1\n```\n")
    # 코드 블록은 서식 없이 내용만 보존
    assert blocks == [Paragraph([Span("x = 1")])]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_md_blocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.md_blocks'`

- [ ] **Step 3: 구현**

`web/md_blocks.py` 생성:

```python
"""마크다운 부분집합 파서: 제목(h1-h3)/문단/표/목록/굵게·기울임.

hwpx_writer 가 소비하는 중간 표현만 만든다. XML 은 모른다.
지원 범위 밖 요소(h4+, 코드펜스 등)는 내용을 보존한 채 문단으로 강등한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_md_blocks.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add web/md_blocks.py tests/test_md_blocks.py
git commit -m "feat: add markdown subset parser for HWPX generation"
```

---

### Task 3: 블록 → 본문 XML + `markdown_to_hwpx` + CLI

**Files:**
- Modify: `web/hwpx_writer.py`
- Test: `tests/test_hwpx_writer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_hwpx_writer.py`에 추가:

```python
def _section_of(md: str, tmp_path: Path) -> str:
    from web.hwpx_writer import markdown_to_hwpx
    out = tmp_path / "doc.hwpx"
    markdown_to_hwpx(md, out)
    with zipfile.ZipFile(out) as zf:
        return zf.read("Contents/section0.xml").decode("utf-8")


def test_markdown_heading_uses_heading_char_pr(tmp_path: Path):
    xml = _section_of("# 큰제목\n본문입니다\n", tmp_path)
    assert 'charPrIDRef="4"' in xml          # h1 → charPr 4
    assert "큰제목" in xml
    assert "본문입니다" in xml


def test_markdown_bold_split_into_separate_run(tmp_path: Path):
    xml = _section_of("앞 **강조** 뒤\n", tmp_path)
    assert 'charPrIDRef="1"' in xml          # 굵게 run
    assert "강조" in xml


def test_markdown_table_renders_tbl_with_cells(tmp_path: Path):
    xml = _section_of("| 학과 | 정원 |\n| --- | --- |\n| 컴공 | 40 |\n", tmp_path)
    assert "<hp:tbl" in xml and 'rowCnt="2"' in xml and 'colCnt="2"' in xml
    assert xml.count("<hp:tc") == 4
    assert "컴공" in xml


def test_markdown_list_renders_prefixed_paragraphs(tmp_path: Path):
    xml = _section_of("- 하나\n- 둘\n\n1. 첫째\n", tmp_path)
    assert "• 하나" in xml and "• 둘" in xml
    assert "1. 첫째" in xml


def test_xml_special_chars_escaped(tmp_path: Path):
    xml = _section_of("a < b & c > d\n", tmp_path)
    assert "a &lt; b &amp; c &gt; d" in xml


def test_generated_doc_passes_validate(tmp_path: Path):
    from web.hwpx_writer import markdown_to_hwpx, validate_hwpx
    out = tmp_path / "doc.hwpx"
    markdown_to_hwpx("# 제목\n| a | b |\n| --- | --- |\n| 1 | 2 |\n", out)
    validate_hwpx(out)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_hwpx_writer.py -v`
Expected: 신규 6건 FAIL — `ImportError: cannot import name 'markdown_to_hwpx'`

- [ ] **Step 3: 구현 — 블록 렌더러**

`web/hwpx_writer.py`에 추가 (파일 상단 import에 `from xml.sax.saxutils import escape`와 `from web.md_blocks import Block, Heading, ListBlock, Paragraph, Span, Table, parse_blocks` 추가):

```python
# ------------------------------------------------------------- block renderer

_HEADING_CHAR_PR = {1: 4, 2: 5, 3: 6}


def _span_char_pr(span: Span) -> int:
    if span.bold and span.italic:
        return 3
    if span.bold:
        return 1
    if span.italic:
        return 2
    return 0


class _IdGen:
    """hp:p id 는 문서 안에서 유일하기만 하면 된다. 1은 secPr 문단이 쓴다."""

    def __init__(self) -> None:
        self._next = 2

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


def _runs_xml(spans: list[Span], char_pr_override: int | None = None) -> str:
    runs = []
    for span in spans:
        pr = char_pr_override if char_pr_override is not None else _span_char_pr(span)
        runs.append(f'<hp:run charPrIDRef="{pr}"><hp:t>{escape(span.text)}</hp:t></hp:run>')
    return "".join(runs)


def _para_xml(ids: _IdGen, spans: list[Span], char_pr_override: int | None = None) -> str:
    return (
        f'<hp:p id="{ids.take()}" paraPrIDRef="0" styleIDRef="0" '
        'pageBreak="0" columnBreak="0" merged="0">'
        + _runs_xml(spans, char_pr_override)
        + "</hp:p>"
    )


def _cell_xml(ids: _IdGen, spans: list[Span], col: int, row: int,
              col_width: int, bold_header: bool) -> str:
    override = 1 if bold_header else None
    return (
        '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" '
        'dirty="0" borderFillIDRef="2">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + _para_xml(ids, spans, override)
        + "</hp:subList>"
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        '<hp:cellSpan colSpan="1" rowSpan="1"/>'
        f'<hp:cellSz width="{col_width}" height="1000"/>'
        '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
        "</hp:tc>"
    )


def _table_xml(ids: _IdGen, table: Table) -> str:
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
            tcs.append(_cell_xml(ids, spans, c, r, col_width, bold_header))
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    tbl = (
        f'<hp:tbl id="{ids.take()}" zOrder="0" numberingType="TABLE" '
        'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'pageBreak="CELL" repeatHeader="1" rowCnt="{row_cnt}" colCnt="{col_cnt}" '
        'cellSpacing="0" borderFillIDRef="2" noAdjust="0">'
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
        f'<hp:p id="{ids.take()}" paraPrIDRef="0" styleIDRef="0" '
        'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{tbl}<hp:t/></hp:run></hp:p>'
    )


def _blocks_to_paras(blocks: list[Block]) -> list[str]:
    ids = _IdGen()
    paras: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            paras.append(_para_xml(ids, block.spans, _HEADING_CHAR_PR[block.level]))
        elif isinstance(block, Paragraph):
            paras.append(_para_xml(ids, block.spans))
        elif isinstance(block, ListBlock):
            for n, item in enumerate(block.items, start=1):
                prefix = f"{n}. " if block.ordered else "• "
                paras.append(_para_xml(ids, [Span(prefix), *item]))
        elif isinstance(block, Table):
            paras.append(_table_xml(ids, block))
    return paras


# ------------------------------------------------------------------ 공개 API

def markdown_to_hwpx(md_text: str, out_path: Path) -> None:
    """마크다운을 기본 서식 HWPX 로 변환해 out_path 에 쓴다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(package_hwpx(_blocks_to_paras(parse_blocks(md_text))))


def write_hwpx(report_path: Path, result_path: Path) -> None:
    """worker 가 호출하는 진입점: report.md → result.hwpx + 자체 검증."""
    md_text = Path(report_path).read_text(encoding="utf-8")
    markdown_to_hwpx(md_text, Path(result_path))
    validate_hwpx(Path(result_path))


if __name__ == "__main__":  # 수동 검증용: python -m web.hwpx_writer in.md out.hwpx
    import sys

    if len(sys.argv) != 3:
        sys.exit("usage: python -m web.hwpx_writer <input.md> <output.hwpx>")
    markdown_to_hwpx(Path(sys.argv[1]).read_text(encoding="utf-8"), Path(sys.argv[2]))
    validate_hwpx(Path(sys.argv[2]))
    print(f"OK: {sys.argv[2]}")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_hwpx_writer.py tests/test_md_blocks.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 실파일 수동 검증**

```bash
python -m web.hwpx_writer README.md /tmp/readme.hwpx
npx -y kordoc /tmp/readme.hwpx --format json --silent | head -c 500
```

Expected: `OK: /tmp/readme.hwpx` 출력, kordoc 라운드트립에서 README 텍스트 일부 확인. 가능하면 한글에서 열어 제목 크기·표 테두리 확인.

- [ ] **Step 6: Commit**

```bash
git add web/hwpx_writer.py tests/test_hwpx_writer.py
git commit -m "feat: render markdown blocks to HWPX body XML with CLI entry"
```

---

### Task 4: `pipeline_runner.convert_many`

**Files:**
- Modify: `web/pipeline_runner.py`
- Test: `tests/test_pipeline_runner.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_pipeline_runner.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_pipeline_runner.py -v`
Expected: 신규 2건 FAIL — `AttributeError: ... has no attribute 'convert_many'`

- [ ] **Step 3: 구현**

`web/pipeline_runner.py` 끝에 추가:

```python
# 업로드 검증에서 쓰도록 재노출
SUPPORTED_EXTENSIONS = kordoc_pipeline.SUPPORTED_EXTENSIONS


def convert_many(upload_paths: list[Path], converted_root: Path) -> list[Path]:
    """여러 업로드 파일을 차례로 변환한다. 하나라도 실패하면 파일명을 담아 실패."""
    doc_dirs: list[Path] = []
    for path in upload_paths:
        try:
            doc_dirs.append(convert(path, converted_root))
        except Exception as exc:
            raise PipelineError(f"'{Path(path).name}' 변환 실패: {exc}") from exc
    return doc_dirs
```

주의: `convert_many` 내부에서 모듈 전역 `convert`를 직접 참조해야 monkeypatch가 동작한다 (지역 별칭으로 잡아두지 말 것).

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_pipeline_runner.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add web/pipeline_runner.py tests/test_pipeline_runner.py
git commit -m "feat: add convert_many with per-file failure reporting"
```

---

### Task 5: `job_manager` — 다중 업로드, output_type, GENERATING

**Files:**
- Modify: `web/job_manager.py`
- Test: `tests/test_job_manager.py` (기존 호출부 수정 포함)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_job_manager.py`에 추가:

```python
def test_create_with_multiple_uploads_and_output_type(tmp_path):
    from web.job_manager import JobManager

    mgr = JobManager(base_dir=tmp_path)
    job = mgr.create(
        uploads=[("a.hwp", b"x"), ("b.xlsx", b"y")],
        request_text="취합해줘",
        output_type="merge",
    )
    assert [p.name for p in job.upload_paths] == ["a.hwp", "b.xlsx"]
    assert all(p.read_bytes() in (b"x", b"y") for p in job.upload_paths)
    assert job.output_type == "merge"
    assert job.result_path == job.dir / "result.hwpx"


def test_generating_state_exists():
    from web.job_manager import JobState
    assert JobState.GENERATING.value == "generating"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_job_manager.py -v`
Expected: 신규 2건 FAIL (`create() got an unexpected keyword argument 'uploads'` 등)

- [ ] **Step 3: 구현**

`web/job_manager.py` 수정:

`JobState`에 추가 (ANALYZING 다음 줄):

```python
    GENERATING = "generating"
```

`Job` 데이터클래스에서 `upload_path: Path`를 `upload_paths: list[Path]`로 바꾸고 `output_type` 필드와 `result_path` 프로퍼티 추가:

```python
@dataclass
class Job:
    id: str
    dir: Path
    upload_paths: list[Path]
    request_text: str
    output_type: str = "report"
    state: JobState = JobState.QUEUED
    step: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    events: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)

    @property
    def converted_dir(self) -> Path:
        return self.dir / "converted"

    @property
    def report_path(self) -> Path:
        return self.dir / "report.md"

    @property
    def result_path(self) -> Path:
        return self.dir / "result.hwpx"

    @property
    def log_path(self) -> Path:
        return self.dir / "codex_log.jsonl"
```

`JobManager.create`를 다중 업로드 시그니처로 교체:

```python
    def create(
        self,
        uploads: list[tuple[str, bytes]],
        request_text: str,
        output_type: str = "report",
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.base_dir / job_id
        (job_dir / "upload").mkdir(parents=True, exist_ok=True)

        upload_paths: list[Path] = []
        for filename, file_bytes in uploads:
            upload_path = job_dir / "upload" / filename
            upload_path.write_bytes(file_bytes)
            upload_paths.append(upload_path)
        (job_dir / "request.txt").write_text(request_text, encoding="utf-8")

        now = _now()
        job = Job(
            id=job_id,
            dir=job_dir,
            upload_paths=upload_paths,
            request_text=request_text,
            output_type=output_type,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job
        self._write_status(job)
        return job
```

`_write_status`의 status dict에 `"output_type": job.output_type,` 한 줄 추가.

- [ ] **Step 4: 기존 호출부 일괄 수정**

기존 테스트들(`tests/test_job_manager.py`, `tests/test_worker.py`, `tests/test_job_runner.py` 등)의 create 호출을 다음 패턴으로 치환:

```python
# 변경 전
mgr.create(upload_filename="a.hwp", file_bytes=b"x", request_text="정리")
# 변경 후
mgr.create(uploads=[("a.hwp", b"x")], request_text="정리")
```

`job.upload_path`를 참조하는 코드는 `job.upload_paths`(목록)로 바꾼다.

Run: `python -m pytest tests/ -v` — 이 시점에 `test_worker.py`/`test_app.py`는 Task 7·8에서 고칠 시그니처 문제로 실패할 수 있다. **`test_job_manager.py`만 전부 PASS면 통과.**

- [ ] **Step 5: Commit**

```bash
git add web/job_manager.py tests/
git commit -m "feat: job carries multiple uploads, output_type, generating state"
```

---

### Task 6: `codex_runner` — report/merge 프롬프트 분기

**Files:**
- Modify: `web/codex_runner.py`
- Test: `tests/test_codex_runner.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_codex_runner.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_codex_runner.py -v`
Expected: 신규 FAIL — `ImportError: cannot import name 'MERGE_INSTRUCTION'`

- [ ] **Step 3: 구현**

`web/codex_runner.py` 수정. `SYSTEM_INSTRUCTION`을 다중 문서를 언급하도록 갱신하고 `MERGE_INSTRUCTION` 추가:

```python
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


def build_prompt(request_text: str, output_type: str = "report") -> str:
    instruction = MERGE_INSTRUCTION if output_type == "merge" else SYSTEM_INSTRUCTION
    return f"{instruction}\n\n[담당자 요청]\n{request_text}\n"
```

`run_codex` 시그니처에 `output_type: str = "report"` 파라미터를 추가하고 첫 줄을 `prompt = build_prompt(request_text, output_type)`으로 변경. (호출 시 `-C`는 Task 7에서 `converted/` 루트가 넘어온다 — `run_codex` 자체는 받은 디렉터리를 그대로 쓰므로 추가 변경 없음.)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_codex_runner.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add web/codex_runner.py tests/test_codex_runner.py
git commit -m "feat: branch codex prompt by output_type (report/merge)"
```

---

### Task 7: `worker` — generating 단계와 폴백

**Files:**
- Modify: `web/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: 기존 테스트를 새 흐름에 맞게 수정 + 신규 테스트 추가**

`tests/test_worker.py` 전체를 다음으로 교체:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_worker.py -v`
Expected: FAIL — `run_job() got an unexpected keyword argument 'hwpx_fn'`

- [ ] **Step 3: 구현**

`web/worker.py` 전체를 다음으로 교체:

```python
"""한 잡의 변환→분석→HWPX 생성 오케스트레이션."""
from __future__ import annotations

from typing import Callable

from web.job_manager import Job, JobManager, JobState

ConvertFn = Callable[..., object]   # (upload_paths, converted_root) -> list[doc_dir]
CodexFn = Callable[..., None]       # (converted_dir, request_text, report_path, output_type, on_event, **kw)
HwpxFn = Callable[..., None]        # (report_path, result_path) -> None


def run_job(
    job: Job,
    manager: JobManager,
    convert_fn: ConvertFn,
    codex_fn: CodexFn,
    hwpx_fn: HwpxFn,
) -> None:
    """블로킹 함수. 웹 레이어는 스레드풀에서 호출한다."""
    try:
        manager.set_state(job, JobState.CONVERTING, step="문서 변환 중")
        convert_fn(job.upload_paths, job.converted_dir)
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
        )
    except Exception as exc:  # noqa: BLE001
        manager.set_state(job, JobState.FAILED, error=f"분석 실패: {exc}")
        job.events.put({"type": "end"})
        return

    try:
        manager.set_state(job, JobState.GENERATING, step="한글 파일 생성 중")
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

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_worker.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add web/worker.py tests/test_worker.py
git commit -m "feat: add generating stage with HTML-preview fallback on failure"
```

---

### Task 8: `app.py` — 다중 업로드 라우트 + 검증 + HWPX 다운로드

**Files:**
- Modify: `web/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: 기존 테스트 수정 + 신규 테스트 추가**

`tests/test_app.py`를 수정한다. 공통 변경: 페이크를 새 시그니처로 바꾸고 업로드 필드를 `files`(복수)로 바꾼다.

```python
# 파일 상단의 페이크들을 이렇게 교체
def fake_convert_many(upload_paths, converted_root):
    doc_dir = Path(converted_root) / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    return [doc_dir]


def fake_codex(converted_dir, request_text, report_path, output_type, on_event, **kwargs):
    on_event({"type": "item", "text": "분석 중"})
    Path(report_path).write_text("# 분석 리포트\n결과", encoding="utf-8")


def fake_hwpx(report_path, result_path):
    Path(result_path).write_bytes(b"PK-fake-hwpx")
```

각 테스트의 monkeypatch 를 `convert` → `convert_many`, `run_codex` → 동일, 추가로 `write_hwpx` → `fake_hwpx`로 바꾸고, 업로드를 다음 형태로 바꾼다:

```python
    resp = client.post(
        "/jobs",
        files=[("files", ("요람.hwp", b"dummy", "application/octet-stream"))],
        data={"request_text": "정리해줘", "output_type": "report"},
    )
```

신규 테스트 추가:

```python
def test_post_multiple_files_all_saved(tmp_path: Path, monkeypatch):
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
            ("files", ("b.xlsx", b"2", "application/octet-stream")),
        ],
        data={"request_text": "취합", "output_type": "merge"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    upload_dir = jobs_dir / job_id / "upload"
    assert (upload_dir / "a.hwp").exists() and (upload_dir / "b.xlsx").exists()


def test_duplicate_filenames_are_suffixed(tmp_path: Path, monkeypatch):
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
            ("files", ("같은이름.hwp", b"1", "application/octet-stream")),
            ("files", ("같은이름.hwp", b"2", "application/octet-stream")),
        ],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 200
    upload_dir = jobs_dir / resp.json()["job_id"] / "upload"
    assert (upload_dir / "같은이름.hwp").exists()
    assert (upload_dir / "같은이름_2.hwp").exists()


def test_invalid_output_type_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("a.hwp", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "pptx"},
    )
    assert resp.status_code == 400


def test_unsupported_extension_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("virus.exe", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "report"},
    )
    assert resp.status_code == 400
    assert "virus.exe" in resp.json()["detail"]


def test_hwpx_download_after_done(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(app_module, "convert_many", fake_convert_many)
    monkeypatch.setattr(app_module, "run_codex", fake_codex)
    monkeypatch.setattr(app_module, "write_hwpx", fake_hwpx)
    app_module.reset_manager()
    app_module.reset_runner()

    client = TestClient(app_module.app)
    resp = client.post(
        "/jobs",
        files=[("files", ("a.hwp", b"1", "application/octet-stream"))],
        data={"request_text": "x", "output_type": "report"},
    )
    job_id = resp.json()["job_id"]
    assert _wait_state(client, job_id, "done") == "done"

    dl = client.get(f"/jobs/{job_id}/hwpx")
    assert dl.status_code == 200
    assert dl.content == b"PK-fake-hwpx"
    assert ".hwpx" in dl.headers["content-disposition"]


def test_hwpx_404_before_generated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS_DIR", tmp_path / "jobs")
    app_module.reset_manager()
    client = TestClient(app_module.app)
    assert client.get("/jobs/nope/hwpx").status_code == 404
```

경로 탈출 테스트(`test_upload_filename_is_sanitized_against_traversal`)는 업로드 형태만 `files=[("files", ("../../evil.hwp", ...))]`로 바꿔 유지한다.

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL (라우트가 아직 단일 `file` 파라미터)

- [ ] **Step 3: 구현**

`web/app.py` 수정. import 갱신:

```python
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from web.codex_runner import run_codex
from web.hwpx_writer import write_hwpx
from web.job_manager import JobManager
from web.job_runner import JobRunner
from web.pipeline_runner import SUPPORTED_EXTENSIONS, convert_many
from web.report_renderer import render_html
from web.worker import run_job
```

`create_job` 교체:

```python
@app.post("/jobs")
async def create_job(
    files: list[UploadFile] = File(...),
    request_text: str = Form(...),
    output_type: str = Form("report"),
) -> dict:
    if output_type not in ("report", "merge"):
        raise HTTPException(status_code=400, detail="output_type 은 report 또는 merge 여야 합니다.")
    if not files:
        raise HTTPException(status_code=400, detail="파일을 1개 이상 올려 주세요.")

    uploads: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for f in files:
        # 클라이언트가 보낸 파일명은 신뢰하지 않는다(경로 탈출 방지): 마지막 경로 요소만 사용.
        safe_name = Path(f.filename or "upload.bin").name or "upload.bin"
        if Path(safe_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"지원하지 않는 파일 형식입니다: {safe_name}",
            )
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        n = 2
        while safe_name in seen:
            safe_name = f"{stem}_{n}{suffix}"
            n += 1
        seen.add(safe_name)
        uploads.append((safe_name, await f.read()))

    job = get_manager().create(
        uploads=uploads, request_text=request_text, output_type=output_type
    )
    get_runner().submit(run_job, job, get_manager(), convert_many, run_codex, write_hwpx)
    return {"job_id": job.id}
```

다운로드 라우트 추가 (`get_report` 아래):

```python
@app.get("/jobs/{job_id}/hwpx")
def get_hwpx(job_id: str) -> FileResponse:
    job = get_manager().get(job_id)
    if job is None or not job.result_path.exists():
        raise HTTPException(status_code=404, detail="한글 파일이 아직 없습니다.")
    filename = "취합문서.hwpx" if job.output_type == "merge" else "분석리포트.hwpx"
    return FileResponse(job.result_path, filename=filename, media_type="application/octet-stream")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_app.py -v`
Expected: 전부 PASS

Run: `python -m pytest tests/ -v`
Expected: 전체 PASS (남은 실패가 있으면 이 시점에 수정)

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_app.py
git commit -m "feat: multi-file upload with output_type and HWPX download route"
```

---

### Task 9: 프런트엔드 — 다중 선택, 산출물 라디오, 다운로드 버튼

**Files:**
- Modify: `web/static/index.html`

- [ ] **Step 1: 폼 마크업 교체**

`<form id="form">` 블록을 다음으로 교체:

```html
  <form id="form">
    <label>문서 파일 (여러 개 선택 가능)
      <input type="file" id="files" name="files" multiple required />
    </label>
    <label>산출물 종류</label>
    <label style="font-weight:400"><input type="radio" name="output_type" value="report" checked />
      분석 리포트 — 문서들을 분석한 리포트를 받습니다</label>
    <label style="font-weight:400"><input type="radio" name="output_type" value="merge" />
      취합 문서 — 여러 문서의 내용을 하나의 새 문서로 취합합니다</label>
    <label>요청 내용
      <textarea id="request_text" placeholder="예: 부서별 보고서를 하나로 취합해줘"></textarea>
    </label>
    <button type="submit">시작</button>
  </form>
```

- [ ] **Step 2: 제출 스크립트 수정**

submit 핸들러의 FormData 부분을 교체:

```javascript
      const fd = new FormData();
      for (const f of document.getElementById('files').files) fd.append('files', f);
      fd.append('request_text', document.getElementById('request_text').value);
      fd.append('output_type', document.querySelector('input[name="output_type"]:checked').value);
```

업로드 실패 시 서버 detail 을 보여주도록 실패 분기도 교체:

```javascript
      if (!resp.ok) {
        let detail = '업로드 실패';
        try { detail = (await resp.json()).detail || detail; } catch {}
        statusEl.textContent = detail;
        statusEl.className = 'status err';
        return;
      }
```

- [ ] **Step 3: 완료 화면에 HWPX 다운로드 버튼 추가**

`loadReport`에서 report 링크 위에 HWPX 링크를 추가:

```javascript
      const hwpx = document.createElement('a');
      hwpx.href = `/jobs/${jobId}/hwpx`;
      hwpx.textContent = '⬇ 한글 파일(.hwpx) 다운로드';
      reportEl.appendChild(hwpx);
      reportEl.appendChild(document.createElement('br'));
```

상태 표시는 서버의 `step` 문자열("한글 파일 생성 중")이 그대로 나오므로 추가 작업 없음. 제목/안내 문구도 갱신: `<h1>한글 문서 분석·취합</h1>`, `<p>한글/엑셀 파일 여러 개를 올리고, 분석 리포트나 취합 문서를 받으세요. 결과는 한글 파일로 내려받을 수 있습니다.</p>`

- [ ] **Step 4: 수동 확인**

```bash
uvicorn web.app:app --reload
```

브라우저에서 `http://127.0.0.1:8000` 접속 → 파일 2개 선택, "취합 문서" 라디오, 제출 → 상태가 변환→분석→생성→완료로 흐르고, 완료 후 `.hwpx` 다운로드 버튼이 동작하는지 확인 (codex 미설치 환경이면 분석 실패 메시지까지만 확인).

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html
git commit -m "feat: multi-file picker, output type radio, hwpx download button"
```

---

### Task 10: 라운드트립 스모크 + 문서 + 최종 검증

**Files:**
- Test: `tests/test_hwpx_writer.py` (스모크 추가)
- Modify: `README.md`

- [ ] **Step 1: kordoc 라운드트립 스모크 테스트 추가**

`tests/test_hwpx_writer.py`에 추가 (npx 없는 환경에선 자동 skip):

```python
import shutil

import pytest


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx 없음 — 로컬에서만 실행")
def test_roundtrip_generated_hwpx_through_kordoc(tmp_path: Path):
    """우리가 만든 HWPX 를 kordoc 이 다시 읽어 텍스트가 보존되는지 확인."""
    import subprocess

    from web.hwpx_writer import markdown_to_hwpx

    out = tmp_path / "rt.hwpx"
    markdown_to_hwpx("# 라운드트립 제목\n본문 텍스트 보존 확인\n", out)
    completed = subprocess.run(
        ["npx", "-y", "kordoc", str(out), "--format", "json", "--silent"],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "라운드트립 제목" in completed.stdout
    assert "본문 텍스트 보존 확인" in completed.stdout
```

Run: `python -m pytest tests/test_hwpx_writer.py -v`
Expected: 전부 PASS (npx 있는 로컬 기준)

- [ ] **Step 2: README 갱신**

README의 웹앱 사용법 섹션에 반영: 다중 파일 업로드 가능, 산출물 종류(분석 리포트/취합 문서) 선택, 결과는 HTML 미리보기 + `.hwpx` 다운로드, HWPX 생성이 실패해도 리포트 내용은 화면에서 확인 가능.

- [ ] **Step 3: 전체 테스트**

Run: `python -m pytest tests/ -v`
Expected: 전체 PASS

- [ ] **Step 4: 실제 동작 수동 검증 (1단계 완료 조건)**

codex 인증이 있는 환경에서:

```bash
uvicorn web.app:app
```

1. 실제 HWP 파일 2개 이상 업로드 + "취합 문서" 선택 + 취합 요청 입력
2. done 도달 후 `.hwpx` 다운로드
3. **다운로드한 파일을 실제 한글(또는 한컴독스)에서 열어** 제목 크기, 표 테두리, 본문이 깨지지 않는지 확인 — 스펙의 수동 검증 완료 조건

- [ ] **Step 5: Commit**

```bash
git add tests/test_hwpx_writer.py README.md
git commit -m "test: kordoc roundtrip smoke for generated HWPX; docs: usage update"
```

---

## Self-Review 결과

- **스펙 커버리지(1단계 범위):** 다중 업로드(Task 5·8), output_type 선택(Task 6·8·9), convert_many(Task 4), 프롬프트 분기(Task 6), generating 상태(Task 5·7), markdown_to_hwpx(Task 1·3), 다운로드 라우트(Task 8), 프런트(Task 9), 생성 실패 폴백(Task 7), 라운드트립 스모크·수동 검증(Task 10), 확장자 검증·파일명 중복 처리(Task 8). 2단계 항목(양식·자리표시자)은 의도적으로 제외 — 별도 계획.
- **타입 일관성:** `convert_fn(upload_paths, converted_root) -> list[Path]`, `codex_fn(..., output_type, on_event)`, `hwpx_fn(report_path, result_path)` 시그니처가 Task 4·6·7·8 페이크와 일치함을 확인.
- **알려진 리스크:** Task 1의 OWPML 템플릿이 실제 한글에서 안 열릴 수 있음 — Task 1 Step 5의 디버깅 절차로 해소하고 넘어간다.
