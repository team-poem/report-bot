"""Markdown 리포트를 자체 완결형(스타일 내장) HTML 문서로 변환."""
from __future__ import annotations

import html

import markdown

# 앱 안에서 보든, 파일로 내려받아 따로 열든 동일하게 보이도록 스타일을 문서에 내장한다.
_STYLE = """
  body { font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; max-width: 880px;
         margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; line-height: 1.6; }
  h1, h2, h3 { line-height: 1.3; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.9rem;
          display: block; overflow-x: auto; }
  th, td { border: 1px solid #c8c8c8; padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: #f3f4f6; font-weight: 600; }
  tr:nth-child(even) td { background: #fafafa; }
  code { background: #f3f4f6; padding: 0.1em 0.3em; border-radius: 4px; font-size: 0.9em; }
  pre { background: #f6f8fa; padding: 0.75rem; border-radius: 6px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  blockquote { border-left: 3px solid #ddd; margin: 1rem 0; padding: 0 1rem; color: #555; }
"""


def render_html(md_text: str, title: str = "분석 리포트") -> str:
    """Markdown 문자열을 스타일이 내장된 완전한 HTML 문서 문자열로 변환한다."""
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    safe_title = html.escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )
