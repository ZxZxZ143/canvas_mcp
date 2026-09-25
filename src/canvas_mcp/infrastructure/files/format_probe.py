"""Bounded credential-free identification. No rendering, extraction or execution."""

import json
import re
import struct
from collections.abc import Callable

from canvas_mcp.domain.mime import DiagnosticStatus as Status, ExpectedFormat, FormatEvidence

MAX_PROBE_BYTES = 1_048_576
MAX_ZIP_ENTRIES = 256
MAX_DIRECTORY_BYTES = 262_144
MAX_MEMBER_NAME = 512
MAX_JSON_DEPTH = 64
MAX_JSON_TOKENS = 100_000
ACTIVE_SUFFIXES = (
    ".exe",
    ".dll",
    ".com",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".msi",
    ".scr",
    ".html",
    ".htm",
    ".svg",
    ".docm",
    ".xlsm",
    ".pptm",
    ".doc",
    ".xls",
    ".ppt",
    ".bin",
)


def _zip_names(data: bytes) -> tuple[str, ...]:
    return _zip_names_reader(len(data), lambda start, size: data[start : start + size])


def _zip_names_reader(size: int, read_at: Callable[[int, int], bytes]) -> tuple[str, ...]:
    """Read only bounded ZIP metadata; never decompress or extract a member."""
    if size < 22:
        raise ValueError()
    tail_start = max(0, size - 65557)
    tail = read_at(tail_start, size - tail_start)
    if len(tail) != size - tail_start:
        raise ValueError()
    marker = tail.rfind(b"PK\x05\x06")
    end = tail_start + marker
    if marker < 0 or marker + 22 > len(tail):
        raise ValueError()
    _, disk, directory_disk, count_disk, count, size, start, comment = struct.unpack_from(
        "<4s4H2LH", tail, marker
    )
    if (
        disk
        or directory_disk
        or count_disk != count
        or count > MAX_ZIP_ENTRIES
        or size > MAX_DIRECTORY_BYTES
        or start + size != end
        or end + 22 + comment != tail_start + len(tail)
        or b"PK\x06\x06" in tail[max(0, marker - 76) : marker]
    ):
        raise ValueError()
    directory = read_at(start, size)
    if len(directory) != size:
        raise ValueError()
    names: list[str] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    pos = 0
    for _ in range(count):
        if pos + 46 > size or directory[pos : pos + 4] != b"PK\x01\x02":
            raise ValueError()
        fields = struct.unpack_from("<4s6H3L5H2L", directory, pos)
        flags, method = fields[3:5]
        compressed, expanded = fields[8:10]
        name_size, extra_size, comment_size, member_disk = fields[10:14]
        external, local = fields[15:17]
        if (
            flags & ~(0x800 | 8 | 6)
            or method not in (0, 8)
            or member_disk
            or not 1 <= name_size <= MAX_MEMBER_NAME
            or compressed == 0xFFFFFFFF
            or expanded == 0xFFFFFFFF
            or local == 0xFFFFFFFF
            or (external >> 16) & 0xF000 == 0xA000
        ):
            raise ValueError()
        finish = pos + 46 + name_size + extra_size + comment_size
        if finish > size:
            raise ValueError()
        raw = directory[pos + 46 : pos + 46 + name_size]
        name = raw.decode("ascii").lower()
        if (
            name in seen
            or name.startswith("/")
            or "\\" in name
            or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p in (".", "..", "") for p in name.rstrip("/").split("/"))
        ):
            raise ValueError()
        if local + 30 > start:
            raise ValueError()
        local_header = read_at(local, 30)
        if len(local_header) != 30 or local_header[:4] != b"PK\x03\x04":
            raise ValueError()
        local_fields = struct.unpack_from("<4s5H3L2H", local_header)
        local_flags, local_method = local_fields[2:4]
        local_name_size, local_extra_size = local_fields[-2:]
        payload = local + 30 + local_name_size + local_extra_size
        local_name_extra = read_at(local + 30, local_name_size + local_extra_size)
        if (
            local_flags != flags
            or local_method != method
            or local_name_size != name_size
            or len(local_name_extra) != local_name_size + local_extra_size
            or local_name_extra[:local_name_size] != raw
            or payload + compressed > start
        ):
            raise ValueError()
        # ZIP64 extra fields are not understood even if no sentinel was used.
        for extra in (
            directory[pos + 46 + name_size : pos + 46 + name_size + extra_size],
            local_name_extra[local_name_size:],
        ):
            offset = 0
            while offset < len(extra):
                if offset + 4 > len(extra):
                    raise ValueError()
                tag, length = struct.unpack_from("<HH", extra, offset)
                if tag == 1 or offset + 4 + length > len(extra):
                    raise ValueError()
                offset += 4 + length
        spans.append((local, payload + compressed))
        seen.add(name)
        names.append(name)
        pos = finish
    if pos != size:
        raise ValueError()
    ordered = sorted(spans)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError()
    return tuple(names)


