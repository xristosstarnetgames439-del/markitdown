"""Offline PDF media/OCR helpers. No network, browser or application bridge is used."""

from collections import defaultdict
import csv
import hashlib
import io
import json
import logging
import math
import os
import re
from ._pdf_raster import PdfRaster
from pathlib import Path
import shutil
import subprocess
import sys

_logger = logging.getLogger(__name__)


def literal_text(text):
    """Keep source brackets literal, distinct from Markdown's LaTeX delimiters."""
    text = re.sub(r"([\\`*_{}<>#+!|~$^=])", r"\\\1", text.replace("&", "&amp;"))
    return text.replace("[", "&#91;").replace("]", "&#93;")


def extension(kind, **data):
    """A fenced JSON payload cannot turn extracted text into executable markup."""
    text = json.dumps(data, ensure_ascii=False)
    # JSON may contain Markdown fences; a tilde fence with a longer run is unambiguous.
    import re

    fence = "~" * max(
        3, max((len(m[0]) + 1 for m in re.finditer(r"~+", text)), default=3)
    )
    return f"{fence}{kind}\n{text}\n{fence}"


def angle(char):
    m = char.get("matrix", (1, 0, 0, 1, 0, 0))
    return math.degrees(math.atan2(m[1], m[0]))


def rotated_text(chars):
    """Use each glyph's origin projected onto the rotated baseline, not its bbox."""
    normal, groups = [], defaultdict(list)
    for char in chars:
        a = angle(char)
        if 8 < abs(a) < 82 and char.get("text", "").strip():
            groups[round(a)].append(char)
        else:
            normal.append(char)
    watermarks = []
    for degrees, glyphs in groups.items():
        theta = math.radians(degrees)
        cs, sn = math.cos(theta), math.sin(theta)
        rows = defaultdict(list)
        for c in glyphs:
            x, y = c["matrix"][4:6]
            rows[round((-x * sn + y * cs) / 3)].append((x * cs + y * sn, c["text"]))
        texts = [
            "".join(text for _, text in sorted(row))
            for _, row in sorted(rows.items(), reverse=True)
        ]
        text = "\n".join(dict.fromkeys(texts))
        if text:
            watermarks.append(
                extension(
                    "pdf-watermark", text=text, angle=degrees, source="text-layer"
                )
            )
    return normal, watermarks


def _ocr_engine():
    # Packaged Python and Tesseract share the same private HNP prefix.
    prefix = Path(sys.executable).resolve().parent.parent
    binary = os.environ.get("HUIWEN_TESSERACT") or str(prefix / "bin/tesseract")
    if not Path(binary).is_file():
        binary = shutil.which("tesseract")
    if not binary:
        raise RuntimeError("PDF 需要 OCR，但 HNP 未包含 Tesseract；请重新准备原生依赖")
    data = os.environ.get("HUIWEN_TESSDATA")
    packaged = prefix / "share/tessdata"
    if not data and packaged.is_dir():
        data = str(packaged)
    return binary, data


def ocr_words(image, *, psm=3):
    """TSV gives coordinates/confidence; PNM avoids native image codec dependencies."""
    binary, data = _ocr_engine()
    command = [
        binary,
        "stdin",
        "stdout",
        "-l",
        os.environ.get("HUIWEN_OCR_LANGUAGE", "chi_sim"),
        "--oem",
        "1",
        "--psm",
        str(psm),
        "-c",
        "tessedit_create_tsv=1",
        "-c",
        "tessedit_load_sublangs=",
    ]
    if data:
        command += ["--tessdata-dir", data]
    stream = io.BytesIO()
    image.convert("RGB").save(stream, format="PPM")
    result = subprocess.run(
        command,
        input=stream.getvalue(),
        capture_output=True,
        timeout=90,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    diagnostic = result.stderr.decode("utf-8", "replace")
    if result.returncode or "Failed loading language" in diagnostic:
        raise RuntimeError("OCR 组件或中英文模型不可用：" + diagnostic[-1000:])
    words = []
    for row in csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8")),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    ):
        text = row.get("text", "").strip()
        if row.get("level") != "5" or not text:
            continue
        row = {
            **row,
            **{key: int(row[key]) for key in ("left", "top", "width", "height")},
        }
        row["confidence"] = float(row["conf"])
        words.append(row)
    return words


def ocr_blocks(image, width, height):
    words = ocr_words(image)
    lines = defaultdict(list)
    for word in words:
        lines[(word["block_num"], word["par_num"], word["line_num"])].append(word)
    blocks = []
    for line in lines.values():
        line.sort(key=lambda w: w["left"])
        text = " ".join(w["text"] for w in line)
        # Tesseract separates CJK words with spaces; preserve Latin word boundaries.
        import re

        text = re.sub(
            r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff，。；：！？、])", "", text
        )
        text = re.sub(r"(?<=[，。；：！？、])\s+(?=[\u3400-\u9fff])", "", text)
        blocks.append(
            dict(
                kind="ocr",
                y=min(w["top"] for w in line) * height / image.height,
                x0=min(w["left"] for w in line) * width / image.width,
                markdown=literal_text(text),
            )
        )
    confidence = sum(w["confidence"] for w in words) / max(1, len(words))
    _logger.info("OCR words=%d mean_confidence=%.1f", len(words), confidence)
    return blocks, confidence


