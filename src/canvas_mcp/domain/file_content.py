"""Inert remote document results and fixed resource bounds; never artifact locators."""

from dataclasses import dataclass, field
import json
from typing import Literal

from canvas_mcp.domain.errors import ValidationError
from canvas_mcp.domain.models import FileMetadata
from canvas_mcp.domain.remote_artifact import RemoteArtifact

REMOTE_INSPECTION_MAX_BYTES = 8 * 1024 * 1024
REMOTE_FILE_MAX_TEXT_CHARS = 16_000
REMOTE_FILE_MAX_PAGES = 30
REMOTE_FILE_MAX_TABLE_CELLS = 2_000
REMOTE_FILE_TIMEOUT_SECONDS = 50.0
REMOTE_PARSER_TIMEOUT_SECONDS = 20.0
REMOTE_PARSER_MEMORY_BYTES = 256 * 1024 * 1024
IMAGE_FORMATS = frozenset(("png", "jpg", "jpeg", "webp"))
SUPPORTED_FORMATS = frozenset(("pdf", "docx", "pptx", "txt", "md", "csv", "json")) | IMAGE_FORMATS
OCR_MAX_PAGES = 3
OCR_MAX_SOURCE_PIXELS = 12_000_000
OCR_MAX_RENDER_PIXELS = 4_000_000
OCR_MAX_DIMENSION = 8_192
OCR_NATIVE_MIN_CHARACTERS = 80
REMOTE_ARTIFACT_MAX_BYTES = 4 * 1024 * 1024


def bounded_json(value: object, maximum: int) -> tuple[str, bool]:
    """Retain valid JSON structure, clipping strings and omitting trailing members.

    No placeholders or renamed keys: the truncation flag carries the omission.
    Input syntax/depth is validated by the reader before this bounded formatter.
    """
    if maximum < 2:
        raise ValueError()
    cut = False

    def encode(item: object) -> str:
        return json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def visit(item: object, budget: int, depth: int = 0) -> str | None:
        nonlocal cut
        if depth > 64:
            raise ValueError()
        if isinstance(item, str):
            prefix = item[:budget]
            lo, hi = 0, len(prefix)
            while lo < hi:
                middle = (lo + hi + 1) // 2
                if len(encode(prefix[:middle])) <= budget:
                    lo = middle
                else:
                    hi = middle - 1
            cut |= lo < len(item)
            return encode(prefix[:lo]) if budget >= 2 else None
        if isinstance(item, (dict, list)):
            if budget < 2:
                return None
            pieces: list[str] = []
            remaining = budget - 2
            entries = item.items() if isinstance(item, dict) else enumerate(item)
            for index, (key, child) in enumerate(entries):
                if index >= 256:
                    cut = True
                    break
                key_text = encode(key) + ":" if isinstance(item, dict) else ""
                overhead = len(key_text) + bool(pieces)
                if overhead >= remaining:
                    cut = True
                    break
                text = visit(child, remaining - overhead, depth + 1)
                if text is None:
                    cut = True
                    break
                pieces.append(key_text + text)
                remaining -= overhead + len(text)
            opening, closing = ("{", "}") if isinstance(item, dict) else ("[", "]")
            return opening + ",".join(pieces) + closing
        text = encode(item)
        return text if len(text) <= budget else None

    text = visit(value, maximum)
    if text is None:
        raise ValueError()
    return text, cut


@dataclass(frozen=True)
class ContentSelection:
    start_page: int = 1
    end_page: int | None = None

    def validate(self) -> None:
        if (
            type(self.start_page) is not int
            or not 1 <= self.start_page <= 2_000
            or self.end_page is not None
            and (
                type(self.end_page) is not int
                or not self.start_page <= self.end_page <= 2_000
                or self.end_page - self.start_page + 1 > REMOTE_FILE_MAX_PAGES
            )
        ):
            raise ValidationError()


@dataclass(frozen=True)
class ContentUnit:
    kind: Literal["page", "slide", "paragraph", "heading", "table_row", "text", "csv_row"]
    number: int
    text: str


@dataclass(frozen=True)
class FileContent:
    metadata: FileMetadata
    format: str
    size: int
    sha256: str
    units: tuple[ContentUnit, ...]
    truncated: bool
    content_available: bool
    reason: str | None
    total_units: int | None
    start_page: int
    end_page: int | None
    omissions: tuple[str, ...] = ()
    extraction_mode: Literal["native", "ocr", "hybrid"] = "native"
    ocr_pages: tuple[int, ...] = ()
    page_count_processed: int = 0
    native_pages: tuple[int, ...] = ()
    limitations: tuple[str, ...] = ()
    original: RemoteArtifact | None = field(default=None, repr=False, compare=False)
    original_unavailable_reason: str | None = field(default=None, compare=False)
