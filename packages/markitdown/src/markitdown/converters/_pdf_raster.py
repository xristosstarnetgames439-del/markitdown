"""Small, stable PDFium raster ABI. HNP uses the project's existing OHOS library.

Do not import the Linux pypdfium2 bindings on OHOS: their wheel embeds a Linux
loader/libstdc++ dependency, and the generated API can exceed our PDFium version.
"""

import ctypes as C
from functools import lru_cache
import importlib.util
import math
import os
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def _library():
    override = os.environ.get("HUIWEN_PDFIUM")
    packaged = Path(sys.executable).resolve().parent.parent / "lib/libpdfium.so"
    if override:
        path = Path(override)
    elif packaged.is_file():
        path = packaged
    else:
        # Host-only fallback uses pypdfium2's binary, not its generated API.
        spec = importlib.util.find_spec("pypdfium2_raw")
        if spec is None or spec.origin is None:
            raise RuntimeError("PDF 图片/OCR 需要 PDFium 光栅化组件")
        folder = Path(spec.origin).parent
        path = next(
            (
                folder / name
                for name in ("libpdfium.dylib", "libpdfium.so", "pdfium.dll")
                if (folder / name).is_file()
            ),
            None,
        )
        if path is None:
            raise RuntimeError("未找到本机 PDFium 动态库")
    lib = C.CDLL(str(path))
    signatures = {
        "FPDF_InitLibrary": (None, []),
        "FPDF_LoadMemDocument64": (C.c_void_p, [C.c_void_p, C.c_size_t, C.c_char_p]),
        "FPDF_CloseDocument": (None, [C.c_void_p]),
        "FPDF_LoadPage": (C.c_void_p, [C.c_void_p, C.c_int]),
        "FPDF_ClosePage": (None, [C.c_void_p]),
        "FPDF_GetPageWidth": (C.c_double, [C.c_void_p]),
        "FPDF_GetPageHeight": (C.c_double, [C.c_void_p]),
        "FPDFBitmap_Create": (C.c_void_p, [C.c_int, C.c_int, C.c_int]),
        "FPDFBitmap_FillRect": (
            None,
            [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_ulong],
        ),
        "FPDFBitmap_GetBuffer": (C.c_void_p, [C.c_void_p]),
        "FPDFBitmap_GetStride": (C.c_int, [C.c_void_p]),
        "FPDF_RenderPageBitmap": (
            None,
            [
                C.c_void_p,
                C.c_void_p,
                C.c_int,
                C.c_int,
                C.c_int,
                C.c_int,
                C.c_int,
                C.c_int,
            ],
        ),
        "FPDFBitmap_Destroy": (None, [C.c_void_p]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = args
    lib.FPDF_InitLibrary()
    return lib


class PdfRaster:
    def __init__(self, source):
        self.lib = _library()
        if hasattr(source, "read"):
            source.seek(0)
            data = source.read()
        else:
            data = source
        self.buffer = C.create_string_buffer(data)
        self.document = self.lib.FPDF_LoadMemDocument64(self.buffer, len(data), None)
        if not self.document:
            raise RuntimeError("PDFium 无法打开 PDF 图像内容")

    def render(self, number, scale=2):
        from PIL import Image

        page = self.lib.FPDF_LoadPage(self.document, number)
        if not page:
            raise RuntimeError(f"PDFium 无法读取第 {number+1} 页")
        bitmap = None
        try:
            w, h = self.lib.FPDF_GetPageWidth(page), self.lib.FPDF_GetPageHeight(page)
            if not all(math.isfinite(n) and n > 0 for n in (w, h)):
                raise RuntimeError("PDF 页面尺寸无效")
            scale = min(scale, 3200 / max(w, h))
            w, h = math.ceil(w * scale), math.ceil(h * scale)
            bitmap = self.lib.FPDFBitmap_Create(w, h, 0)
            if not bitmap:
                raise MemoryError("PDFium bitmap allocation failed")
            self.lib.FPDFBitmap_FillRect(bitmap, 0, 0, w, h, 0xFFFFFFFF)
            self.lib.FPDF_RenderPageBitmap(bitmap, page, 0, 0, w, h, 0, 1)
            stride = self.lib.FPDFBitmap_GetStride(bitmap)
            data = C.string_at(self.lib.FPDFBitmap_GetBuffer(bitmap), stride * h)
            return Image.frombytes("RGB", (w, h), data, "raw", "BGRX", stride)
        finally:
            if bitmap:
                self.lib.FPDFBitmap_Destroy(bitmap)
            self.lib.FPDF_ClosePage(page)

    def close(self):
        if self.document:
            self.lib.FPDF_CloseDocument(self.document)
            self.document = None