def image_bytes(image, role):
    """Keep image bytes outside Markdown; lossless for logos/isolated outlines."""
    stream = io.BytesIO()
    suffix = "png" if role in {"logo", "watermark", "figure"} else "jpg"
    if suffix == "png":
        image.save(stream, "PNG")
    else:
        image.convert("RGB").save(stream, "JPEG", quality=90, optimize=True)
    return stream.getvalue(), suffix


def embedded_image(obj):
    """Decode standalone PDF images; masks/special colour spaces use page rendering."""
    from PIL import Image

    stream = obj.get("stream")
    if (
        stream is None
        or any(stream.get(k) is not None for k in ("SMask", "Mask", "Decode"))
        or obj.get("imagemask")
    ):
        return None
    colors = obj.get("colorspace") or []
    names = [getattr(color, "name", str(color)) for color in colors]
    if names not in (["DeviceRGB"], ["DeviceGray"], ["RGB"], ["G"]):
        return None
    try:
        width, height = map(int, obj["srcsize"])
        if width * height > 16_000_000:
            return None
        data = stream.get_data()
        if len(data) > 32 * 1024 * 1024:
            return None
        if data.startswith(b"\xff\xd8"):
            image = Image.open(io.BytesIO(data))
            image.load()
            if image.mode in {"RGB", "L"}:
                return data, "jpg"
            return None
        if obj.get("bits") == 8:
            mode = "RGB" if names[0] in {"DeviceRGB", "RGB"} else "L"
            size = tuple(map(int, obj["srcsize"]))
            if size[0] * size[1] > 3200 * 3200:
                return None
            image = Image.frombytes(mode, size, data)
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue(), "png"
    except Exception as error:
        # Unsupported PDF filters/colour models must still retain the rendered region.
        _logger.info("IMAGE_DECODE_FALLBACK reason=%s", error)
    return None


def _box(obj):
    return obj["x0"], obj["top"], obj["x1"], obj["bottom"]