def _zip_evidence(names: tuple[str, ...], expected: ExpectedFormat) -> FormatEvidence:
    if any(
        name.endswith(ACTIVE_SUFFIXES)
        or "/activex/" in name
        or "/embeddings/" in name
        or "/macrosheets/" in name
        or "/intlmacrosheets/" in name
        or "vbaproject" in name
        or "vbadata" in name
        for name in names
    ):
        return FormatEvidence(expected, "unsafe", "zip_member_names", Status.UNSAFE)
    families = [
        fmt
        for fmt, root in (
            ("docx", "word/document.xml"),
            ("pptx", "ppt/presentation.xml"),
            ("xlsx", "xl/workbook.xml"),
        )
        if root in names
    ]
    detected = (
        families[0] if len(families) == 1 and "[content_types].xml" in names else "opaque_zip"
    )
    matches = (expected is ExpectedFormat.ZIP and detected == "opaque_zip") or (
        detected == expected.value
    )
    return FormatEvidence(
        expected, detected, "zip_directory_structure", Status.EXPECTED if matches else Status.OTHER
    )


def identify_archive_reader(
    size: int, read_at: Callable[[int, int], bytes], expected: ExpectedFormat
) -> FormatEvidence:
    if type(expected) is not ExpectedFormat or expected not in (
        ExpectedFormat.ZIP,
        ExpectedFormat.DOCX,
        ExpectedFormat.PPTX,
        ExpectedFormat.XLSX,
    ):
        raise ValueError()
    try:
        names = _zip_names_reader(size, read_at)
    except (ValueError, UnicodeError, struct.error):
        return FormatEvidence(expected, "unrecognized", "zip_directory", Status.INVALID)
    return _zip_evidence(names, expected)


def _json(text: str) -> object:
    quoted = escaped = False
    depth = tokens = 0
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{}[],:":
            tokens += 1
            depth += 1 if char in "{[" else -1 if char in "}]" else 0
            if depth > MAX_JSON_DEPTH or tokens > MAX_JSON_TOKENS:
                raise ValueError()

    def constant(value: str) -> object:
        raise ValueError()

    return json.loads(text, parse_constant=constant)


def identify(data: bytes, expected: ExpectedFormat) -> FormatEvidence:
    if (
        type(expected) is not ExpectedFormat
        or type(data) is not bytes
        or len(data) > MAX_PROBE_BYTES
    ):
        raise ValueError()

    def result(detected: str, method: str, status: Status) -> FormatEvidence:
        return FormatEvidence(expected, detected, method, status)

    # The whole input is capped at 1 MiB. Do not let long leading whitespace
    # hide active markup beyond the streaming policy's 512-byte prefix window.
    prefix = data.lstrip(b"\xef\xbb\xbf \t\r\n\f\v")[:512].lower()
    if prefix.startswith((b"mz", b"\x7felf", b"<!doctype html", b"<html", b"<svg")):
        return result("unsafe", "unsafe_prefix", Status.UNSAFE)
    # Conservative extra diagnostic guard: XML-declared SVG, comment-prefixed
    # HTML, other leading markup, OLE and additional executable signatures.
    # These are rejected without parsing, rendering or opening the content.
    if re.match(rb"<(?:[a-z!?])", prefix) or data.startswith(
        (
            b"\xd0\xcf\x11\xe0",
            b"\xca\xfe\xba\xbe",
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xce",
        )
    ):
        return result("unsafe", "unsafe_prefix", Status.UNSAFE)
    if data.startswith(b"%PDF-"):
        return result(
            "pdf",
            "pdf_signature",
            Status.EXPECTED if expected is ExpectedFormat.PDF else Status.OTHER,
        )
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        try:
            names = _zip_names(data)
        except (ValueError, UnicodeError, struct.error):
            return result("unrecognized", "zip_directory", Status.INVALID)
        return _zip_evidence(names, expected)
    try:
        text = data.decode("utf-8")
        if "\0" in text:
            raise ValueError()
    except (UnicodeError, ValueError):
        return result("unrecognized", "utf8_no_nul", Status.UNRECOGNIZED)
    if expected in (ExpectedFormat.TXT, ExpectedFormat.MD, ExpectedFormat.CSV, ExpectedFormat.PY):
        return result("utf8_text", "utf8_no_nul", Status.EXPECTED)
    if expected in (ExpectedFormat.JSON, ExpectedFormat.IPYNB):
        try:
            value = _json(text)
        except (ValueError, RecursionError):
            return result("utf8_text", "json_syntax", Status.INVALID)
        notebook = (
            type(value) is dict
            and value.get("nbformat") == 4
            and type(value.get("cells")) is list
            and len(value["cells"]) <= 2048
            and type(value.get("metadata")) is dict
        )
        if expected is ExpectedFormat.IPYNB:
            return result(
                "ipynb" if notebook else "json",
                "notebook_structure",
                Status.EXPECTED if notebook else Status.OTHER,
            )
        return result("json", "json_syntax", Status.EXPECTED)
    return result("utf8_text", "utf8_no_nul", Status.OTHER)
