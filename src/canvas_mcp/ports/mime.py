"""No model-facing rule insertion: infrastructure supplies validated evidence."""

from typing import Protocol

from canvas_mcp.domain.mime import (
    ExpectedFormat,
    FormatEvidence,
    MimeScope,
    MimeRule,
    RevalidationTicket,
)


class MimeCompatibilityRegistry(Protocol):
    def lookup(
        self, scope: MimeScope, expected: ExpectedFormat, http_mime: str
    ) -> MimeRule | None: ...

    def begin_revalidation(
        self, scope: MimeScope, expected: ExpectedFormat, http_mime: str
    ) -> RevalidationTicket | None: ...

    def record_validated(
        self,
        scope: MimeScope,
        http_mime: str,
        evidence: FormatEvidence,
        *,
        remember: bool,
        ticket: RevalidationTicket | None,
    ) -> str: ...
