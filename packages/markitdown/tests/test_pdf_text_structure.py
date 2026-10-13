"""Regression cases for font-based PDF structure restoration."""

from collections import Counter

from markitdown.converters._pdf_converter import (
    _bold_text,
    _font_size,
    _restore_text_structure,
)


def test_pdf_text_structure():
    def line(text, size, page=1, **extra):
        return {
            "text": text,
            "bold_text": text,
            "size": size,
            "uniform": True,
            "page": page,
            "margin": False,
            "gap": 8,
            "short": True,
            "page_lines": 20,
            **extra,
        }

    assert _font_size({"size": 13.98}) == _font_size({"size": 14})
    bold = _bold_text(
        "普通强调文字",
        [
            {"text": char, "fontname": "Example-Bold" if 2 <= i < 4 else "Example"}
            for i, char in enumerate("普通强调文字")
        ],
    )
    assert bold == "普通**强调**文字"
    features = [
        line("封面", 28, page=0, page_lines=3),
        line("页眉", 22, page=0, margin=True),
        line("页眉", 22, page=1, margin=True),
        line("项目介绍", 20),
        line("设计方案", 18),
        line("实现细节", 16),
        line("正文", 14),
        line("普通强调文字", 14, bold_text=bold),
        line("略大正文。", 16),
        line("紧邻正文", 16, gap=0),
    ]
    source = "\n".join(feature["text"] for feature in features) + "\n| 内容 | 数值 |"
    result = _restore_text_structure(
        source, Counter({14: 700, 16: 100, 18: 100, 20: 100}), features, 2
    )
    assert "# 项目介绍" in result
    assert "## 设计方案" in result
    assert "### 实现细节" in result
    assert "**强调**" in result
    assert "# 封面" not in result and "# 页眉" not in result
    assert "# 正文" not in result and "# 略大正文。" not in result
    assert "# 紧邻正文" not in result
    assert "| 内容 | 数值 |" in result
    assert (
        _restore_text_structure("正文", Counter({14: 100}), [line("正文", 14)], 1) == "正文"
    )

    # No 60% majority: the most common size is a tentative body baseline.
    assert (
        _restore_text_structure(
            "不确定标题", Counter({14: 50, 16: 30, 18: 20}), [line("不确定标题", 16)], 2
        )
        == "不确定标题"
    )
    assert "## 1.1 子节" in _restore_text_structure(
        "1.1 子节", Counter({14: 70, 16: 30}), [line("1.1 子节", 16)], 2
    )
