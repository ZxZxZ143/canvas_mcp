"""Meaningful native-first, ordering, coverage and uncertainty regression checks."""

import io

from canvas_mcp.domain.file_content import ContentSelection
from canvas_mcp.infrastructure.files.content_parser import extract
from file_content_fixtures import pdf_bytes


class FakeEngine:
    def __init__(self, text="Scanned instructions: write a report."):
        self.calls, self.text = [], text

    def pdf_page(self, data, number):
        self.calls.append(number)
        return self.text

    def image(self, data, fmt):
        return self.text


def test_native_pages_are_never_ocrd_and_hybrid_order_is_preserved():
    native = "Native instructions with meaningful text. " * 4
    engine = FakeEngine()
    result = extract(
        io.BytesIO(pdf_bytes((native, "", native))), "pdf", ContentSelection(), engine=engine
    )
    assert engine.calls == [2]
    assert [unit["number"] for unit in result["units"]] == [1, 2, 3]
    assert result["units"][0]["text"] == native
    assert result["extraction_mode"] == "hybrid"
    assert result["ocr_pages"] == [2] and result["native_pages"] == [1, 3]
    assert result["page_count_processed"] == 3
    assert "formulas_symbols_and_handwriting_may_contain_errors" in result["limitations"]


def test_ocr_cap_retains_remaining_native_pages_and_reports_partial():
    engine = FakeEngine()
    result = extract(io.BytesIO(pdf_bytes(("",) * 5)), "pdf", ContentSelection(), engine=engine)
    assert engine.calls == [1, 2, 3]
    assert len(result["units"]) == 5 and result["truncated"]
    assert result["extraction_mode"] == "ocr" and result["native_pages"] == []
    assert "ocr_page_limit" in result["limitations"]


def test_empty_ocr_does_not_invent_text_or_erase_native_yield():
    engine = FakeEngine("")
    result = extract(
        io.BytesIO(pdf_bytes(("Tiny native text", ""))), "pdf", ContentSelection(), engine=engine
    )
    assert result["units"][0]["text"] == "Tiny native text"
    assert result["units"][1]["text"] == ""
    assert "ocr_no_reliable_text_on_page" in result["limitations"]
