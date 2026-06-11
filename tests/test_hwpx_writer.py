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
