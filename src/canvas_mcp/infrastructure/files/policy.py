"""Locators and media evidence are untrusted. No content parsing or extraction."""

import codecs
from html import unescape
import re
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

from canvas_mcp.domain.errors import (
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    FileTooLargeError,
    MimeMismatchReason,
    UnsafeRedirectError,
)
from canvas_mcp.domain.models import FileMetadata
from canvas_mcp.domain.reflection import reflects_secrets
from canvas_mcp.infrastructure.config.origin import normalize_origin

Classification = Literal[
    "untrusted_document", "untrusted_text", "opaque_archive", "office_candidate", "untrusted_image"
]
MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "ipynb": "application/x-ipynb+json",
    "py": "text/x-python",
}


def reflects_capability(data: bytes, target: str) -> bool:
    """Reject current secret echoes in common reversible encodings; never redact."""
    normalized = unquote(unescape(target))
    secrets = (target, normalized, normalized.partition("?")[2])
    return reflects_secrets(data, secrets)


def decoded_url_safe(value: str, secret: str) -> bool:
    """Reject reflected credentials or controls at every percent-decoding layer."""
    decoded = value
    for _ in range(32):
        if secret.casefold() in decoded.casefold() or re.search(r"[\x00-\x1f\x7f\\]", decoded):
            return False
        following = unquote(decoded)
        if following == decoded:
            return True
        decoded = following
    # Deeply nested encodings are ambiguous and need no download support.
    return False


def redirect_target(current: str, location: bytes, origins: frozenset[str], secret: str) -> str:
    invalid = False
    try:
        value = location.decode("ascii")
        if (
            not value
            or len(value) > 8192
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)
            or re.search(r"%(?![0-9a-fA-F]{2})", value)
        ):
            raise ValueError()
        target = urljoin(current, value)
        parsed = urlsplit(target)
        origin = normalize_origin(parsed.scheme + "://" + parsed.netloc)
        if (
            origin not in origins
            or parsed.fragment
            or "#" in value
            or not decoded_url_safe(target, secret)
        ):
            raise ValueError()
        return origin + (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    except (ValueError, UnicodeError, ConfigurationError):
        invalid = True
    if invalid:
        raise UnsafeRedirectError()
    raise UnsafeRedirectError()


def metadata_policy(metadata: FileMetadata, maximum: int) -> tuple[str, Classification]:
    if any(
        value is True
        for value in (
            metadata.locked,
            metadata.hidden,
            metadata.locked_for_user,
            metadata.hidden_for_user,
        )
    ):
        raise DownloadRejectedError(reason=DownloadRejectionReason.METADATA_RESTRICTED)
    if metadata.size.value is not None and metadata.size.value > maximum:
        raise FileTooLargeError()
    name = metadata.filename.value
    extension = name.text.rsplit(".", 1)[-1].lower() if name is not None else ""
    if extension not in MEDIA:
        raise DownloadRejectedError(reason=DownloadRejectionReason.UNSUPPORTED_EXTENSION)
    expected = MEDIA[extension]
    declared = metadata.content_type.value
    if declared is not None:
        validate_media(declared.text, expected, source="canvas")
    classification: Classification = (
        "untrusted_image"
        if extension in ("png", "jpg", "jpeg", "webp")
        else "untrusted_document"
        if extension == "pdf"
        else "opaque_archive"
        if extension == "zip"
        else "office_candidate"
        if extension in ("docx", "pptx", "xlsx")
        else "untrusted_text"
    )
    return expected, classification


def validate_media(value: str, expected: str, *, source: Literal["canvas", "http"]) -> str:
    media = value.split(";", 1)[0].strip().lower()
    # Generic binary metadata is not positive format evidence. Require prefix
    # consistency below; even then every candidate remains untrusted.
    if media not in (expected, "application/octet-stream") and not (
        expected
        in (
            "text/plain",
            "text/markdown",
            "text/csv",
            "text/x-python",
            "application/json",
            "application/x-ipynb+json",
        )
        and media in ("text/plain", "application/json", "text/x-python-script")
    ):
        raise DownloadRejectedError(
            reason=DownloadRejectionReason.MIME_MISMATCH,
            mime_reason=(
                MimeMismatchReason.CANVAS_MIME_VS_EXTENSION
                if source == "canvas"
                else MimeMismatchReason.HTTP_MIME_MISSING
                if not media
                else MimeMismatchReason.HTTP_MIME_VS_EXTENSION
            ),
        )
    return media


class BytePolicy:
    def __init__(self, classification: Classification) -> None:
        self.classification = classification
        self.prefix = b""
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")

    def update(self, data: bytes) -> None:
        self.prefix = (self.prefix + data[:512])[:512]
        if self.classification == "untrusted_text":
            invalid = False
            try:
                self.decoder.decode(data)
            except UnicodeError:
                invalid = True
            if invalid or b"\x00" in data:
                raise DownloadRejectedError(reason=DownloadRejectionReason.INVALID_TEXT)

    def finish(self) -> None:
        prefix = self.prefix.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if prefix.startswith((b"mz", b"\x7felf", b"<!doctype html", b"<html", b"<svg")):
            raise DownloadRejectedError(reason=DownloadRejectionReason.UNSAFE_PREFIX)
        if self.classification == "untrusted_document" and not self.prefix.startswith(b"%PDF-"):
            raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)
        if self.classification == "untrusted_image" and not (
            self.prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
            or self.prefix[:4] == b"RIFF"
            and self.prefix[8:12] == b"WEBP"
        ):
            raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)
        if self.classification in (
            "opaque_archive",
            "office_candidate",
        ) and not self.prefix.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)
        if self.classification == "untrusted_text":
            invalid = False
            try:
                self.decoder.decode(b"", final=True)
            except UnicodeError:
                invalid = True
            if invalid:
                raise DownloadRejectedError(reason=DownloadRejectionReason.INVALID_TEXT)
