"""Credential-free inert text readers. No URL resolution, rendering or execution."""

import csv
import io
import logging
import posixpath
import re
import zipfile
from typing import Any, BinaryIO
from xml.etree.ElementTree import Element, TreeBuilder

from defusedxml.ElementTree import DefusedXMLParser  # type: ignore[import-untyped]
from pypdf import PdfReader, apply_configuration

from canvas_mcp.domain.file_content import (
    ContentSelection,
    REMOTE_FILE_MAX_PAGES,
    REMOTE_FILE_MAX_TABLE_CELLS,
    REMOTE_FILE_MAX_TEXT_CHARS,
    REMOTE_INSPECTION_MAX_BYTES,
    SUPPORTED_FORMATS,
    bounded_json,
)
from canvas_mcp.infrastructure.files.format_probe import _json, identify, identify_archive_reader
from canvas_mcp.domain.mime import DiagnosticStatus, ExpectedFormat

MAX_XML_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_XML_NODES = 50_000
MAX_XML_DEPTH = 64
MAX_UNITS = 256
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"


class BoundedTree(TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = self.depth = 0

    def start(self, tag: str, attrs: dict[str, str]) -> Element:
        self.nodes += 1
        self.depth += 1
        if self.nodes > MAX_XML_NODES or self.depth > MAX_XML_DEPTH:
            raise ValueError()
        return super().start(tag, attrs)

    def end(self, tag: str) -> Element:
        self.depth -= 1
        return super().end(tag)


class Output:
    def __init__(self) -> None:
        self.units: list[dict[str, Any]] = []
        self.characters = 0
        self.truncated = False

    def add(self, kind: str, number: int, text: str) -> bool:
        remaining = REMOTE_FILE_MAX_TEXT_CHARS - self.characters
        if len(self.units) >= MAX_UNITS or remaining <= 0:
            self.truncated = True
            return False
        # Bound Unicode scalars before JSON serialization, never cut encoded bytes.
        text = text.replace("\x00", "")
        bounded = True
        if len(text) > remaining:
            text = text[:remaining]
            self.truncated = True
            bounded = False
        self.units.append({"kind": kind, "number": number, "text": text})
        self.characters += len(text)
        return bounded


def xml(data: bytes) -> Element:
    if len(data) > MAX_XML_BYTES:
        raise ValueError()
    parser = DefusedXMLParser(
        target=BoundedTree(), forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    for offset in range(0, len(data), 65_536):
        parser.feed(data[offset : offset + 65_536])
    return parser.close()


class Package:
    def __init__(self, stream: BinaryIO, fmt: str) -> None:
        size = stream.seek(0, 2)

        def read_at(start: int, length: int) -> bytes:
            stream.seek(start)
            return stream.read(length)

        evidence = identify_archive_reader(size, read_at, ExpectedFormat(fmt))
        if evidence.result is not DiagnosticStatus.EXPECTED:
            raise ValueError()
        stream.seek(0)
        self.archive = zipfile.ZipFile(stream)
        total = 0
        for item in self.archive.infolist():
            total += item.file_size
            if (
                item.file_size > MAX_XML_BYTES
                or total > MAX_EXPANDED_BYTES
                or item.file_size > max(1, item.compress_size) * 100
                or item.flag_bits & 1
            ):
                self.archive.close()
                raise ValueError()
        root = self.read_xml("[Content_Types].xml")
        main = "/word/document.xml" if fmt == "docx" else "/ppt/presentation.xml"
        expected = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            if fmt == "docx"
            else "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
        )
        declarations = [part.get("ContentType", "") for part in root]
        overrides = [part for part in root if part.tag == CT + "Override"]
        content_types = {part.get("PartName"): part.get("ContentType") for part in overrides}
        if (
            root.tag != CT + "Types"
            or len(content_types) != len(overrides)
            or content_types.get(main) != expected
            or any(
                "macroenabled" in (value or "").lower()
                or "activex" in (value or "").lower()
                or "oleobject" in (value or "").lower()
                for value in declarations
            )
        ):
            self.archive.close()
            raise ValueError()

    def read_xml(self, name: str) -> Element:
        # Names come solely from fixed reader routes or validated package relations.
        parts: list[bytes] = []
        count = 0
        with self.archive.open(name) as member:
            while chunk := member.read(65_536):
                count += len(chunk)
                if count > MAX_XML_BYTES:
                    raise ValueError()
                parts.append(chunk)
        return xml(b"".join(parts))

    def relationships(self, name: str, base: str, family: str) -> dict[str, str]:
        root = self.read_xml(name)
        if root.tag != REL + "Relationships":
            raise ValueError()
        targets: dict[str, str] = {}
        for item in root:
            if item.tag != REL + "Relationship" or item.get("TargetMode") == "External":
                continue
            if item.get("Type") != R[1:-1] + "/" + family:
                continue
            identifier, target = item.get("Id"), item.get("Target", "")
            if (
                not identifier
                or identifier in targets
                or len(target) > 256
                or re.search(r"[\\:%\x00-\x20]", target)
                or target.startswith("/")
            ):
                raise ValueError()
            normalized = posixpath.normpath(posixpath.join(base, target))
            pattern = (
                r"ppt/slides/slide[1-9][0-9]*\.xml"
                if family == "slide"
                else r"ppt/notesSlides/notesSlide[1-9][0-9]*\.xml"
            )
            if re.fullmatch(pattern, normalized) is None:
                raise ValueError()
            targets[identifier] = normalized
        return targets


def paragraph(element: Element, namespace: str) -> str:
    pieces: list[str] = []
    for item in element.iter():
        if item.tag == namespace + "t":
            pieces.append(item.text or "")
        elif item.tag == namespace + "tab":
            pieces.append("\t")
        elif item.tag in (namespace + "br", namespace + "cr"):
            pieces.append("\n")
    return "".join(pieces)


def _docx(package: Package, output: Output) -> tuple[str, ...]:
    root = package.read_xml("word/document.xml")
    if root.tag != W + "document":
        raise ValueError()
    body = root.find(W + "body")
    if body is None:
        raise ValueError()
    cells = 0
    number = 0
    for item in body:
        if item.tag == W + "p":
            number += 1
            style = item.find("./" + W + "pPr/" + W + "pStyle")
            heading = style is not None and style.get(W + "val", "").lower().startswith("heading")
            if not output.add("heading" if heading else "paragraph", number, paragraph(item, W)):
                break
        elif item.tag == W + "tbl":
            for row in item.findall(W + "tr"):
                number += 1
                values: list[str] = []
                for cell in row.findall(W + "tc"):
                    cells += 1
                    if cells > REMOTE_FILE_MAX_TABLE_CELLS:
                        output.truncated = True
                        break
                    values.append("\n".join(paragraph(p, W) for p in cell.findall(W + "p")))
                if not output.add("table_row", number, "\t".join(values)):
                    break
            if output.truncated:
                break
    return ("headers_footers_comments_revisions_and_graphics_not_extracted",)


def _range(total: int, selection: ContentSelection, output: Output) -> range:
    if not 1 <= total <= 2_000 or selection.start_page > total:
        raise ValueError()
    end = min(total, selection.end_page or selection.start_page + REMOTE_FILE_MAX_PAGES - 1)
    output.truncated = selection.start_page > 1 or end < total
    return range(selection.start_page, end + 1)


def _pptx(package: Package, output: Output, selection: ContentSelection) -> int:
    root = package.read_xml("ppt/presentation.xml")
    if root.tag != P + "presentation":
        raise ValueError()
    relations = package.relationships("ppt/_rels/presentation.xml.rels", "ppt", "slide")
    slides = root.findall("./" + P + "sldIdLst/" + P + "sldId")
    identifiers = [item.get(R + "id", "") for item in slides]
    if len(set(identifiers)) != len(identifiers) or any(r not in relations for r in identifiers):
        raise ValueError()
    cells = 0
    for number in _range(len(slides), selection, output):
        name = relations[identifiers[number - 1]]
        slide = package.read_xml(name)
        if slide.tag != P + "sld":
            raise ValueError()
        cells += sum(1 for _ in slide.iter(A + "tc"))
        if cells > REMOTE_FILE_MAX_TABLE_CELLS:
            output.truncated = True
            break
        text = "\n".join(paragraph(p, A) for p in slide.iter(A + "p"))
        rel_name = posixpath.dirname(name) + "/_rels/" + posixpath.basename(name) + ".rels"
        if rel_name in package.archive.namelist():
            notes = package.relationships(rel_name, posixpath.dirname(name), "notesSlide")
            if len(notes) > 1:
                raise ValueError()
            if notes:
                note = package.read_xml(next(iter(notes.values())))
                if note.tag != P + "notes":
                    raise ValueError()
                text += "\n[Speaker notes]\n" + "\n".join(
                    paragraph(p, A) for p in note.iter(A + "p")
                )
        # Coverage truncation must not stop a requested noninitial page range.
        prior = output.truncated
        output.truncated = False
        bounded = output.add("slide", number, text)
        output.truncated = output.truncated or prior
        if not bounded:
            break
    return len(slides)


def extract(stream: BinaryIO, fmt: str, selection: ContentSelection) -> dict[str, Any]:
    selection.validate()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError()
    if fmt not in ("pdf", "pptx") and selection != ContentSelection():
        raise ValueError()
    size = stream.seek(0, 2)
    if size > REMOTE_INSPECTION_MAX_BYTES:
        raise ValueError()
    stream.seek(0)
    output = Output()
    total: int | None = None
    omissions: tuple[str, ...] = ()
    if fmt == "pdf":
        logging.getLogger("pypdf").disabled = True
        with apply_configuration(
            maximum_declared_stream_length=MAX_XML_BYTES,
            array_based_stream_maximum_output_length=MAX_XML_BYTES,
            zlib_maximum_output_length=MAX_XML_BYTES,
            lzw_maximum_output_length=MAX_XML_BYTES,
            run_length_maximum_output_length=MAX_XML_BYTES,
            zlib_maximum_recovery_input_length=262_144,
            page_tree_maximum_entries=2_000,
            page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=100,
            jbig2dec_binary=None,
        ):
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise ValueError()
            total = len(reader.pages)
            for number in _range(total, selection, output):
                prior = output.truncated
                output.truncated = False
                bounded = output.add("page", number, reader.pages[number - 1].extract_text())
                output.truncated = output.truncated or prior
                if not bounded:
                    break
        omissions = ("images_ocr_attachments_and_active_actions_not_processed",)
    elif fmt in ("docx", "pptx"):
        package = Package(stream, fmt)
        try:
            if fmt == "docx":
                omissions = _docx(package, output)
            else:
                total = _pptx(package, output, selection)
                omissions = ("images_media_charts_and_embedded_objects_not_processed",)
        finally:
            package.archive.close()
    else:
        raw = stream.read(REMOTE_INSPECTION_MAX_BYTES + 1)
        text = raw.decode("utf-8-sig")
        if (
            identify(text[:1024].encode("utf-8"), ExpectedFormat.TXT).result
            is not DiagnosticStatus.EXPECTED
        ):
            raise ValueError()
        if "\x00" in text or re.match(r"<(?:[a-z!?])", text.lstrip().lower()):
            raise ValueError()
        if fmt == "json":
            text, output.truncated = bounded_json(_json(text), REMOTE_FILE_MAX_TEXT_CHARS)
        if fmt == "csv":
            csv.field_size_limit(REMOTE_INSPECTION_MAX_BYTES)
            rows = csv.reader(io.StringIO(text))
            cells = 0
            for number, row in enumerate(rows, 1):
                if number > 100 or cells + min(len(row), 32) > REMOTE_FILE_MAX_TABLE_CELLS:
                    output.truncated = True
                    break
                cells += min(len(row), 32)
                columns_cut = len(row) > 32
                if not output.add("csv_row", number, "\t".join(row[:32])):
                    break
                output.truncated = output.truncated or columns_cut
        else:
            output.add("text", 1, text)
    available = any(unit["text"].strip() for unit in output.units)
    return {
        "units": output.units,
        "truncated": output.truncated,
        "content_available": available,
        "reason": None
        if available
        else "image_only_or_no_extractable_text"
        if fmt == "pdf"
        else "no_extractable_text",
        "total_units": total,
        "start_page": selection.start_page,
        "end_page": output.units[-1]["number"] if fmt in ("pdf", "pptx") and output.units else None,
        "omissions": list(omissions),
    }
