"""Explicit local-only exception to the final HTTP MIME-claim check."""

from urllib.parse import unquote

from canvas_mcp.domain.errors import (
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    MimeMismatchReason,
)
from canvas_mcp.domain.mime import (
    ExpectedFormat,
    MimeScope,
    RevalidationTicket,
    RedirectHop,
    RedirectOriginClass,
    ResponseContentClass,
)
from canvas_mcp.infrastructure.files.mime_registry import normalized_mime
from canvas_mcp.infrastructure.files.policy import MEDIA, validate_media
from canvas_mcp.ports.mime import MimeCompatibilityRegistry


def diagnostic_mime(value: str, secret: str) -> str:
    # Never persist raw parameters, URL syntax, or a reflected credential (even
    # case-folded/percent-encoded in the media token itself).
    if secret.casefold() in unquote(value).casefold():
        raise DownloadRejectedError(reason=DownloadRejectionReason.CREDENTIAL_REFLECTION)
    return normalized_mime(value)


def response_content_class(fields: list[tuple[bytes, bytes]]) -> ResponseContentClass:
    """Classify the header claim only; no body reading or raw value retention."""
    values = [value for name, value in fields if name.lower() == b"content-type"]
    if len(values) != 1 or len(values[0]) > 1024:
        return ResponseContentClass.OTHER
    media = values[0].split(b";", 1)[0].strip().lower()
    if media in (b"text/html", b"application/xhtml+xml"):
        return ResponseContentClass.HTML
    if media in (b"application/json", b"text/json", b"application/x-ipynb+json"):
        return ResponseContentClass.JSON
    if media in (
        b"application/octet-stream",
        b"binary/octet-stream",
        b"application/pdf",
        b"application/zip",
        *(MEDIA[ext].encode("ascii") for ext in ("docx", "pptx", "xlsx")),
    ):
        return ResponseContentClass.BINARY
    return ResponseContentClass.OTHER


class MimeObservation:
    def __init__(
        self,
        scope: MimeScope,
        expected: ExpectedFormat,
        registry: MimeCompatibilityRegistry | None,
        *,
        allow_mismatch: bool,
        headers_only: bool = False,
    ) -> None:
        if (
            allow_mismatch is not True
            or type(headers_only) is not bool
            or (not headers_only and registry is None)
        ):
            raise ConfigurationError()
        self.scope, self.expected, self.registry = scope, expected, registry
        self.http_mime: str | None = None
        self.mismatch = False
        self.ticket: RevalidationTicket | None = None
        self._headers_only = headers_only
        self.hops: list[RedirectHop] = []

    @property
    def headers_only(self) -> bool:
        return self._headers_only

    def record_hop(
        self,
        index: int,
        origin: RedirectOriginClass,
        status: int,
        authorization_attached: bool,
        fields: list[tuple[bytes, bytes]],
    ) -> None:
        if (
            type(index) is not int
            or index != len(self.hops)
            or not 0 <= index <= 3
            or type(origin) is not RedirectOriginClass
            or type(status) is not int
            or not 100 <= status <= 599
            or type(authorization_attached) is not bool
        ):
            raise DownloadRejectedError(reason=DownloadRejectionReason.PROTOCOL_ERROR)
        self.hops.append(
            RedirectHop(
                index, origin, status, authorization_attached, response_content_class(fields)
            )
        )

    def check_http(self, raw: str, expected_media: str, secret: str) -> str:
        if self.headers_only:
            # A trace neither overrides MIME nor touches registry state. Even a
            # compatible header is not evidence of a downloaded/validated body.
            media = validate_media(raw, expected_media, source="http")
            diagnostic_mime(raw, secret)
            return media
        # Missing, malformed, or secret-bearing MIME is not a compatibility alias.
        try:
            media = validate_media(raw, expected_media, source="http")
        except DownloadRejectedError as error:
            if (
                type(error) is not DownloadRejectedError
                or error.reason is not DownloadRejectionReason.MIME_MISMATCH
                or error.mime_reason is not MimeMismatchReason.HTTP_MIME_VS_EXTENSION
            ):
                raise
            media = diagnostic_mime(raw, secret)
            assert self.registry is not None
            self.ticket = self.registry.begin_revalidation(self.scope, self.expected, media)
            self.mismatch = True
        self.http_mime = diagnostic_mime(raw, secret)
        return media
