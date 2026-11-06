"""Text-layer PDFs: baseline reading order and explicitly ruled tables only."""

import json
import logging
import re
import statistics
import sys
from collections import Counter
from typing import Any, BinaryIO

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import MissingDependencyException, MISSING_DEPENDENCY_MESSAGE
from .._stream_info import StreamInfo
from ._pdf_media import (
    PdfMedia,
    _box,
    _box_inside,
    _boxes_overlap,
    ocr_blocks,
    rotated_text,
    literal_text as _literal,
)

PDF_STRUCTURE_ALGORITHM = "huiwen-rich-pdf-v3"
_logger = logging.getLogger(__name__)
_dependency_exc_info = None
try:
    import pdfplumber
except ImportError:
    _dependency_exc_info = sys.exc_info()

PARTIAL_NUMBERING_PATTERN = re.compile(r"^\.\d+$")
_BOLD = re.compile(r"bold|black|heavy|demi", re.I)
_MONO = re.compile(r"courier|consolas|monaco|mono|menlo|sourcecode", re.I)
_LABELS = {"目的", "重要规则"}
_BULLET = re.compile(r"^[•●▪◦]\s*")
_LIST = re.compile(r"^(?:[-*+]\s+|\d+[.)、]\s+|\.\d+\s+)")


def _merge_partial_numbering_lines(text: str) -> str:
    return re.sub(r"(?m)^(\.\d+)\s*\n\s*(?=\S)", r"\1 ", text)


def _baseline(char: dict, height: float) -> float:
    matrix = char.get("matrix")
    return (
        height - matrix[5] if matrix and char.get("upright", True) else char["bottom"]
    )


def _contains(box: tuple, char: dict, height: float) -> bool:
    x = (char["x0"] + char["x1"]) / 2
    y = _baseline(char, height)
    return box[0] <= x < box[2] and box[1] - 0.6 <= y < box[3]


def _join_text(left: str, right: str) -> str:
    if not left or not right:
        return left + right
    if left.endswith("**") and right.startswith("**"):
        return _join_text(left[:-2], right[2:])
    # Preserve word boundaries for English; Chinese layout wraps need no extra space.
    space = bool(
        re.search(r"[A-Za-z0-9]$", left.rstrip("*"))
        and re.match(r"[A-Za-z0-9]", right.lstrip("*"))
    )
    return left.rstrip() + (" " if space else "") + right.lstrip()


def _line(chars: list[dict], height: float) -> dict:
    chars = sorted(chars, key=lambda char: char["x0"])
    raw, styled, run = "", "", ""
    bold = False
    previous = None
    for char in chars:
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", char["text"])
        is_bold = bool(_BOLD.search(char.get("fontname", "")))
        gap = char["x0"] - previous["x1"] if previous else 0
        space = (
            " "
            if previous
            and gap > max(0.7, min(char["size"], previous["size"]) * 0.15)
            and not raw.endswith(" ")
            and not value.startswith(" ")
            else ""
        )
        raw += space + value
        if is_bold != bold:
            styled += "**" + run.strip() + "**" if bold and run.strip() else run
            run = ""
            bold = is_bold
        run += space + _literal(value)
        previous = char
    styled += "**" + run.strip() + "**" if bold and run.strip() else run
    nonblank = [char for char in chars if char["text"].strip()]
    sizes = Counter(round(char["size"] * 2) / 2 for char in nonblank)
    return dict(
        kind="line",
        text=raw.strip(),
        markdown=styled.strip(),
        x0=chars[0]["x0"],
        text_x0=nonblank[0]["x0"] if nonblank else chars[0]["x0"],
        x1=max(char["x1"] for char in chars),
        y=statistics.median(_baseline(char, height) for char in chars),
        size=sizes.most_common(1)[0][0] if sizes else 0,
        chars=len(nonblank),
    )