def _union(boxes):
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def media_candidates(page):
    """Find repeatable image objects and small connected vector groups, not page backgrounds."""
    candidates = []
    area = page.width * page.height
    for image_index, obj in enumerate(page.images):
        box = _box(obj)
        if box[2] <= 0 or box[3] <= 0 or box[0] >= page.width or box[1] >= page.height:
            continue
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        stream = obj.get("stream")
        key = hashlib.sha256(stream.get_rawdata() or b"").hexdigest() if stream else ""
        candidates.append(
            dict(key="image-" + key, bbox=box, kind="image", image_index=image_index)
        )
    # Connect nearby paths of the same paint. Grid buckets avoid quadratic page-wide scans.
    groups, grid = [], defaultdict(set)
    for curve in page.curves:
        box = _box(curve)
        if box[2] <= 0 or box[3] <= 0 or box[0] >= page.width or box[1] >= page.height:
            continue
        w, h = box[2] - box[0], box[3] - box[1]
        if not curve.get("fill") or w <= 0 or h <= 0 or w * h > area * 0.03:
            continue
        color = str(curve.get("non_stroking_color"))
        cells = [
            (x, y, color)
            for x in range(int((box[0] - 3) // 24), int((box[2] + 3) // 24) + 1)
            for y in range(int((box[1] - 3) // 24), int((box[3] + 3) // 24) + 1)
        ]
        near = set().union(*(grid[c] for c in cells))
        matches = []
        for i in near:
            if not groups[i]:
                continue
            other = _union([_box(c) for c in groups[i]])
            if (
                box[0] <= other[2] + 3
                and box[2] >= other[0] - 3
                and box[1] <= other[3] + 3
                and box[3] >= other[1] - 3
            ):
                matches.append(i)
        i = min(matches) if matches else len(groups)
        if not matches:
            groups.append([])
        for j in matches:
            if j != i:
                groups[i].extend(groups[j])
                groups[j] = []
                # Existing grid entries must point to the surviving group.
                for ids in grid.values():
                    if j in ids:
                        ids.discard(j)
                        ids.add(i)
        groups[i].append(curve)
        for c in cells:
            grid[c].add(i)
    for group in groups:
        if len(group) < 3:
            continue
        box = _union([_box(c) for c in group])
        w, h = box[2] - box[0], box[3] - box[1]
        if (
            w * h < 60
            or w * h > area * 0.08
            or w > page.width * 0.6
            or h > page.height * 0.2
        ):
            continue
        shapes = []
        for curve in group:
            shapes.append(
                [
                    (
                        step[0],
                        *[
                            (round(p[0] - box[0], 1), round(p[1] - box[1], 1))
                            for p in step[1:]
                        ],
                    )
                    for step in curve.get("path", [])
                ]
            )
        key = hashlib.sha256(json.dumps(shapes, sort_keys=True).encode()).hexdigest()
        centers = [
            ((c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2) for c in group
        ]
        mx, my = sum(x for x, y in centers) / len(centers), sum(
            y for x, y in centers
        ) / len(centers)
        a = math.degrees(
            math.atan2(
                sum((x - mx) * (y - my) for x, y in centers),
                sum((x - mx) ** 2 for x, y in centers) or 1,
            )
        )
        candidates.append(
            dict(key="vector-" + key, bbox=box, kind="vector", shapes=group, angle=a)
        )
    return candidates


def vector_image(candidate):
    """Rasterize isolated paths via a minimal in-memory PDF, excluding underlying body text."""
    x0, y0, x1, y1 = candidate["bbox"]
    width, height = x1 - x0 + 4, y1 - y0 + 4
    commands = ["0 g 0 G"]
    for curve in candidate["shapes"]:
        for step in curve.get("path", []):
            op = step[0]
            if op not in ("m", "l", "c", "h"):
                continue
            coords = [
                value
                for x, y in step[1:]
                for value in (x - x0 + 2, height - (y - y0 + 2))
            ]
            commands.append(" ".join(f"{c:.3f}" for c in coords) + " " + op)
        commands.append("f*" if curve.get("evenodd") else "f")
    content = "\n".join(commands).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources <<>> /Contents 4 0 R >>".encode(),
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    )
    document = PdfRaster(bytes(data))
    try:
        return document.render(0, 4)
    finally:
        document.close()


class PdfMedia:
    def __init__(self, file_stream, image_writer=None):
        self.stream = file_stream
        self.document = None
        self.candidates = {}
        self.repeated = set()
        self.assets = {}
        self.vector_watermarks = {}
        self.vector_samples = {}
        self.image_writer = image_writer

    def scan(self, pdf):
        occurrences = defaultdict(set)
        for page in pdf.pages:
            try:
                items = media_candidates(page)
                self.candidates[page.page_number] = items
                for item in items:
                    if item["kind"] == "vector":
                        if (
                            occurrences[item["key"]]
                            and item["key"] not in self.vector_samples
                        ):
                            self.vector_samples[item["key"]] = dict(item)
                        item.pop("shapes", None)
                    occurrences[item["key"]].add(page.page_number)
            finally:
                page.close()
        self.repeated = {key for key, pages in occurrences.items() if len(pages) >= 2}
        self.vector_samples = {
            key: item
            for key, item in self.vector_samples.items()
            if key in self.repeated
        }

    def save_image(self, image, page, role):
        return self.save_bytes(*image_bytes(image, role), page, role)

    def save_bytes(self, data, suffix, page, role):
        if self.image_writer is None:
            raise RuntimeError(
                "PDF 含图片；调用方必须提供 pdf_image_writer 指定输出位置"
            )
        return self.image_writer(data, suffix, page.page_number, role)

    def bitmap(self, page):
        if self.document is None:
            self.stream.seek(0)
            self.document = PdfRaster(self.stream)
        return self.document.render(page.page_number - 1, 3)

    def page(self, page, body_chars):
        items = self.candidates.get(page.page_number, [])
        logos, watermarks, images = [], [], []
        bitmap = None

        def crop(box):
            nonlocal bitmap
            if bitmap is None:
                bitmap = self.bitmap(page)
            sx, sy = bitmap.width / page.width, bitmap.height / page.height
            x0, y0, x1, y1 = box
            return bitmap.crop(
                (
                    max(0, int(x0 * sx)),
                    max(0, int(y0 * sy)),
                    min(bitmap.width, math.ceil(x1 * sx)),
                    min(bitmap.height, math.ceil(y1 * sy)),
                )
            )

        for item in items:
            x0, y0, x1, y1 = item["bbox"]
            key = item["key"]
            area = (x1 - x0) * (y1 - y0) / (page.width * page.height)
            repeated = key in self.repeated and area < 0.08
            if item["kind"] == "vector":
                repeated = repeated and (
                    8 < abs(item["angle"]) < 60
                    or (
                        (y0 < page.height * 0.14 or y1 > page.height * 0.9)
                        and x1 - x0 >= 20
                    )
                )
            if item["kind"] == "vector" and not repeated:
                continue
            if repeated:
                if key not in self.assets:
                    # OCR isolated slanted outlines only; ordinary logos keep their source appearance.
                    if item["kind"] == "vector" and 8 < abs(item["angle"]) < 60:
                        isolated = vector_image(self.vector_samples[key])
                        aligned = isolated.rotate(
                            item["angle"], expand=True, fillcolor="white"
                        )
                        words = ocr_words(aligned, psm=6)
                        text = " ".join(w["text"] for w in words)
                        confidence = sum(w["confidence"] for w in words) / max(
                            1, len(words)
                        )
                        if len(text) >= 4 and confidence >= 55:
                            self.vector_watermarks[key] = extension(
                                "pdf-watermark",
                                text=text,
                                angle=round(-item["angle"], 1),
                                source="outline-ocr",
                                confidence=round(confidence, 1),
                                image=self.save_image(isolated, page, "watermark"),
                            )
                    if key not in self.vector_watermarks:
                        original = (
                            embedded_image(page.images[item["image_index"]])
                            if item["kind"] == "image"
                            else None
                        )
                        self.assets[key] = (
                            self.save_bytes(*original, page, "logo")
                            if original
                            else self.save_image(crop(item["bbox"]), page, "logo")
                        )
                    else:
                        self.assets[key] = ""
                if key in self.vector_watermarks:
                    watermarks.append(self.vector_watermarks[key])
                else:
                    logos.append(
                        extension(
                            "pdf-logo",
                            image=self.assets[key],
                            alt="原文重复图案",
                            id=key[:24],
                        )
                    )
            elif item["kind"] == "image":
                original = embedded_image(page.images[item["image_index"]])
                data = (
                    self.save_bytes(*original, page, "image")
                    if original
                    else self.save_image(crop(item["bbox"]), page, "image")
                )
                images.append(
                    dict(
                        kind="image",
                        y=y0,
                        x0=x0,
                        full_page=area >= 0.9,
                        markdown=f"![原文插图]({data})",
                    )
                )
        # Complex vector illustrations have no recoverable Markdown topology. Keep a
        # visual reference rather than inventing Mermaid edges from label positions.
        repeat_boxes = [item["bbox"] for item in items if item["key"] in self.repeated]
        artwork = [
            curve
            for curve in page.curves
            if curve["bottom"] > page.height * 0.12
            and curve["top"] < page.height * 0.9
            and not any(
                box[0] <= curve["x0"]
                and box[1] <= curve["top"]
                and box[2] >= curve["x1"]
                and box[3] >= curve["bottom"]
                for box in repeat_boxes
            )
        ]
        if body_chars and len(artwork) >= 40:
            data = self.save_image(
                crop((0, page.height * 0.08, page.width, page.height * 0.94)),
                page,
                "figure",
            )
            images.append(
                dict(
                    kind="image",
                    y=page.height,
                    x0=0,
                    markdown=f"![原页图示（布局核对）]({data})",
                )
            )
        # A tiled watermark is one page annotation even when its vector paths have
        # tiny coordinate differences and therefore different image fingerprints.
        unique_marks = {}
        for block in watermarks:
            text = json.loads(block.splitlines()[1])["text"]
            unique_marks.setdefault(text, block)
        return list(unique_marks.values()), list(dict.fromkeys(logos)), images

    def close(self):
        if self.document is not None:
            self.document.close()
