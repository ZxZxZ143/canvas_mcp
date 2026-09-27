"""Real C engine and rendering within the actual Linux deny-open sandbox."""

import asyncio
import sys
import time

import pytest

from canvas_mcp.domain.file_content import ContentSelection
from canvas_mcp.infrastructure.files.content_runner import parse_file
from canvas_mcp.infrastructure.files.ephemeral import EphemeralStore
from file_content_fixtures import image_bytes, scanned_pdf_bytes, pdf_bytes

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Real Linux OCR sandbox")


async def parse(tmp_path, body, fmt):
    store = EphemeralStore(_root=tmp_path)
    pending = store.begin()
    try:
        store.write(pending, body)
        store.validate(pending, fmt)
        return await parse_file(
            store.reader(pending),
            pending.count,
            pending.digest.hexdigest(),
            fmt,
            ContentSelection(),
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "fmt,encoded", [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP")]
)
def test_actual_image_text_in_sealed_worker(tmp_path, fmt, encoded):
    result = asyncio.run(parse(tmp_path, image_bytes(encoded), fmt))
    assert "report" in result["units"][0]["text"].lower()
    assert "automata" in result["units"][0]["text"].lower()
    assert result["extraction_mode"] == "ocr" and result["ocr_pages"] == [1]
    assert not list(tmp_path.iterdir())


def test_scanned_and_hybrid_pdf_use_real_ocr_in_page_order(tmp_path):
    result = asyncio.run(parse(tmp_path, scanned_pdf_bytes(hybrid=True), "pdf"))
    assert "Native requirement" in result["units"][0]["text"]
    assert "report" in result["units"][1]["text"].lower()
    assert result["extraction_mode"] == "hybrid" and result["ocr_pages"] == [2]


def test_native_pdf_with_engine_loaded_preserves_original_text(tmp_path):
    text = "Write an explanation about finite automata and regular languages. " * 3
    result = asyncio.run(parse(tmp_path, pdf_bytes((text,)), "pdf"))
    assert result["units"][0]["text"] == text
    assert result["extraction_mode"] == "native" and result["ocr_pages"] == []


def test_actual_ocr_resource_measurement(tmp_path):
    import resource
    from pathlib import Path

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    result = asyncio.run(parse(tmp_path, scanned_pdf_bytes(), "pdf"))
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    assert "report" in result["units"][0]["text"].lower()
    assert after.ru_maxrss < 256 * 1024
    assert time.monotonic() - started < 20
    model = Path("/usr/share/tesseract-ocr/5/tessdata/eng.traineddata")
    print(
        "OCR_RESOURCE_MEASUREMENT "
        f"wall_seconds={time.monotonic() - started:.3f} "
        f"cpu_seconds={after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime:.3f} "
        f"child_peak_rss_kib={after.ru_maxrss} model_bytes={model.stat().st_size}"
    )


@pytest.mark.parametrize("case", ["mismatch", "malformed", "dimensions", "animated"])
def test_invalid_images_fail_closed_and_cleanup(tmp_path, case):
    import io
    from PIL import Image

    if case == "dimensions":
        image = Image.new("L", (8193, 1))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        body = stream.getvalue()
    elif case == "animated":
        image = Image.new("RGB", (10, 10), "white")
        stream = io.BytesIO()
        image.save(
            stream, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "black")]
        )
        body = stream.getvalue()
    else:
        body = image_bytes("JPEG") if case == "mismatch" else b"\x89PNG\r\n\x1a\ninvalid"
    with pytest.raises(Exception):
        asyncio.run(parse(tmp_path, body, "png"))
    assert not list(tmp_path.iterdir())