def _lines(chars: list[dict], height: float) -> list[dict]:
    """Group mixed CJK/Latin glyphs by baseline, never by glyph top."""
    groups: list[list[dict]] = []
    for char in sorted(chars, key=lambda char: (_baseline(char, height), char["x0"])):
        if not char.get("text"):
            continue
        if (
            not groups
            or abs(_baseline(char, height) - _baseline(groups[-1][0], height)) > 0.6
        ):
            groups.append([])
        groups[-1].append(char)
    return [line for group in groups if (line := _line(group, height))["text"]]


def _paragraphs(lines: list[dict], *, cell: bool = False) -> str:
    output: list[str] = []
    previous = None
    for line in lines:
        text = line["text"] if cell else line["markdown"]
        bullet = _BULLET.match(line["text"])
        if bullet:
            # Remove the original marker before styling, without rewriting its content.
            content = line["text"][bullet.end() :]
            text = "- " + (content if cell else _literal(content))
        new = previous is None or bullet or _LIST.match(line["text"])
        if previous:
            new = (
                new
                or line["y"] - previous["y"]
                > max(line["size"], previous["size"]) * 1.65
            )
            new = (
                new
                or abs(line["x0"] - previous["x0"])
                > max(line["size"], previous["size"]) * 2
            )
        if new:
            output.append(text)
        else:
            output[-1] = _join_text(output[-1], text)
        previous = line
    return _merge_partial_numbering_lines(("<br>" if cell else "\n\n").join(output))


def _table_image_reason(table, page, detected):
    """只将完整、无合并且列边界一致的网格交给 Markdown 表格渲染。"""
    rows = [row.cells for row in table.rows]
    if any(
        len(row) != len(rows[0]) or any(cell is None for cell in row) for row in rows
    ):
        return "merged_or_missing_cell"
    first = rows[0]
    xs = [first[0][0], *[cell[2] for cell in first]]
    ys = [row[0][1] for row in rows] + [rows[-1][0][3]]
    close = lambda left, right: abs(left - right) <= 1
    for index, row in enumerate(rows):
        if any(
            not close(cell[0], xs[column])
            or not close(cell[2], xs[column + 1])
            or not close(cell[1], ys[index])
            or not close(cell[3], ys[index + 1])
            for column, cell in enumerate(row)
        ):
            return "irregular_grid"
    if any(
        other is not table and _box_inside(other.bbox, table.bbox) for other in detected
    ):
        return "nested_table"
    if any(_boxes_overlap(_box(image), table.bbox) for image in page.images):
        return "cell_image"
    for obj in page.curves + page.rects + page.lines:
        box = _box(obj)
        if not _boxes_overlap(box, table.bbox) or not (
            obj.get("stroke") or obj.get("fill")
        ):
            continue
        if (
            obj.get("object_type") == "rect"
            and _box_inside(table.bbox, box)
            and not _box_inside(box, table.bbox)
        ):
            continue  # 页背景／外框不是单元格里的图形。
        # 网格边框和整格底色可表达为普通表格；格内绘图不能压平为文字。
        if obj.get("object_type") == "rect" and all(
            any(close(value, edge) for edge in edges)
            for value, edges in zip(box, (xs, ys, xs, ys))
        ):
            continue
        if obj.get("object_type") == "curve":
            current = start = None
            on_grid = True
            for operation, *points in obj.get("path", []):
                if operation == "m":
                    current = start = points[0]
                    continue
                if operation not in {"l", "h"} or current is None:
                    on_grid = False
                    break
                target = start if operation == "h" else points[0]
                if not (
                    (
                        close(current[0], target[0])
                        and any(close(current[0], x) for x in xs)
                    )
                    or (
                        close(current[1], target[1])
                        and any(close(current[1], y) for y in ys)
                    )
                ):
                    on_grid = False
                    break
                current = target
            if on_grid and current is not None:
                continue
        return "cell_graphic"
    return ""


