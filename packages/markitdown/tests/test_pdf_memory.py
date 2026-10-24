"""Page cache cleanup and failure visibility for the baseline converter."""

import io
from unittest.mock import MagicMock, patch

import pytest

from markitdown import StreamInfo
from markitdown.converters._pdf_converter import PdfConverter


def page(number):
    value = MagicMock()
    value.page_number = number
    value.height = 792
    return value


def test_page_cache_closed_before_processing_next_page():
    pages = [page(1), page(2), page(3)]
    opened = MagicMock()
    opened.__enter__.return_value.pages = pages

    def blocks(current, regions):
        if current.page_number > 1:
            pages[current.page_number - 2].close.assert_called_once()
        return []

    with patch(
        "markitdown.converters._pdf_converter.pdfplumber.open", return_value=opened
    ) as reader, patch(
        "markitdown.converters._pdf_converter._page_blocks", side_effect=blocks
    ):
        PdfConverter().convert(io.BytesIO(b"pdf"), StreamInfo(extension=".pdf"))
    reader.assert_called_once()
    for current in pages:
        current.close.assert_called_once()


def test_extraction_error_closes_page_and_is_not_silently_downgraded(caplog):
    current = page(1)
    opened = MagicMock()
    opened.__enter__.return_value.pages = [current]
    with patch(
        "markitdown.converters._pdf_converter.pdfplumber.open", return_value=opened
    ), patch(
        "markitdown.converters._pdf_converter._page_blocks",
        side_effect=ValueError("broken geometry"),
    ):
        with pytest.raises(ValueError, match="broken geometry"):
            PdfConverter().convert(io.BytesIO(b"pdf"), StreamInfo(extension=".pdf"))
    current.close.assert_called_once()
    assert "no_silent_plain_text_fallback=True" in caplog.text
