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