def _table_text(table: Any, assigned: dict[tuple, list[dict]], height: float) -> str:
    """仅渲染通过网格检查的表格，不把合并单元格补成虚构空格。"""
    rows = []
    for row in table.rows:
        cells = []
        for box in row.cells:
            lines = _lines(assigned.get(tuple(box), []), height)
            # Narrow identifier columns are stacked labels; prose cells unwrap layout lines.
            stacked = (
                lines
                and all(len(line["text"]) <= 20 for line in lines)
                and (box[2] - box[0] < 160 or re.fullmatch(r"R\d+", lines[0]["text"]))
            )
            value = (
                "<br>".join(line["text"] for line in lines)
                if stacked
                else _paragraphs(lines, cell=True)
            )
            if stacked:
                metadata = re.fullmatch(
                    r"(R\d+)\s*(.*?)\s*(\d{4}-\s*\d{2}-\s*\d{2})",
                    "".join(line["text"] for line in lines),
                )
                if metadata:
                    value = "<br>".join(
                        part
                        for part in (
                            metadata[1],
                            metadata[2].strip(),
                            re.sub(r"\s", "", metadata[3]),
                        )
                        if part
                    )
            cells.append("<br>".join(_literal(part) for part in value.split("<br>")))
        rows.append(cells)
    width = len(rows[0])
    rendered = ["| " + " | ".join(row) + " |" for row in rows]
    rendered.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(rendered)


def _code_boxes(page: Any, explicit: list[dict]) -> list[dict]:
    """Only neutral filled rectangles with monospace text are code candidates."""
    if explicit:
        return explicit
    boxes = []
    backgrounds = []
    for rect in page.rects + page.curves:
        color = rect.get("non_stroking_color")
        color = (color,) if isinstance(color, (int, float)) else color
        if (
            not rect.get("fill")
            or not isinstance(color, (tuple, list))
            or len(color) not in (1, 3)
        ):
            continue
        if min(color) < 0.85 or max(color) >= 0.995 or max(color) - min(color) > 0.08:
            continue
        box = (rect["x0"], rect["top"], rect["x1"], rect["bottom"])
        backgrounds.append(box)
    # Word/PDF exporters often paint a code background one row at a time.
    # Merge touching rows with the same horizontal extent before testing the font.
    merged = []
    for box in sorted(backgrounds, key=lambda b: (b[0], b[2], b[1])):
        if (
            merged
            and abs(box[0] - merged[-1][0]) < 1
            and abs(box[2] - merged[-1][2]) < 1
            and abs(box[1] - merged[-1][3]) < 1
        ):
            previous = merged[-1]
            merged[-1] = (previous[0], previous[1], previous[2], box[3])
        else:
            merged.append(box)
    for box in sorted(
        merged, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True
    ):
        if any(
            other["bbox"][0] <= box[0]
            and other["bbox"][1] <= box[1]
            and other["bbox"][2] >= box[2]
            and other["bbox"][3] >= box[3]
            for other in boxes
        ):
            continue
        inside = [char for char in page.chars if _contains(box, char, page.height)]
        if (
            sum(bool(_MONO.search(char.get("fontname", ""))) for char in inside) >= 8
            and len(_lines(inside, page.height)) >= 2
        ):
            boxes.append({"bbox": box})
    return boxes


