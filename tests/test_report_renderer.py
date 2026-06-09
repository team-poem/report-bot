from web.report_renderer import render_html


def test_render_html_is_standalone_document_with_inlined_style():
    out = render_html("# 제목\n\n본문 문단")
    assert out.startswith("<!DOCTYPE html>")
    assert "<style>" in out  # 스타일 내장(파일로 열어도 동일하게 보임)
    assert "<h1>제목</h1>" in out
    assert "본문 문단" in out


def test_render_html_renders_markdown_table():
    md = "| 학과 | 정원 |\n| --- | --- |\n| 컴공 | 50 |\n"
    out = render_html(md)
    assert "<table>" in out
    assert "<th>학과</th>" in out
    assert "<td>컴공</td>" in out
    # 표 테두리 스타일이 문서에 포함되어 있어야 한다
    assert "border: 1px solid" in out


def test_render_html_escapes_title():
    out = render_html("내용", title="<x>")
    assert "<title>&lt;x&gt;</title>" in out
