import io
import re
import zipfile
from pathlib import Path

import pytest

from web.hwpx_writer import markdown_to_hwpx, package_hwpx, render_blocks


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


# ---------------------------------------------------------------------------
# Fix 1 — _para_text must not recurse into nested tables
# ---------------------------------------------------------------------------

def test_scan_marker_inside_table_cell_found_once(tmp_path: Path):
    """표 칸 안의 마커는 한 번만 감지되고, 바깥 문단이 오탐되지 않아야 한다."""
    from web.hwpx_template import scan_placeholders

    t = make_template(tmp_path, "| {{추가: 셀 지시}} | 일반 칸 |\n| --- | --- |\n| a | b |\n")
    slots = scan_placeholders(t)
    assert [s.id for s in slots] == ["추가-1"]
    assert slots[0].instruction == "셀 지시"


# ---------------------------------------------------------------------------
# Fix 2 — marker regex must not swallow }}
# ---------------------------------------------------------------------------

def test_scan_two_markers_in_one_paragraph_rejected(tmp_path: Path):
    from web.hwpx_template import TemplateError, scan_placeholders

    t = make_template(tmp_path, "{{추가: a}} {{추가: b}}\n")
    with pytest.raises(TemplateError, match="단독"):
        scan_placeholders(t)


# ---------------------------------------------------------------------------
# Fix 3 — section filename ordering must be numeric, not lexicographic
# ---------------------------------------------------------------------------

def test_scan_sections_ordered_numerically(tmp_path: Path):
    """section10 이 section2 보다 뒤에 와야 한다(사전식 정렬 금지)."""
    from web.hwpx_template import scan_placeholders

    base = make_template(tmp_path, "{{본문}}\n")  # section0 에 본문-마커
    out = tmp_path / "multi.hwpx"
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))
        sec = zin.read("Contents/section0.xml").decode("utf-8")

    # Build section XML by replacing the 본문-marker paragraph with an 추가-marker paragraph.
    # We inject a fresh paragraph XML string in place of the body (between secPr anchor and </hs:sec>).
    def section_with(marker_md: str) -> str:
        para = render_blocks(marker_md, start_id=50)[0]
        # find the body paragraphs region: everything after the first <hp:p id="2" ...> opener
        # up to (but not including) </hs:sec>
        start_idx = sec.index('<hp:p id="2"')
        end_idx = sec.rindex("</hs:sec>")
        return sec[:start_idx] + para + sec[end_idx:]

    with zipfile.ZipFile(out, "a") as zout:
        zout.writestr("Contents/section2.xml", section_with("{{추가: 둘}}\n"))
        zout.writestr("Contents/section10.xml", section_with("{{추가: 열}}\n"))
    slots = scan_placeholders(out)
    assert [s.instruction for s in slots] == ["", "둘", "열"]


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
