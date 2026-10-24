"""Baseline order, paragraph joining, heading levels, and literal code regression."""

import json

from markitdown.converters._pdf_converter import (
    _code_text,
    _join_text,
    _lines,
    _paragraphs,
    _render_pages,
)


def glyph(text, x, baseline, size=10, top=None, font="Regular"):
    return dict(
        text=text,
        x0=x,
        x1=x + size / 2,
        top=top or baseline - size,
        bottom=baseline + 2,
        matrix=(1, 0, 0, 1, x, 800 - baseline),
        size=size,
        fontname=font,
        upright=True,
    )


def test_baseline_mixed_cjk_latin_order():
    chars = [
        glyph("B", 20, 100, top=80),
        glyph("中", 10, 100, top=94),
        glyph("文", 30, 100.4, top=95),
    ]
    lines = _lines(chars, 800)
    assert len(lines) == 1
    assert "".join(lines[0]["text"].split()) == "中B文"
    assert len(_lines(chars + [glyph("次", 10, 102)], 800)) == 2


def test_paragraph_unwrap_and_bullet():
    lines = [
        _lines([glyph(c, 10 + i * 5, y) for i, c in enumerate(text)], 800)[0]
        for text, y in [("关键技能不仅是得", 100), ("到答案", 113), ("•列表", 140)]
    ]
    text = _paragraphs(lines)
    assert "关键技能不仅是得到答案" in text
    assert "\n\n- 列表" in text
    assert _join_text("campus shuttle tracking", "app") == "campus shuttle tracking app"
    assert _join_text("**tracking**", "**app**") == "**tracking app**"


def test_relative_headings_merge_wrapped_title_and_keep_footer():
    def line(text, size, y):
        return dict(
            kind="line",
            text=text,
            markdown=text,
            size=size,
            y=y,
            chars=len(text),
            x0=40,
            x1=200,
        )

    blocks = [
        line("大标题", 20, 45),
        line("续行", 20, 70),
        line("章节", 15, 110),
        line("分部分", 12.5, 140),
        line("目的", 10.5, 170),
        line("正文内容" * 50, 10.5, 195),
        line("页脚", 8, 780),
    ]
    text = _render_pages([(800, blocks)])
    assert "# 大标题续行" in text
    assert "## 章节" in text and "### 分部分" in text and "### 目的" in text
    assert "<small>页脚</small>" in text
    assert "<!-- PDF page 1 -->" in text


def test_json_string_layout_wrap_and_indent():
    source = [
        (50, 100, "{"),
        (60, 112, '"value": "正面'),
        (50, 124, '反馈"'),
        (50, 136, "}"),
    ]
    chars = [
        glyph(c, x + i * 5, y, font="CourierNew")
        for x, y, text in source
        for i, c in enumerate(text)
    ]
    rendered = _code_text(chars, 800)
    assert rendered.startswith("```json\n")
    assert json.loads(rendered.split("\n", 1)[1].rsplit("\n", 1)[0]) == {
        "value": "正面反馈"
    }
    assert '\n  "value"' in rendered


def test_code_markdown_keeps_literal_symbols():
    chars = [
        glyph(c, 50 + i * 5, y, font="CourierNew")
        for text, y in [("# 示例", 100), ("|a|b|", 112)]
        for i, c in enumerate(text)
    ]
    assert _code_text(chars, 800).startswith("```markdown\n# 示例\n|a|b|")
