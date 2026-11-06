"""Regression checks for scan handling, exact watermark text and media ownership."""

import io
import json
import math
from unittest.mock import patch

from PIL import Image
import pytest
from reportlab.pdfgen import canvas

from markitdown import MarkItDown
from markitdown.converters._pdf_media import extension, rotated_text, ocr_words
from markitdown.converters._pdf_raster import PdfRaster


def test_literal_brackets_do_not_become_tex_or_markdown_emphasis():
    from markitdown.converters._pdf_media import literal_text

    text = literal_text("[1, 'apple', True] x^2 ==yes== &amp;")
    assert text == "&#91;1, 'apple', True&#93; x\\^2 \\=\\=yes\\=\\= &amp;amp;"


@pytest.fixture
def image_writer(tmp_path):
    def write(data, suffix, page, role):
        import hashlib

        name = f"p{page}-{role}-{hashlib.sha256(data).hexdigest()[:12]}.{suffix}"
        (tmp_path / name).write_bytes(data)
        return name

    return write


def pdf_bytes(draw):
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(300, 400))
    draw(pdf)
    pdf.save()
    stream.seek(0)
    return stream


def test_rotated_watermark_keeps_reading_order_and_body():
    t = math.pi / 4
    chars = [
        dict(
            text=c,
            matrix=(
                math.cos(t),
                math.sin(t),
                -math.sin(t),
                math.cos(t),
                20 + i * 10 * math.cos(t),
                30 + i * 10 * math.sin(t),
            ),
        )
        for i, c in enumerate("水印Python")
    ]
    body = dict(text="正文", matrix=(1, 0, 0, 1, 20, 300))
    normal, marks = rotated_text(list(reversed(chars)) + [body])
    assert normal == [body]
    assert json.loads(marks[0].splitlines()[1])["text"] == "水印Python"
    assert json.loads(marks[0].splitlines()[1])["angle"] == 45


def test_watermark_fence_cannot_be_closed_by_untrusted_text():
    block = extension("pdf-watermark", text="~~~\n<script>alert(1)</script>")
    assert block.startswith("~~~~pdf-watermark\n")
    assert json.loads(block.splitlines()[1])["text"].startswith("~~~\n")


def test_blank_pdf_never_succeeds_with_only_page_markers():
    stream = pdf_bytes(lambda pdf: pdf.showPage())
    with pytest.raises(Exception, match="PDF 未提取到文字"):
        MarkItDown().convert_stream(stream, file_extension=".pdf")


def test_blank_scan_between_text_pages_preserves_page_numbers(image_writer):
    def draw(pdf):
        pdf.drawString(20, 350, "Before")
        pdf.showPage()
        from reportlab.lib.utils import ImageReader

        pdf.drawImage(
            ImageReader(Image.new("RGB", (300, 400), "white")), 0, 0, 300, 400
        )
        pdf.showPage()
        pdf.drawString(20, 350, "After")
        pdf.showPage()

    with patch(
        "markitdown.converters._pdf_media.ocr_words",
        side_effect=AssertionError("blank scan must not invoke OCR"),
    ):
        text = (
            MarkItDown()
            .convert_stream(
                pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
            )
            .markdown
        )
    assert text.count("<!-- PDF page") == 3 and "Before" in text and "After" in text


def test_missing_chinese_model_is_not_silent_english_only_ocr():
    result = type(
        "Result",
        (),
        dict(
            returncode=0,
            stdout=b"level\ttext\n",
            stderr=b"Failed loading language chi_sim",
        ),
    )()
    with patch(
        "markitdown.converters._pdf_media._ocr_engine", return_value=("tesseract", None)
    ), patch("markitdown.converters._pdf_media.subprocess.run", return_value=result):
        with pytest.raises(RuntimeError, match="模型不可用"):
            ocr_words(Image.new("RGB", (20, 20), "white"))


