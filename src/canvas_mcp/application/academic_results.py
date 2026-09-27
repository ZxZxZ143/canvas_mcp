"""Warnings for normalized records only; no serialization of provider internals."""

from dataclasses import fields, is_dataclass
from typing import TypeVar

from canvas_mcp.application.connection import ConnectionService
from canvas_mcp.application.contracts import Result, Warning
from canvas_mcp.domain.models import (
    Availability,
    Assignment,
    ExternalText,
    MaterialReference,
    Observed,
    Page,
    RequestContext,
)

T = TypeVar("T")


def result(
    data: T,
    ctx: RequestContext,
    component: str,
    warnings: tuple[Warning, ...] = (),
    complete: bool = True,
) -> Result[T]:
    codes: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Assignment) and value.description_redacted:
            codes.add("source_text_redacted")
        if isinstance(value, Assignment) and value.description_nontext_content:
            codes.add("nontext_description_not_inspected")
        if isinstance(value, Observed):
            if value.state in (Availability.UNAVAILABLE, Availability.NOT_SUPPORTED):
                codes.add("optional_metadata_unavailable")
            if value.truncated:
                codes.add("content_truncated")
        if isinstance(value, ExternalText) and value.truncated:
            codes.add("content_truncated")
        if isinstance(value, MaterialReference) and value.state != "resolved":
            codes.add("references_not_fetched")
        if isinstance(value, Page) and not value.complete:
            # A workload can be incomplete without a resumable cursor. Its
            # per-course warnings/coverage explain why; do not imply more pages.
            if value.next_cursor is not None:
                codes.add("more_pages")
        if is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                visit(getattr(value, item.name))
        elif isinstance(value, tuple):
            for item in value:
                visit(item)

    visit(data)
    warnings = tuple(
        dict.fromkeys((*warnings, *(Warning(component, code) for code in sorted(codes))))
    )
    if isinstance(data, Page):
        complete = complete and data.complete
    response = Result(data, ctx.request_id, ctx.as_of, complete and not warnings, warnings)
    ConnectionService._bound_result(response, ctx)
    return response
