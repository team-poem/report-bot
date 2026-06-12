import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import kordoc_pipeline  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def test_write_xlsx_coerces_non_scalar_cells(tmp_path: Path):
    """kordoc 이 표 안에 에러 객체(dict)를 끼워 넣어도 xlsx 생성이 죽지 않아야 한다."""
    out = tmp_path / "doc.xlsx"
    sheets = {
        "tables": [
            ["순번", "과목"],
            [1, {"message": "이미지 기반 PDF — OCR 필요", "code": "NEEDS_OCR"}],
            [2, ["리스트", "값"]],
        ],
        "facts": [
            {"k": "정원", "v": {"nested": True}},
        ],
    }
    assert kordoc_pipeline.write_xlsx(out, sheets) is True

    wb = openpyxl.load_workbook(out)
    ws = wb["tables"]
    assert ws.cell(row=2, column=1).value == 1
    assert "NEEDS_OCR" in ws.cell(row=2, column=2).value  # dict → 문자열로 강등
    assert "리스트" in ws.cell(row=3, column=2).value
    assert "nested" in wb["facts"].cell(row=2, column=2).value
