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
