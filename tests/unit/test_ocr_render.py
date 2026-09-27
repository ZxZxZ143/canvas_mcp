"""Real in-memory decoders/rendering; OCR engine itself is covered on Linux."""

import pytest
from PIL import Image

from canvas_mcp.infrastructure.files.ocr import OcrEngine
from file_content_fixtures import image_bytes, scanned_pdf_bytes


def recognizer(monkeypatch):
    engine = object.__new__(OcrEngine)

    def recognize(image):
        assert 0 < image.width * image.height <= 4_000_000
        assert image.convert("L").getextrema()[0] < 128
        return "Verified nonblank raster"

    monkeypatch.setattr(engine, "recognize", recognize)
    return engine


@pytest.mark.parametrize("fmt,encoded", [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")])
def test_supported_memory_decoder_does_not_discover_plugins(monkeypatch, fmt, encoded):
    body = image_bytes(encoded)

    def denied():
        raise PermissionError("Plugin discovery must not occur after sealing")

    monkeypatch.setattr(Image, "preinit", denied)
    assert recognizer(monkeypatch).image(body, fmt) == "Verified nonblank raster"


def test_actual_pdfium_page_and_bitmap_lifecycle(monkeypatch):
    assert recognizer(monkeypatch).pdf_page(scanned_pdf_bytes(), 1) == "Verified nonblank raster"
