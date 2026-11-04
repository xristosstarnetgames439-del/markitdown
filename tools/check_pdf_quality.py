"""Batch host validation against actual source PDFs (never connects to a device).

Example: python tools/check_pdf_quality.py INPUT_DIR OUTPUT_DIR --bundle converter.pyz
Requires host pdfplumber/pypdf plus the OCR binary/models used by the bundle.
The character coverage metric is a loss detector, not an OCR accuracy score.
"""

import argparse
from collections import Counter
import hashlib
import html
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote

from PIL import Image

import pdfplumber
from pypdf import PdfReader

logging.getLogger("pdfminer").setLevel(logging.ERROR)


def visible_text(markdown):
    def payload(match):
        try:
            data = json.loads(match[2])
            return data.get("text", "") if match[1] == "pdf-watermark" else ""
        except ValueError:
            return match[0]

    text = re.sub(
        r"~{3,}(pdf-watermark|pdf-logo)\n(.*?)\n~{3,}", payload, markdown, flags=re.S
    )
    text = re.sub(r"!\[[^\]]*\]\([^\n]*\)", "", text)
    text = re.sub(r"<!-- PDF page \d+ -->|<br\s*/?>|</?small>", "", text)
    code = []

    def keep_code(match):
        code.append(match[2])
        return ""

    text = re.sub(r"(?ms)^(`{3,})[^\n]*\n(.*?)^\1[ \t]*$", keep_code, text)
    return html.unescape(
        re.sub(r"\\([\\`*_{}\[\]<>#+.!|~$^=])", r"\1", text)
    ) + "".join(code)


def run(source, output, bundle):
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    start = time.monotonic()
    target = output / (source.stem + ".md")
    log = output / (source.stem + ".log")
    with log.open("w") as stream:
        try:
            result = subprocess.run(
                [sys.executable, str(bundle), str(source), str(target)],
                stdout=stream,
                stderr=stream,
                timeout=900,
            )
            status = result.returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
    row = dict(
        file=source.name,
        status=status,
        seconds=round(time.monotonic() - start, 2),
        source_sha256=original,
        source_unchanged=original == hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    reference = []
    char_count = 0
    empty = 0
    with pdfplumber.open(source) as pdf:
        row["pages"] = len(pdf.pages)
        for page in pdf.pages:
            text = "".join(c["text"] for c in page.chars)
            reference.append(text)
            char_count += len(text.strip())
            empty += not text.strip()
            page.close()
    row["textless_pages"] = empty
    row["pypdf_text_chars"] = sum(
        len(p.extract_text() or "") for p in PdfReader(source).pages
    )
    text = target.read_text() if target.exists() and status == 0 else ""
    row["output_bytes"] = len(text.encode())
    row["page_markers"] = len(re.findall(r"<!-- PDF page \d+ -->", text))
    for key, pattern in [
        ("watermark_blocks", r"~{3,}pdf-watermark\n"),
        ("logo_blocks", r"~{3,}pdf-logo\n"),
        ("image_blocks", r"!\["),
        ("code_blocks", r"(?m)^`{3,}[a-z]+"),
        ("table_separators", r"(?m)^\|(?: *--- *\|)+$"),
    ]:
        row[key] = len(re.findall(pattern, text))
    source_chars = Counter(
        c for c in "".join(reference) if c.isprintable() and not c.isspace()
    )
    output_chars = Counter(
        c for c in visible_text(text) if c.isprintable() and not c.isspace()
    )
    row["text_layer_character_coverage"] = (
        round(
            sum((source_chars & output_chars).values()) / sum(source_chars.values()), 4
        )
        if source_chars
        else None
    )
    row["missing_character_samples"] = "".join(
        (source_chars - output_chars).elements()
    )[:120]
    confidence = [
        float(n) for n in re.findall(r"mean_confidence=([0-9.]+)", log.read_text())
    ]
    row["ocr_pages"] = len(confidence)
    row["ocr_mean_confidence"] = (
        round(sum(confidence) / len(confidence), 1) if confidence else None
    )
    row["blank_scans"] = len(re.findall(r"BLANK_SCAN page=", log.read_text()))
    references = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    for payload in re.findall(
        r"~{3,}(?:pdf-watermark|pdf-logo)\n(.*?)\n~{3,}", text, re.S
    ):
        data = json.loads(payload)
        if data.get("image"):
            references.append(data["image"])
    row["embedded_data_images"] = sum(ref.startswith("data:") for ref in references)
    assets = set()
    invalid = []
    for reference in references:
        path = (output / unquote(reference)).resolve()
        try:
            if path.parent != output.resolve():
                raise ValueError("image not beside markdown")
            with Image.open(path) as image:
                image.verify()
            assets.add(path)
        except (OSError, ValueError) as error:
            invalid.append({"path": reference[:200], "error": str(error)})
    row["image_files"] = len(assets)
    row["image_bytes"] = sum(path.stat().st_size for path in assets)
    row["invalid_images"] = invalid
    row["passed"] = (
        status == 0
        and not row["embedded_data_images"]
        and not row["invalid_images"]
        and row["source_unchanged"]
        and row["pages"] == row["page_markers"]
        and 0 < row["output_bytes"] <= 16 * 1024 * 1024
    )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    files = sorted(args.input.glob("*.pdf"))
    if not files:
        raise SystemExit("No PDFs found")
    for source in files:
        row = run(source, args.output, args.bundle.resolve())
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (args.output / "results.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
        )
    lines = [
        "# PDF 本地转换检查",
        "",
        "字符覆盖率仅检查文字层字符是否丢失，不证明阅读顺序、表格结构或 OCR 正确率。OCR 置信度是引擎估计，不是人工准确率。",
        "",
        "| 文件 | 页数 | 成功 | 字符覆盖率 | OCR 页数 | 输出 MiB |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for r in rows:
        coverage = (
            "无文字层"
            if r["text_layer_character_coverage"] is None
            else f"{r['text_layer_character_coverage']:.2%}"
        )
        lines.append(
            f"| {r['file']} | {r['pages']} | {r['passed']} | {coverage} | {r['ocr_pages']} | {r['output_bytes']/1024/1024:.2f} |"
        )
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n")
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
