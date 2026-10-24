"""Only drawn borders can create tables; aligned text must remain prose."""

import io
from pathlib import Path

import pdfplumber
import pytest
from reportlab.pdfgen import canvas

from markitdown import MarkItDown
from markitdown.converters._pdf_converter import _page_blocks


def make_pdf(draw):
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(612, 792))
    draw(pdf)
    pdf.save()
    stream.seek(0)
    return stream


def test_aligned_borderless_text_is_never_a_table():
    def draw(pdf):
        for index, row in enumerate(
            [("Name", "Value", "Unit"), ("Alpha", "100", "kg"), ("Beta", "200", "kg")]
        ):
            for x, value in zip((50, 250, 450), row):
                pdf.drawString(x, 720 - index * 20, value)

    stream = make_pdf(draw)
    result = MarkItDown().convert_stream(stream, file_extension=".pdf").markdown
    assert "| ---" not in result
    for word in ("Name", "Value", "Alpha", "100", "Beta", "200"):
        assert word in result


def test_ruled_table_character_ownership_and_escaping():
    def draw(pdf):
        for y in (600, 650, 700):
            pdf.line(50, y, 350, y)
        for x in (50, 150, 350):
            pdf.line(x, 600, x, 700)
        pdf.drawString(60, 675, "ID")
        pdf.drawString(160, 675, "Description")
        pdf.drawString(60, 635, "R01")
        pdf.drawString(60, 620, "2026-04-12")
        pdf.drawString(160, 602, "edge | value")
        pdf.drawString(50, 730, "Before")
        pdf.drawString(50, 570, "After")

    stream = make_pdf(draw)
    result = MarkItDown().convert_stream(stream, file_extension=".pdf").markdown
    assert "| ID | Description |" in result
    assert "R01<br>2026-04-12" in result
    assert r"edge \| value" in result
    assert result.count("edge") == 1
    assert result.index("Before") < result.index("| ID") < result.index("After")


def test_grey_callout_without_monospace_is_not_code_or_table():
    def draw(pdf):
        pdf.setFillColorRGB(0.95, 0.95, 0.95)
        pdf.rect(50, 600, 500, 100, fill=1)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.drawString(60, 675, "ordinary callout")
        pdf.drawString(60, 655, "ordinary second line")

    with pdfplumber.open(make_pdf(draw)) as pdf:
        assert all(block["kind"] == "line" for block in _page_blocks(pdf.pages[0], []))


def test_nested_grey_code_regions_are_not_duplicated():
    def draw(pdf):
        pdf.setFillColorRGB(0.95, 0.96, 0.97)
        pdf.rect(50, 600, 500, 100, fill=1)
        pdf.rect(51, 601, 498, 98, fill=1)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Courier", 10)
        pdf.drawString(60, 675, "# literal heading")
        pdf.drawString(60, 655, "| field | value |")

    result = MarkItDown().convert_stream(make_pdf(draw), file_extension=".pdf").markdown
    assert result.count("```markdown") == 1
    assert result.count("literal heading") == 1
    assert "| field | value |" in result


@pytest.mark.parametrize(
    "name",
    [
        "SPARSE-2024-INV-1234_borderless_table.pdf",
        "RECEIPT-2024-TXN-98765_retail_purchase.pdf",
    ],
)
def test_real_pdf_only_emits_geometry_detected_tables(name):
    path = Path(__file__).parent / "test_files" / name
    if not path.exists():
        pytest.skip("upstream fixture missing")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            expected = [
                table
                for table in page.find_tables()
                if len(table.rows) >= 2
                and max(len(row.cells) for row in table.rows) >= 2
            ]
            blocks = _page_blocks(page, [])
            assert len([block for block in blocks if block["kind"] == "table"]) == len(
                expected
            )
            page.close()
