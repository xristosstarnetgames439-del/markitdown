"""Only drawn borders can create tables; aligned text must remain prose."""

import io
from pathlib import Path

import pdfplumber
import pytest
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from markitdown import MarkItDown
from markitdown.converters._pdf_converter import _page_blocks
from markitdown.converters._pdf_media import _box_inside


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
            regions = [
                block["bbox"]
                for block in blocks
                if block["kind"] in {"table", "table_image"}
            ]
            assert all(
                any(_box_inside(table.bbox, region) for region in regions)
                for table in expected
            )
            assert all(
                region in [table.bbox for table in expected] for region in regions
            )
            page.close()


@pytest.fixture
def image_writer(tmp_path):
    """记录实际写出的图片，便于检查一个表格只输出一次。"""
    written = []

    def write(data, suffix, page, role):
        name = f"p{page}-{role}-{len(written)}.{suffix}"
        (tmp_path / name).write_bytes(data)
        written.append(name)
        return name

    return write


def test_regular_table_keeps_empty_cells_without_images(image_writer, tmp_path):
    def draw(pdf):
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(0, 0, 612, 792, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        for y in (600, 650, 700):
            pdf.line(50, y, 350, y)
        for x in (50, 150, 350):
            pdf.line(x, 600, x, 700)
        pdf.drawString(60, 675, "ID")
        pdf.drawString(160, 675, "Description")
        pdf.drawString(60, 625, "A")

    text = (
        MarkItDown()
        .convert_stream(
            make_pdf(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert "| ID | Description |" in text and "| A |  |" in text
    assert "![" not in text and not list(tmp_path.iterdir())


@pytest.mark.parametrize("merge", ["columns", "rows"])
def test_merged_table_is_one_local_image_without_duplicate_text(
    merge, image_writer, tmp_path
):
    def draw(pdf):
        pdf.rect(50, 600, 300, 100)
        if merge == "columns":
            pdf.line(50, 650, 350, 650)
            pdf.line(150, 600, 150, 650)
        else:
            pdf.line(150, 600, 150, 700)
            pdf.line(150, 650, 350, 650)
        pdf.drawString(60, 675, "MergedCell")
        pdf.drawString(160, 625, "InsideCell")
        pdf.drawString(50, 730, "Before")
        pdf.drawString(50, 570, "After")

    text = (
        MarkItDown()
        .convert_stream(
            make_pdf(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    images = list(tmp_path.iterdir())
    assert text.count("![原文表格]") == 1 and len(images) == 1
    assert "MergedCell" not in text and "InsideCell" not in text and "| ---" not in text
    assert text.index("Before") < text.index("![原文表格]") < text.index("After")
    with Image.open(images[0]) as image:
        # 允许描边与坐标容差，仍必须是表格局部裁剪，不能扩大为整页图。
        assert 900 <= image.width <= 912 and 300 <= image.height <= 312


def test_cell_image_is_owned_by_one_table_crop(image_writer, tmp_path):
    def draw(pdf):
        for y in (600, 650, 700):
            pdf.line(50, y, 350, y)
        for x in (50, 150, 350):
            pdf.line(x, 600, x, 700)
        pdf.drawString(60, 675, "ID")
        pdf.drawString(160, 675, "Picture")
        pdf.drawString(60, 625, "One")
        pdf.drawImage(ImageReader(Image.new("RGB", (20, 20), "red")), 170, 610, 20, 20)

    text = (
        MarkItDown()
        .convert_stream(
            make_pdf(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert text.count("![原文表格]") == 1 and len(list(tmp_path.iterdir())) == 1
    assert "![原文插图]" not in text and "| ---" not in text and "Picture" not in text


def test_nested_table_uses_only_outer_crop(image_writer, tmp_path):
    def draw(pdf):
        for y in (500, 600, 700):
            pdf.line(50, y, 350, y)
        for x in (50, 150, 350):
            pdf.line(x, 500, x, 700)
        for y in (610, 640, 680):
            pdf.line(180, y, 300, y)
        for x in (180, 240, 300):
            pdf.line(x, 610, x, 680)
        pdf.drawString(60, 675, "Outer")
        pdf.drawString(190, 655, "Nested")

    text = (
        MarkItDown()
        .convert_stream(
            make_pdf(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert text.count("![原文表格]") == 1 and len(list(tmp_path.iterdir())) == 1
    assert "Outer" not in text and "Nested" not in text and "| ---" not in text
