import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from web.hwpx_writer import REQUIRED_ENTRIES, package_hwpx, validate_hwpx


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
    from web.hwpx_writer import HwpxError
    with pytest.raises(HwpxError):
        validate_hwpx(broken)


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


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx 없음 — 로컬에서만 실행")
def test_roundtrip_generated_hwpx_through_kordoc(tmp_path: Path):
    """우리가 만든 HWPX 를 kordoc 이 다시 읽어 텍스트가 보존되는지 확인."""
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
