"""양식 HWPX 의 자리표시자 스캔·치환.

규약: {{본문}} / {{추가: 지시}} / {{수정시작: 지시}} ~ {{수정끝}}.
자리표시자는 한 문단에 단독으로 있어야 한다(업로드 시 검증).
슬롯 ID 는 문서 등장 순서로 자동 부여한다: 본문-1, 추가-2, 수정-3 …
"""
from __future__ import annotations

import dataclasses
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from xml.etree import ElementTree as ET

from web.hwpx_writer import StyleIds, _border, _char_pr, render_blocks, validate_hwpx

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

_MARKER_RE = re.compile(r"^\{\{(본문|추가|수정시작|수정끝)(?::\s*([^{}]*))?\}\}$")
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
    """문단의 직계 run 텍스트만 합친다(마커가 run 으로 쪼개진 경우 대비).
    중첩된 표(run 안의 hp:tbl) 내부 텍스트는 포함하지 않는다 — 표 칸 문단은
    별도의 hp:p 로 순회되므로 거기서 따로 검사된다."""
    return "".join(t.text or "" for t in p.findall("hp:run/hp:t", NS))


def _read_sections(source: Path | IO[bytes]) -> dict[str, ET.Element]:
    try:
        with zipfile.ZipFile(source) as zf:
            names = sorted(
                (n for n in zf.namelist() if _SECTION_RE.fullmatch(n)),
                key=lambda n: int(re.search(r"section(\d+)", n).group(1)),
            )
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
