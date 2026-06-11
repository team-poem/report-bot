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
