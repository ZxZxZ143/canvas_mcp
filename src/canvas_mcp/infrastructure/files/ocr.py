"""Trusted engine bootstrap; all document decoding happens after sandbox sealing.

No OCR subprocess, model downloads, document paths or external callbacks.
"""

import ctypes
import io
import math
from typing import Any

from PIL import Image, PngImagePlugin, JpegImagePlugin, WebPImagePlugin
import pypdfium2 as pdfium  # type: ignore[import-untyped]

from canvas_mcp.domain.file_content import (
    OCR_MAX_DIMENSION,
    OCR_MAX_RENDER_PIXELS,
    OCR_MAX_SOURCE_PIXELS,
)

# Import only supported codecs before deny-open; do not Image.init() arbitrary plugins.
_CODECS = (PngImagePlugin, JpegImagePlugin, WebPImagePlugin)
Image.MAX_IMAGE_PIXELS = OCR_MAX_SOURCE_PIXELS


def image_signature(data: bytes, fmt: str) -> bool:
    if fmt == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if fmt in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    return fmt == "webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


class OcrEngine:
    """One English engine, initialized from packaged trusted data before seccomp."""

    def __init__(self) -> None:
        self.lib = ctypes.CDLL("libtesseract.so.5")
        signatures: dict[str, tuple[Any, list[Any]]] = {
            "TessBaseAPICreate": (ctypes.c_void_p, []),
            "TessBaseAPIInit3": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]),
            "TessBaseAPISetVariable": (
                ctypes.c_int,
                [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p],
            ),
            "TessBaseAPISetPageSegMode": (None, [ctypes.c_void_p, ctypes.c_int]),
            "TessBaseAPISetImage": (
                None,
                [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                ],
            ),
            "TessBaseAPIGetUTF8Text": (ctypes.c_void_p, [ctypes.c_void_p]),
            "TessBaseAPIClear": (None, [ctypes.c_void_p]),
            "TessBaseAPIDelete": (None, [ctypes.c_void_p]),
            "TessDeleteText": (None, [ctypes.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, arguments
        self.handle = self.lib.TessBaseAPICreate()
        if not self.handle or self.lib.TessBaseAPIInit3(
            self.handle, b"/usr/share/tesseract-ocr/5/tessdata", b"eng"
        ):
            raise ValueError()
        # Never create debug images or diagnostic files. Fixed config, not document input.
        if not self.lib.TessBaseAPISetVariable(self.handle, b"debug_file", b"/dev/null"):
            raise ValueError()
        self.lib.TessBaseAPISetPageSegMode(self.handle, 3)

    def recognize(self, image: Image.Image) -> str:
        width, height = image.size
        if not 0 < width * height <= OCR_MAX_RENDER_PIXELS:
            raise ValueError()
        pixels = image.convert("L").tobytes()
        buffer = ctypes.create_string_buffer(pixels)
        self.lib.TessBaseAPISetImage(self.handle, buffer, width, height, 1, width)
        pointer = self.lib.TessBaseAPIGetUTF8Text(self.handle)
        try:
            if not pointer:
                return ""
            text = ctypes.string_at(pointer).decode("utf-8", errors="strict")
            # Preserve symbols and line order; never correct formula semantics.
            return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
        finally:
            if pointer:
                self.lib.TessDeleteText(pointer)
            self.lib.TessBaseAPIClear(self.handle)

    def image(self, data: bytes, fmt: str) -> str:
        if not image_signature(data, fmt):
            raise ValueError()
        expected = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[fmt]
        with Image.open(io.BytesIO(data), formats=[expected]) as image:
            width, height = image.size
            if (
                not 0 < width <= OCR_MAX_DIMENSION
                or not 0 < height <= OCR_MAX_DIMENSION
                or width * height > OCR_MAX_SOURCE_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError()
            image.load()
            image.thumbnail((2_000, 2_000))
            return self.recognize(image)

    def pdf_page(self, data: bytes, number: int) -> str:
        # Bytes exclusively: never give PDFium a filename or network URL.
        with pdfium.PdfDocument(data) as document:
            with document[number - 1] as page:
                width, height = page.get_size()
                if not all(
                    math.isfinite(value) and 0 < value <= 14_400 for value in (width, height)
                ):
                    raise ValueError()
                scale = min(2.5, math.sqrt(OCR_MAX_RENDER_PIXELS / (width * height)) * 0.99)
                with page.render(
                    scale=scale,
                    grayscale=True,
                    may_draw_forms=False,
                    draw_annots=False,
                    limit_image_cache=True,
                ) as bitmap:
                    image = bitmap.to_pil()
                    try:
                        return self.recognize(image)
                    finally:
                        image.close()
