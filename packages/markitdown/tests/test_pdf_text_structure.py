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


def paragraph_line(text, x, y, width=10, size=10):
    chars = []
    for index, value in enumerate(text):
        char = glyph(value, x + index * width, y, size=size)
        char["x1"] = char["x0"] + width
        chars.append(char)
    return _lines(chars, 800)[0]


def test_two_character_indent_uses_glyph_width_and_joins_wide_leading():
    """字宽可与字号不同；两倍字号的正常行距也不能拆散一个段落。"""
    lines = [
        paragraph_line("这是正文第一行，", 46, 100, width=8),
        paragraph_line("这是自动换行内容", 30, 120, width=8),
        paragraph_line("下一段正文也有缩进", 46, 140, width=8),
        paragraph_line("接上后半段。", 30, 160, width=8),
    ]
    assert lines[0]["char_width"] == 8
    assert _paragraphs(lines) == (
        "\u3000\u3000这是正文第一行，这是自动换行内容\n\n"
        "\u3000\u3000下一段正文也有缩进接上后半段。"
    )


def test_explicit_fullwidth_indent_is_preserved_once():
    line = paragraph_line("\u3000\u3000单行段落。", 30, 100)
    assert _paragraphs([line]) == "\u3000\u3000单行段落。"


def test_guest_biographies_with_large_leading_form_four_paragraphs():
    """复现真机页：12pt 字号、31.2pt 正常行距，21 行应合并成四位嘉宾的介绍。"""
    biographies = [
        (
            68.64,
            [
                "嘉宾甲大数据开发工程师，熟悉数据环境部",
                "署和核心计算框架，参与分布式存储系统",
                "开发，并掌握数据处理与分析方法，曾参",
                "与多项平台建设工作，具有较丰富的项目",
                "实践经验。",
            ],
        ),
        (
            80.64,
            [
                "嘉宾乙人工智能工程师，擅长网页数据采",
                "集与分析，熟悉可视化工具，参与系统需",
                "求分析与设计，负责项目研发与数据处理",
                "以及应用服务开发，具备多类项目实施经",
                "验，并参与高校教学工作，能够将工程实",
                "践与课堂训练结合，指导学生完成完整项",
                "目的研发。",
            ],
        ),
        (
            80.64,
            [
                "嘉宾丙应用高级工程师与系统架构师，参",
                "与社区建设及技术分享，具有财务系统开",
                "发与项目架构经验，长期参与企业研发以",
                "及高校实践教学工作，负责授课、项目指",
                "导和技术培训。",
            ],
        ),
        (
            82.32,
            [
                "嘉宾丁应用高级工程师，曾参与在线项",
                "目系统的设计与开发，具有多年项目经",
                "验，致力于将企业技术带入课堂，善于把",
                "复杂技术讲得生动有趣。",
            ],
        ),
    ]
    lines, expected = [], []
    y = 126.05
    for guest, (first_x, texts) in enumerate(biographies):
        if guest == 1:
            y += 18  # 原页第二位嘉宾前的段间距为 49.2pt。
        for index, text in enumerate(texts):
            line = paragraph_line(
                text, first_x if index == 0 else 56.64, y, width=12, size=12
            )
            if index == 0:
                line["markdown"] = "**" + text[:3] + "**" + text[3:]
            lines.append(line)
            y += 31.2
        joined = "".join(texts)
        expected.append(
            ("\u3000\u3000" if guest else "") + "**" + joined[:3] + "**" + joined[3:]
        )
    assert len(lines) == 21
    paragraphs = _paragraphs(lines).split("\n\n")
    assert len(paragraphs) == 4
    assert paragraphs == expected


def test_short_completed_paragraph_and_extra_vertical_gap_stay_separate():
    lines = [
        paragraph_line("短段落。", 30, 100),
        paragraph_line("这里是下一段较长的正文", 30, 120),
        paragraph_line("自然接续的文字", 30, 140),
        paragraph_line("段间留白后的正文", 30, 180),
    ]
    assert _paragraphs(lines) == (
        "短段落。\n\n这里是下一段较长的正文自然接续的文字\n\n段间留白后的正文"
    )


def test_full_line_sentence_end_alone_does_not_start_paragraph():
    lines = [
        paragraph_line("第一句话恰好在整行末尾结束。", 30, 100),
        paragraph_line("下一句接着写", 30, 120),
    ]
    assert _paragraphs(lines) == "第一句话恰好在整行末尾结束。下一句接着写"


def test_english_wrap_keeps_word_space_with_wide_leading():
    lines = [
        paragraph_line("Words wrap at", 30, 100, width=5),
        paragraph_line("the page edge", 30, 120, width=5),
    ]
    assert _paragraphs(lines) == "Words wrap at the page edge"


def test_list_hanging_wrap_does_not_become_first_line_indent():
    lines = [
        paragraph_line("• 第一项需要换行", 30, 100),
        paragraph_line("接上列表内容", 50, 120),
        paragraph_line("• 第二项", 30, 140),
        paragraph_line("列表后的正文。", 30, 160),
    ]
    assert _paragraphs(lines) == (
        "- 第一项需要换行接上列表内容\n\n- 第二项\n\n列表后的正文。"
    )


def test_table_cell_keeps_existing_line_break_rules():
    lines = [
        paragraph_line("表格第一行", 30, 100),
        paragraph_line("表格第二行", 30, 120),
    ]
    assert _paragraphs(lines, cell=True) == "表格第一行<br>表格第二行"


def test_outdented_list_marker_does_not_shift_body_margin():
    lines = [
        paragraph_line("正常正文的第一行", 50, 100),
        paragraph_line("接续正文的第二行", 50, 120),
        paragraph_line("• 列表项", 30, 140),
    ]
    assert _paragraphs(lines) == "正常正文的第一行接续正文的第二行\n\n- 列表项"


def test_heading_table_code_and_page_boundaries_flush_paragraphs():
    blocks = [
        paragraph_line("正文标题", 30, 50, width=20, size=20),
        paragraph_line("标题之后的正文", 30, 100),
        dict(kind="table", x0=30, y=120, markdown="| 表格 |\n| --- |"),
        paragraph_line("表格之后的正文", 30, 140),
        dict(kind="code", x0=30, y=160, markdown="```text\n代码\n```"),
        paragraph_line("代码之后的正文", 30, 180),
    ]
    text = _render_pages([(800, blocks), (800, [paragraph_line("次页正文", 30, 100)])])
    assert "# 正文标题\n\n标题之后的正文\n\n| 表格 |" in text
    assert "| --- |\n\n表格之后的正文\n\n```text" in text
    assert "```\n\n代码之后的正文\n\n<!-- PDF page 2 -->\n\n次页正文" in text


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