def _code_text(chars: list[dict], height: float, language: str = "") -> str:
    lines = _lines(chars, height)
    if not lines:
        return ""
    origin = min(line["text_x0"] for line in lines)
    widths = [
        char["x1"] - char["x0"]
        for char in chars
        if _MONO.search(char.get("fontname", "")) and char["x1"] > char["x0"]
    ]
    unit = statistics.median(widths) if widths else lines[0]["size"] * 0.6
    text_lines = []
    spacings = [b["y"] - a["y"] for a, b in zip(lines, lines[1:])]
    spacing = statistics.median(spacings) if spacings else 0
    previous = None
    for line in lines:
        if previous and spacing and line["y"] - previous["y"] > spacing * 1.6:
            text_lines.append("")
        text_lines.append(
            " " * max(0, round((line["text_x0"] - origin) / unit)) + line["text"]
        )
        previous = line
    text = "\n".join(text_lines)
    if not language:
        # Printed line numbers remain in the output; ignore them only for language detection.
        probe = re.sub(r"(?m)^\s*\d+\s+", "", text)
        language = (
            "python"
            if re.search(
                r"(?m)^\s*(?:def |class \w+.*:|from \w+ import |import \w|for .* in .*:|if .*:|print\()",
                probe,
            )
            else (
                "javascript"
                if re.search(
                    r"(?m)^\s*(?:function\b|(?:var|let|const)\s|console\.|for\s*\()",
                    probe,
                )
                else (
                    "json"
                    if text.lstrip().startswith(("{", "["))
                    else "markdown" if re.search(r"(?m)^#{1,6}\s", text) else "text"
                )
            )
        )
    if language == "json":
        # Unwrap only newlines inside JSON strings, leaving all content characters intact.
        repaired, quoted, escaped = "", False, False
        for part in text.splitlines():
            if repaired:
                repaired += "" if quoted else "\n"
            part = part.lstrip() if quoted else part
            repaired += part
            for char in part:
                if escaped:
                    escaped = False
                elif char == "\\" and quoted:
                    escaped = True
                elif char == '"':
                    quoted = not quoted
        try:
            json.loads(repaired)
            text = repaired
        except ValueError:
            _logger.warning("CODE_JSON_INVALID preserving_extracted_text=True")
    fence = "`" * max(
        3, max((len(match[0]) + 1 for match in re.finditer(r"`+", text)), default=3)
    )
    return f"{fence}{language}\n{text}\n{fence}"


def _page_blocks(page: Any, code_regions: list[dict]) -> list[dict]:
    # Explicit line strategies never infer a table from aligned words or paragraph spacing.
    detected = [
        table
        for table in page.find_tables(
            {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
        )
        if len(table.rows) >= 2 and max(len(row.cells) for row in table.rows) >= 2
    ]
    tables = []
    for table in sorted(
        detected,
        key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]),
        reverse=True,
    ):
        if not any(_box_inside(table.bbox, owner.bbox) for owner in tables):
            tables.append(table)
    tables.sort(key=lambda table: (table.bbox[1], table.bbox[0]))
    code_boxes = _code_boxes(page, code_regions)
    code_boxes = [
        box
        for box in code_boxes
        if not any(_box_inside(box["bbox"], table.bbox) for table in tables)
    ]
    table_chars: list[dict[tuple, list[dict]]] = [{} for table in tables]
    code_chars: list[list[dict]] = [[] for box in code_boxes]
    plain = []
    for char in page.chars:
        found = False
        for index, table in enumerate(tables):
            if not _contains(table.bbox, char, page.height):
                continue
            # Each glyph is owned once, including glyphs touching a cell's lower border.
            cell = next(
                (
                    tuple(box)
                    for row in table.rows
                    for box in row.cells
                    if box and _contains(box, char, page.height)
                ),
                None,
            )
            if cell is not None:
                table_chars[index].setdefault(cell, []).append(char)
            # 整块表格统一占有文字，缺失／合并格中的字也不能再次流入正文。
            found = True
            break
        if found:
            continue
        for index, box in enumerate(code_boxes):
            if _contains(box["bbox"], char, page.height):
                code_chars[index].append(char)
                found = True
                break
        if not found:
            plain.append(char)
    blocks = _lines(plain, page.height)
    for table, owned in zip(tables, table_chars):
        reason = _table_image_reason(table, page, detected)
        blocks.append(
            dict(
                kind="table_image" if reason else "table",
                bbox=table.bbox,
                y=table.bbox[1],
                x0=table.bbox[0],
                markdown="" if reason else _table_text(table, owned, page.height),
            )
        )
        _logger.info(
            "TABLE_OUTPUT page=%d mode=%s reason=%s bbox=%s",
            page.page_number,
            "image" if reason else "markdown",
            reason or "regular_grid",
            table.bbox,
        )
    blocks += [
        dict(
            kind="code",
            bbox=box["bbox"],
            y=box["bbox"][1],
            x0=box["bbox"][0],
            markdown=_code_text(chars, page.height, box.get("language", "")),
        )
        for box, chars in zip(code_boxes, code_chars)
    ]
    _logger.info(
        "PAGE page=%d chars=%d lines=%d ruled_tables=%d code_blocks=%d",
        page.page_number,
        len(page.chars),
        len(blocks) - len(tables) - len(code_boxes),
        len(tables),
        len(code_boxes),
    )
    return sorted(blocks, key=lambda block: (block["y"], block["x0"]))