def test_repeated_image_goes_into_logo_blocks_only(image_writer, tmp_path):
    from reportlab.lib.utils import ImageReader

    image = Image.new("RGB", (60, 20), "blue")

    def draw(pdf):
        for label in ("Page one", "Page two"):
            pdf.drawImage(ImageReader(image), 20, 365, 60, 20)
            pdf.drawString(20, 300, label)
            pdf.showPage()

    text = (
        MarkItDown()
        .convert_stream(
            pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert text.count("~~~pdf-logo") == 2
    assert "![原文插图]" not in text
    assert "Page one" in text and "Page two" in text
    assert "data:image" not in text
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_pdfium_minimal_abi_and_closed_document():
    stream = pdf_bytes(lambda pdf: pdf.drawString(20, 350, "Raster check"))
    doc = PdfRaster(stream)
    image = doc.render(0)
    assert image.size == (600, 800) and image.convert("L").getextrema()[0] < 100
    doc.close()
    doc.close()


def test_row_backgrounds_merge_into_one_code_block():
    def draw(pdf):
        pdf.setFillColorRGB(0.96, 0.95, 0.94)
        for y in (240, 260, 280):
            pdf.rect(20, y, 260, 20, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Courier", 10)
        for y, text in [(286, "function hello() {"), (266, "  return 1;"), (246, "}")]:
            pdf.drawString(30, y, text)

    text = MarkItDown().convert_stream(pdf_bytes(draw), file_extension=".pdf").markdown
    assert text.count("```javascript") == 1 and "\n  return 1;" in text


def test_unsupported_embedded_filter_keeps_render_fallback():
    from markitdown.converters._pdf_media import embedded_image
    from unittest.mock import Mock

    stream = Mock()
    stream.get.return_value = None
    stream.get_data.side_effect = RuntimeError("unsupported PDF image filter")
    assert (
        embedded_image(
            dict(stream=stream, colorspace=["DeviceRGB"], srcsize=(12, 12), bits=8)
        )
        is None
    )


def test_image_conversion_requires_explicit_output_writer():
    from reportlab.lib.utils import ImageReader

    def draw(pdf):
        pdf.drawString(20, 350, "Body")
        pdf.drawImage(ImageReader(Image.new("RGB", (12, 12), "blue")), 20, 100, 12, 12)

    with pytest.raises(Exception, match="pdf_image_writer"):
        MarkItDown().convert_stream(pdf_bytes(draw), file_extension=".pdf")


def test_scan_ocr_does_not_append_whole_page_image(image_writer, tmp_path):
    """扫描底图仅参与 OCR，不再作为核对图或独立插图写出。"""
    from reportlab.lib.utils import ImageReader

    def draw(pdf):
        pdf.drawImage(ImageReader(Image.new("RGB", (300, 400), "gray")), 0, 0, 300, 400)

    with patch(
        "markitdown.converters._pdf_converter.ocr_blocks",
        return_value=([dict(kind="ocr", y=20, x0=20, markdown="OCR body")], 90),
    ):
        text = (
            MarkItDown()
            .convert_stream(
                pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
            )
            .markdown
        )
    assert "OCR body" in text and "![" not in text
    assert "已保留原图" not in text and "页尾原图" not in text
    assert not list(tmp_path.iterdir())


def test_separate_vector_figures_are_cropped_individually(image_writer, tmp_path):
    """独立图形不能合并成包含中间正文的大范围页面截图。"""

    def draw(pdf):
        pdf.drawString(20, 350, "Body outside figures")
        for x, y in ((70, 250), (220, 100)):
            pdf.circle(x, y, 15)
            pdf.line(x + 15, y, x + 35, y)

    text = (
        MarkItDown()
        .convert_stream(
            pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    images = list(tmp_path.glob("*-figure-*.png"))
    assert text.count("![原文图示]") == 2 and len(images) == 2
    for path in images:
        with Image.open(path) as image:
            assert image.width < 300 and image.height < 150


def test_tables_and_code_backgrounds_are_not_exported_as_figures(
    image_writer, tmp_path
):
    """已解析为表格或代码的区域只保留其 Markdown，不再重复生成图片。"""

    def draw(pdf):
        for x in (20, 80, 140):
            pdf.line(x, 20, x, 100)
        for y in (20, 60, 100):
            pdf.line(20, y, 140, y)
        pdf.drawString(30, 80, "Name")
        pdf.drawString(90, 80, "Value")
        pdf.drawString(30, 40, "One")
        pdf.drawString(90, 40, "Two")
        pdf.setFillColorRGB(0.96, 0.95, 0.94)
        pdf.rect(20, 240, 260, 60, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Courier", 10)
        pdf.drawString(30, 280, "function hello() {")
        pdf.drawString(30, 260, "  return 1;")

    text = (
        MarkItDown()
        .convert_stream(
            pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert "| Name | Value |" in text and "```javascript" in text
    assert "![" not in text and not list(tmp_path.iterdir())


def test_ocr_excludes_text_already_owned_by_image():
    """图片内文字不重复输出；像素坐标须先换算为 PDF 页面坐标。"""
    from markitdown.converters._pdf_media import ocr_blocks

    words = [
        dict(
            block_num="1",
            par_num="1",
            line_num="1",
            left=left,
            top=top,
            width=20,
            height=20,
            text=text,
            confidence=90,
        )
        for left, top, text in ((100, 100, "Inside"), (10, 10, "Outside"))
    ]
    with patch("markitdown.converters._pdf_media.ocr_words", return_value=words):
        blocks, _ = ocr_blocks(
            Image.new("RGB", (600, 800)), 300, 400, [(40, 40, 100, 100)]
        )
    assert "Inside" not in " ".join(block["markdown"] for block in blocks)
    assert "Outside" in " ".join(block["markdown"] for block in blocks)


def test_partial_image_preserves_only_area_outside_table():
    from markitdown.converters._pdf_media import _outside_regions, _boxes_overlap

    table = (20, 20, 80, 80)
    regions = _outside_regions((0, 0, 100, 100), [table])
    assert sum((box[2] - box[0]) * (box[3] - box[1]) for box in regions) == 6400
    assert all(not _boxes_overlap(box, table) for box in regions)
    assert all(
        not _boxes_overlap(box, other)
        for index, box in enumerate(regions)
        for other in regions[index + 1 :]
    )


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("line_width", [0.48, 4])
def test_table_border_overhang_is_not_exported_as_strips(
    image_writer, tmp_path, merged, line_width
):
    """略超出网格边界的粗细描边归属表格，表外独立图形仍正常导出。"""

    def draw(pdf):
        pdf.drawString(20, 380, "Body outside table")
        pdf.setLineWidth(line_width)
        for x in (40, 150, 260):
            top = 180 if merged and x == 150 else 260
            pdf.line(x, 99.76, x, top + 0.24)
        for y in (100, 180, 260):
            pdf.line(39.76, y, 260.24, y)
        for x, y, text in (
            (50, 235, "Name"),
            (160, 235, "Value"),
            (50, 145, "One"),
            (160, 145, "Two"),
        ):
            pdf.drawString(x, y, text)
        pdf.setLineWidth(0.48)
        pdf.circle(280, 330, 8)
        pdf.line(280, 322, 280, 306)

    text = (
        MarkItDown()
        .convert_stream(
            pdf_bytes(draw), file_extension=".pdf", pdf_image_writer=image_writer
        )
        .markdown
    )
    assert text.count("![原文表格]") == int(merged)
    assert text.count("![原文图示]") == 1
    assert len(list(tmp_path.glob("*-figure-*.png"))) == 1 + int(merged)
    if not merged:
        assert "| Name | Value |" in text
