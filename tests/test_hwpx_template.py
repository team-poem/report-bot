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