def _render_pages(pages: list[tuple[float, list[dict]]]) -> str:
    sizes: Counter = Counter()
    for height, blocks in pages:
        for line in blocks:
            if line["kind"] == "line" and line["y"] < height * 0.94:
                sizes[line["size"]] += line["chars"]
    body = sizes.most_common(1)[0][0] if sizes else 10
    heading_sizes = sorted(
        {size for size in sizes if size >= body + max(1, body * 0.1)}, reverse=True
    )
    levels = {size: min(index + 1, 6) for index, size in enumerate(heading_sizes)}
    _logger.info(
        "BODY size=%.2f sizes=%s heading_levels=%s", body, sizes.most_common(12), levels
    )
    output = []
    for page_number, (height, blocks) in enumerate(pages, 1):
        paragraphs: list[dict] = []
        rendered: list[str] = []

        def flush():
            if paragraphs:
                rendered.append(_paragraphs(paragraphs))
                paragraphs.clear()

        for index, block in enumerate(blocks):
            if block["kind"] != "line":
                flush()
                rendered.append(block["markdown"])
                continue
            level = levels.get(block["size"])
            if block["text"] in _LABELS:
                level = 3
            footer = block["y"] > height * 0.94 and block["size"] < body * 0.9
            if footer:
                flush()
                import html

                rendered.append("<small>" + html.escape(block["text"]) + "</small>")
            elif (
                level
                and len(block["text"]) <= 120
                and not re.search(r"[。！？；]$", block["text"])
            ):
                flush()
                marker = "#" * level + " "
                if (
                    rendered
                    and rendered[-1].startswith(marker)
                    and block.get("previous_heading_size") == block["size"]
                ):
                    rendered[-1] = _join_text(rendered[-1], block["text"])
                else:
                    rendered.append(marker + _literal(block["text"]))
                # Only directly adjacent, same-size heading lines are merged below.
            else:
                paragraphs.append(block)
            # Store adjacency on the next line, without relying on textual equality.
            if level and not footer and index + 1 < len(blocks):
                following = blocks[index + 1]
                if (
                    following["kind"] == "line"
                    and following["y"] - block["y"] <= block["size"] * 1.5
                ):
                    following["previous_heading_size"] = block["size"]
        flush()
        _logger.info(
            "RENDER page=%d headings=%d tables=%d code_blocks=%d",
            page_number,
            sum(bool(re.match(r"^#{1,6} ", block)) for block in rendered),
            sum(block["kind"] == "table" for block in blocks),
            sum(block["kind"] == "code" for block in blocks),
        )
        output.append(f"<!-- PDF page {page_number} -->\n\n" + "\n\n".join(rendered))
    return "\n\n".join(output).strip()


