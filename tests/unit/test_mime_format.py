import io
import json
import struct
import zipfile

import pytest

from canvas_mcp.domain.mime import DiagnosticStatus as Status, ExpectedFormat as Format
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES, identify


def archive(*names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as handle:
        for name in names:
            handle.writestr(name, b"inert fixture")
    return output.getvalue()


@pytest.mark.parametrize(
    "expected,data,detected",
    [
        (Format.PDF, b"%PDF-1.7\nfixture", "pdf"),
        *(
            (kind, b"inert UTF-8 \xd0\x94", "utf8_text")
            for kind in (Format.TXT, Format.MD, Format.CSV, Format.PY)
        ),
        (Format.JSON, b'{"a": [1, true, null]}', "json"),
        (Format.IPYNB, b'{"nbformat":4,"metadata":{},"cells":[]}', "ipynb"),
        (Format.DOCX, archive("[Content_Types].xml", "word/document.xml"), "docx"),
        (Format.PPTX, archive("[Content_Types].xml", "ppt/presentation.xml"), "pptx"),
        (Format.XLSX, archive("[Content_Types].xml", "xl/workbook.xml"), "xlsx"),
        (Format.ZIP, archive("a.txt"), "opaque_zip"),
    ],
)
def test_supported_evidence(expected, data, detected, monkeypatch):
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("member read"))
    result = identify(data, expected)
    assert result.result is Status.EXPECTED and result.learnable
    assert result.detected_format == detected


@pytest.mark.parametrize(
    "data",
    [
        b"<html>private",
        b"\xef\xbb\xbf <svg>",
        b"MZpayload",
        b"\x7fELF",
        b'<?xml version="1.0"?><svg>',
        b"<!-- comment --><html>",
        b"\xd0\xcf\x11\xe0binary",
        b"\xca\xfe\xba\xbebinary",
    ],
)
def test_active_prefix_is_never_learnable(data):
    for expected in Format:
        evidence = identify(data, expected)
        assert evidence.result is Status.UNSAFE and not evidence.learnable


@pytest.mark.parametrize(
    "suffix",
    [
        "exe",
        "dll",
        "com",
        "bat",
        "cmd",
        "ps1",
        "vbs",
        "js",
        "msi",
        "scr",
        "html",
        "svg",
        "docm",
        "xlsm",
        "pptm",
        "doc",
        "xls",
        "ppt",
        "bin",
    ],
)
def test_dangerous_format_not_supported_or_hidden_in_archive(suffix):
    with pytest.raises(ValueError):
        Format(suffix)
    evidence = identify(
        archive("[Content_Types].xml", "word/document.xml", "word/payload." + suffix), Format.DOCX
    )
    assert evidence.result is Status.UNSAFE and not evidence.learnable


@pytest.mark.parametrize(
    "names",
    [
        ("hello.txt",),
        ("[Content_Types].xml",),
        ("word/document.xml",),
        ("[Content_Types].xml", "word/document.xml", "xl/workbook.xml"),
    ],
)
def test_generic_or_ambiguous_zip_is_not_docx(names):
    evidence = identify(archive(*names), Format.DOCX)
    assert evidence.detected_format == "opaque_zip" and not evidence.learnable


@pytest.mark.parametrize(
    "data",
    [b"{", b"NaN", b"Infinity", b"[" * 65 + b"0" + b"]" * 65, json.dumps([0] * 100001).encode()],
    ids=["syntax", "nan", "infinity", "depth", "tokens"],
)
def test_json_invalid_or_work_bound(data):
    result = identify(data, Format.JSON)
    assert result.result is Status.INVALID and not result.learnable


@pytest.mark.parametrize("data", [b"\xff", b"hi\x00there"])
def test_text_encoding_failclosed(data):
    assert not identify(data, Format.TXT).learnable


def test_notebook_inert_and_structure_required():
    body = json.dumps(
        {
            "nbformat": 4,
            "metadata": {},
            "cells": [{"source": "raise AssertionError('never execute')"}],
        }
    ).encode()
    assert identify(body, Format.IPYNB).learnable
    assert not identify(b"{}", Format.IPYNB).learnable


@pytest.mark.parametrize("body", [b"<html>", b"<?xml?><svg>", b"MZpayload"])
@pytest.mark.parametrize("padding", [b"", b"\f", b"\v"])
def test_long_leading_whitespace_does_not_hide_active_prefix(body, padding):
    evidence = identify(b" " * 1024 + padding + body, Format.TXT)
    assert evidence.result is Status.UNSAFE and not evidence.learnable


@pytest.mark.parametrize(
    "names",
    [
        ("../escape",),
        ("a\\b",),
        ("/absolute",),
        ("a:stream",),
        ("a.txt", "A.TXT"),
        ("a" * 513,),
        tuple(str(i) for i in range(257)),
    ],
)
def test_zip_name_and_entry_bounds(names):
    body = archive(*names)
    if names == ("a\\b",):
        body = body.replace(b"a/b", b"a\\b")  # Windows ZipFile normalizes this on write.
    assert identify(body, Format.ZIP).result is Status.INVALID


@pytest.mark.parametrize(
    "name",
    [
        "xl/macrosheets/sheet1.xml",
        "xl/intlmacrosheets/sheet1.xml",
        "xl/vbaProject.xml",
        "word/vbaData.xml",
        "xl/activeX/control.xml",
        "xl/embeddings/object.xml",
    ],
)
def test_xml_macro_and_active_office_parts_are_never_learnable(name):
    evidence = identify(archive("[Content_Types].xml", "xl/workbook.xml", name), Format.XLSX)
    assert evidence.result is Status.UNSAFE and not evidence.learnable


def test_zip_malformed_encryption_zip64_symlink_and_multidisk():
    body = archive("a.txt")
    central = body.index(b"PK\x01\x02")
    end = body.index(b"PK\x05\x06")
    for offset, encoding, value in [
        (central + 8, "H", 1),
        (central + 8, "H", 0x2000),
        (central + 20, "L", 0xFFFFFFFF),
        (central + 38, "L", 0xA000 << 16),
        (end + 4, "H", 1),
        (central + 42, "L", 99999),
    ]:
        malformed = bytearray(body)
        struct.pack_into("<" + encoding, malformed, offset, value)
        assert identify(bytes(malformed), Format.ZIP).result is Status.INVALID
    for malformed in (body[:-1], b"PK\x03\x04", body + b"junk"):
        assert identify(malformed, Format.ZIP).result is Status.INVALID


def test_total_work_bound_and_expected_type():
    with pytest.raises(ValueError):
        identify(b"a" * (MAX_PROBE_BYTES + 1), Format.TXT)
    with pytest.raises(ValueError):
        identify(b"hi", "txt")