class PdfConverter(DocumentConverter):
    """Convert a text layer using baseline order and drawn cell boundaries."""

    def accepts(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> bool:
        return (stream_info.extension or "").lower() == ".pdf" or (
            stream_info.mimetype or ""
        ).lower().startswith(("application/pdf", "application/x-pdf"))

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        _logger.info("PDF_START algorithm=%s", PDF_STRUCTURE_ALGORITHM)
        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__, extension=".pdf", feature="pdf"
                )
            ) from _dependency_exc_info[1]
        pages = []
        media = PdfMedia(file_stream, kwargs.get("pdf_image_writer"))
        extracted = 0
        # Callers may explicitly set code regions by 1-based page; no sample coordinates are hardcoded.
        code_regions = kwargs.get("pdf_code_regions", {})
        try:
            with pdfplumber.open(file_stream) as pdf:
                media.scan(pdf)
                for page in pdf.pages:
                    try:
                        chars, watermarks = rotated_text(page.chars)
                        usable = [c for c in chars if c.get("text", "").strip()]
                        blocks = []
                        if usable:
                            # 先确定正文表格和代码区域，避免把它们再次裁成图片。
                            removed = {id(c) for c in page.chars} - {
                                id(c) for c in chars
                            }
                            body_page = (
                                page.filter(lambda obj: id(obj) not in removed)
                                if removed
                                else page
                            )
                            blocks = _page_blocks(
                                body_page, code_regions.get(page.page_number, [])
                            )
                        vector_marks, logos, images = media.page(
                            page,
                            bool(usable),
                            [block["bbox"] for block in blocks if "bbox" in block],
                            table_regions=[
                                dict(
                                    bbox=block["bbox"],
                                    as_image=block["kind"] == "table_image",
                                )
                                for block in blocks
                                if block["kind"] in {"table", "table_image"}
                            ],
                        )
                        blocks = [
                            block for block in blocks if block["kind"] != "table_image"
                        ]
                        watermarks += vector_marks
                        if not usable and (page.images or page.curves):
                            bitmap = media.bitmap(page)
                            histogram = bitmap.convert("L").histogram()
                            if (
                                sum(histogram[:220]) / (bitmap.width * bitmap.height)
                                < 0.00005
                            ):
                                blocks = images
                                _logger.info("BLANK_SCAN page=%d", page.page_number)
                            else:
                                blocks, confidence = ocr_blocks(
                                    bitmap,
                                    page.width,
                                    page.height,
                                    [
                                        block["bbox"]
                                        for block in images
                                        if "bbox" in block
                                    ],
                                )
                                message = (
                                    "本页为 OCR 识别，复杂表格和公式请对照原 PDF 核对。"
                                    if blocks
                                    else "本页未识别到可靠文字，请对照原 PDF 人工核对。"
                                )
                                if blocks or not images:
                                    blocks.insert(
                                        0,
                                        dict(
                                            kind="notice",
                                            y=-1,
                                            x0=0,
                                            markdown="> " + message,
                                        ),
                                    )
                                blocks.extend(images)
                        else:
                            blocks += images
                            blocks.sort(key=lambda block: (block["y"], block["x0"]))
                        extracted += sum(
                            len(b.get("text", b["markdown"]))
                            for b in blocks
                            if b["kind"] != "notice"
                        )
                        blocks = [
                            dict(kind="watermark", y=-2, x0=0, markdown=m)
                            for m in watermarks
                        ] + blocks
                        blocks += [
                            dict(kind="logo", y=page.height + 1, x0=0, markdown=m)
                            for m in logos
                        ]
                        pages.append(
                            (
                                page.height,
                                blocks,
                            )
                        )
                    finally:
                        page.close()
            if not extracted:
                raise ValueError("PDF 未提取到文字或图像；不能仅以页码标记判定转换成功")
            markdown = _render_pages(pages)
            if len(markdown.encode("utf-8")) > 16 * 1024 * 1024:
                raise RuntimeError(
                    "转换结果超过当前预览的 16 MiB 上限，请拆分 PDF 后转换"
                )
        except Exception:
            _logger.exception("PDF_FAILED no_silent_plain_text_fallback=True")
            raise
        finally:
            media.close()
        _logger.info("PDF_DONE output_chars=%d pages=%d", len(markdown), len(pages))
        return DocumentConverterResult(markdown=markdown)
