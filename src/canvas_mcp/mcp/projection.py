"""Allowlisted, JSON-safe local MCP projections of normalized application data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from dataclasses import replace
import json
from typing import Any, Literal, TypeVar, TypedDict, cast

from pydantic_core import to_json
from mcp.types import CallToolResult, TextContent

from canvas_mcp.application.contracts import AssignmentContext, Result, Workload, Warning
from canvas_mcp.domain.file_content import FileContent, bounded_json, REMOTE_ARTIFACT_MAX_BYTES
from canvas_mcp.domain.errors import BudgetExceededError
from canvas_mcp.domain.models import (
    Announcement,
    Assignment,
    AttachmentMetadata,
    CalendarEvent,
    Course,
    CourseGrade,
    DownloadedFile,
    ExternalText,
    FileMetadata,
    FileReference,
    Module,
    ModuleItem,
    ModuleSequence,
    Observed,
    Page,
    Profile,
    RubricCriterion,
    RubricRating,
    Submission,
)

T = TypeVar("T")
MAX_MCP_OUTPUT_BYTES = 131_072


class WarningDTO(TypedDict):
    component: str
    code: str
    course_id: int | None


class McpResult(TypedDict):
    data: dict[str, Any]
    request_id: str
    complete: bool
    warnings: list[WarningDTO]
    observed_at: str


def _date(value: datetime) -> str:
    return value.isoformat()


def _text(value: ExternalText) -> dict[str, Any]:
    return {"text": value.text, "truncated": value.truncated, "trust": "untrusted"}


def _observed(value: Observed[T], project: Callable[[T], Any]) -> dict[str, Any]:
    return {
        "state": value.state.value,
        "value": None if value.value is None else project(value.value),
        "truncated": value.truncated,
    }


def _identity(value: T) -> T:
    return value


def _entity(value: str) -> int:
    """Canvas ingress already validated canonical positive decimal identities."""
    return int(value)


def _optional_entity(value: str | None) -> int | None:
    return None if value is None else _entity(value)


def _page(value: Page[T], project: Callable[[T], Any]) -> dict[str, Any]:
    return {
        "items": [project(item) for item in value.items],
        "next_cursor": value.next_cursor,
        "complete": value.complete,
    }


def envelope(
    value: Result[T],
    project: Callable[[T], dict[str, Any]],
    *,
    maximum_wire_bytes: int = MAX_MCP_OUTPUT_BYTES,
) -> McpResult:
    output: McpResult = {
        "data": project(value.data),
        "request_id": value.request_id,
        "complete": value.complete,
        "warnings": [
            {
                "component": item.component,
                "code": item.code,
                "course_id": _optional_entity(item.course_id),
            }
            for item in value.warnings
        ],
        "observed_at": _date(value.observed_at),
    }
    _fit_assignment_output(output, min(MAX_MCP_OUTPUT_BYTES, maximum_wire_bytes))
    # FastMCP emits both a pretty JSON TextContent block and structuredContent.
    # Serialize their exact MCP result shape, allowing for JSON-RPC framing.
    pretty = to_json(output, indent=2).decode("utf-8")
    wire_result = CallToolResult(
        content=[TextContent(type="text", text=pretty)],
        structuredContent=cast(dict[str, Any], output),
    )
    wire_bytes = len(wire_result.model_dump_json().encode("utf-8")) + 1_024
    if wire_bytes > min(MAX_MCP_OUTPUT_BYTES, maximum_wire_bytes):
        raise BudgetExceededError()
    return output


def _fit_assignment_output(output: McpResult, maximum: int) -> None:
    """Fit the complete aggregate, keeping authoritative source and relationships first."""
    data = output["data"]
    record = data.get("assignment", data)
    if not isinstance(record, dict) or "description_verbatim_text" not in record:
        return

    def fits() -> bool:
        pretty = to_json(output, indent=2).decode("utf-8")
        wire = CallToolResult(
            content=[TextContent(type="text", text=pretty)],
            structuredContent=cast(dict[str, Any], output),
        )
        return len(wire.model_dump_json().encode("utf-8")) + 1024 <= maximum

    def shrink(text: dict[str, Any]) -> None:
        original = text["text"]
        lower, upper = 0, len(original)
        while lower < upper:
            middle = (lower + upper + 1) // 2
            text["text"] = original[:middle]
            text["truncated"] = True
            if fits():
                lower = middle
            else:
                upper = middle - 1
        text["text"] = original[:lower]
        text["truncated"] = True

    if fits():
        return
    legacy = record.get("description", {}).get("value")
    if legacy is not None:
        shrink(legacy)
    if fits():
        return
    candidates = data.get("material_candidates")
    if candidates:
        data["material_candidates"] = []
        data["material_candidates_truncated"] = True
        output["complete"] = False
        output["warnings"].append(
            {
                "component": "assignment_context",
                "code": "material_candidate_hints_omitted",
                "course_id": None,
            }
        )
    if fits():
        return
    source = record.get("description_verbatim_text", {}).get("value")
    if source is not None:
        output["complete"] = False
        output["warnings"].append(
            {"component": "assignment", "code": "content_truncated", "course_id": None}
        )
        shrink(source)


def profile(value: Profile) -> dict[str, Any]:
    return {"name": _text(value.display_name), "timezone": _observed(value.timezone, _identity)}


def course(value: Course) -> dict[str, Any]:
    return {
        "course_id": _entity(value.id),
        "name": _text(value.name),
        "course_code": _text(value.code),
        "term": _observed(value.term, _text),
    }


def submission(value: Submission) -> dict[str, Any]:
    return {
        "state": value.state.value,
        "submitted_at": _observed(value.submitted_at, _date),
        "graded": value.graded,
        "late": value.late,
        "missing": value.missing,
        "excused": value.excused,
        "required": value.required,
        "attachments": _observed(value.attachments, lambda xs: [attachment(x) for x in xs]),
    }


def assignment_summary(value: Assignment) -> dict[str, Any]:
    own = value.submission.value
    return {
        "assignment_id": _entity(value.id),
        "course_id": _entity(value.course_id),
        "name": _text(value.title),
        "due_at": _observed(value.due_at, _date),
        "points_possible": _observed(value.points, _identity),
        "submitted": None if own is None else own.state.value,
        "late": None if own is None else own.late,
        "missing": None if own is None else own.missing,
        "graded": None if own is None else own.graded,
    }


def _material(value: Any) -> dict[str, Any]:
    return {
        "label": _text(value.label),
        "kind": value.kind,
        "target_id": _optional_entity(value.target_id),
        "state": value.state,
    }


def assignment_detail(value: Assignment) -> dict[str, Any]:
    return {
        **assignment_summary(value),
        "description": _observed(value.description, _text),
        "description_verbatim_text": _observed(value.description_verbatim, _text),
        "description_available": (
            bool(value.description_verbatim.value and value.description_verbatim.value.text.strip())
            if value.description_verbatim.state.value == "available"
            else None
        ),
        "description_redacted": value.description_redacted,
        "description_nontext_content": value.description_nontext_content,
        "submission_types": [_text(item) for item in value.submission_types],
        "references": _observed(value.references, lambda xs: [_material(x) for x in xs]),
        "unlock_at": _observed(value.unlock_at, _date),
        "lock_at": _observed(value.lock_at, _date),
        "allowed_attempts": _observed(value.allowed_attempts, _identity),
        "published": value.published,
        "required": value.required,
    }


def attachment(value: AttachmentMetadata) -> dict[str, Any]:
    return {
        "file_id": _entity(value.id),
        "display_name": _text(value.display_name),
        "content_type": _observed(value.content_type, _text),
        "size": _observed(value.size, _identity),
    }


def _rating(value: RubricRating) -> dict[str, Any]:
    return {
        "description": _text(value.description),
        "points": _observed(value.points, _identity),
        "long_description": _observed(value.long_description, _text),
    }


def _criterion(value: RubricCriterion) -> dict[str, Any]:
    return {
        "description": _text(value.description),
        "points": _observed(value.points, _identity),
        "long_description": _observed(value.long_description, _text),
        "ratings": [_rating(item) for item in value.ratings],
    }


def module(value: Module) -> dict[str, Any]:
    return {
        "module_id": _entity(value.id),
        "course_id": _entity(value.course_id),
        "name": _text(value.title),
        "position": value.position,
        "items_count": value.items_count,
    }


def module_item(value: ModuleItem) -> dict[str, Any]:
    output = {
        "item_id": _entity(value.id),
        "module_id": _entity(value.module_id),
        "course_id": _entity(value.course_id),
        "name": _text(value.title),
        "kind": value.kind,
        "target_id": _optional_entity(value.target_id),
        "position": value.position,
    }
    if value.kind == "file" and value.target_id is not None:
        output["file_reference"] = {
            "course_id": _entity(value.course_id),
            "file_id": _entity(value.target_id),
            "source_kind": "module_file",
            "source_id": _entity(value.id),
            "module_id": _entity(value.module_id),
        }
    return output


def _sequence(value: ModuleSequence) -> dict[str, Any]:
    return {
        "module": module(value.module),
        "current_item": module_item(value.current_item),
        "previous_item": None if value.previous_item is None else module_item(value.previous_item),
        "next_item": None if value.next_item is None else module_item(value.next_item),
    }


def assignment_context(value: AssignmentContext) -> dict[str, Any]:
    def sourced_attachments(xs: tuple[AttachmentMetadata, ...]) -> list[dict[str, Any]]:
        return [
            {
                **attachment(item),
                "file_reference": {
                    "course_id": _entity(value.course.id),
                    "file_id": _entity(item.id),
                    "source_kind": "assignment_attachment",
                    "source_id": _entity(value.assignment.id),
                    "module_id": None,
                },
            }
            for item in xs
        ]

    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()

    def candidate(
        reference: dict[str, Any],
        name: dict[str, Any],
        relationship: str,
        reason: str,
        confidence: str,
    ) -> None:
        identity = reference["file_id"]
        if identity not in seen:
            seen.add(identity)
            candidates.append(
                {
                    "file_reference": reference,
                    "display_name": name,
                    "relationship": relationship,
                    "reason_for_relevance": reason,
                    "confidence": confidence,
                }
            )

    for item in sourced_attachments(value.attachments.value or ()):
        candidate(
            item["file_reference"],
            item["display_name"],
            "direct_attachment",
            "Attached to this assignment record; inspect if it contains requirements.",
            "direct",
        )
    for linked in value.assignment.references.value or ():
        if linked.kind == "file" and linked.target_id is not None:
            # An HTML link is evidence, not an assignment_attachment authorization.
            # Course file reads still verify course membership and file access.
            candidate(
                {
                    "course_id": _entity(value.course.id),
                    "file_id": _entity(linked.target_id),
                    "source_kind": "course_file",
                    "source_id": None,
                    "module_id": None,
                },
                _text(linked.label),
                "assignment_link",
                "Referenced by this assignment body; file contents have not been inspected.",
                "direct",
            )
    for sequence in value.module_context.value or ():
        for neighbor in (sequence.previous_item, sequence.next_item):
            if neighbor and neighbor.kind == "file" and neighbor.target_id is not None:
                projected = module_item(neighbor)
                candidate(
                    projected["file_reference"],
                    projected["name"],
                    "module_neighbor",
                    "Immediately adjacent to this assignment in its module; adjacency alone does not establish relevance.",
                    "structural_candidate",
                )
    return {
        "course": course(value.course),
        "assignment": assignment_detail(value.assignment),
        "submission": _observed(value.submission, submission),
        "rubric": _observed(value.rubric, lambda xs: [_criterion(x) for x in xs]),
        "attachments": _observed(value.attachments, sourced_attachments),
        "module_context": _observed(value.module_context, lambda xs: [_sequence(x) for x in xs]),
        "material_candidates": candidates,
        "material_discovery_complete": False,
    }


def workload(value: Workload) -> dict[str, Any]:
    return {
        "items": _page(
            value.items,
            lambda item: {
                "course": course(item.course),
                "assignment": assignment_summary(item.assignment),
                "submission": _observed(item.submission, submission),
                "due_state": item.due_state,
            },
        ),
        "coverage": {
            "requested": [_entity(item) for item in value.coverage.requested],
            "scanned": [_entity(item) for item in value.coverage.scanned],
            "failed": [_entity(item) for item in value.coverage.failed],
            "discovery_complete": value.coverage.discovery_complete,
        },
    }


def announcement(value: Announcement) -> dict[str, Any]:
    return {
        "announcement_id": _entity(value.id),
        "course_id": _entity(value.course_id),
        "title": _text(value.title),
        "body": _text(value.body),
        "published_at": _observed(value.published_at, _date),
    }


def calendar_event(value: CalendarEvent) -> dict[str, Any]:
    return {
        "event_id": _entity(value.id),
        "course_id": _entity(value.course_id),
        "title": _text(value.title),
        "starts_at": _observed(value.starts_at, _date),
        "ends_at": _observed(value.ends_at, _date),
        "all_day": value.all_day,
        "assignment_id": _optional_entity(value.assignment_id),
        "description": _observed(value.description, _text),
    }


def grade(value: CourseGrade) -> dict[str, Any]:
    available = any(
        field.value is not None
        for field in (
            value.current_score,
            value.current_grade,
            value.final_score,
            value.final_grade,
        )
    )
    return {
        "course_id": _entity(value.course_id),
        "available": available,
        "current_score": _observed(value.current_score, _identity),
        "current_grade": _observed(value.current_grade, _text),
        "final_score": _observed(value.final_score, _identity),
        "final_grade": _observed(value.final_grade, _text),
    }


def file_reference(value: FileReference) -> dict[str, Any]:
    return {
        "course_id": _entity(value.course_id),
        "file_id": _entity(value.file_id),
        "source_kind": value.source_kind,
        "source_id": _optional_entity(value.source_id),
        "module_id": _optional_entity(value.module_id),
    }


def file_metadata(value: FileMetadata) -> dict[str, Any]:
    return {
        "file_id": _entity(value.id),
        "course_id": _entity(value.course_id),
        "display_name": _text(value.display_name),
        "content_type": _observed(value.content_type, _text),
        "size": _observed(value.size, _identity),
        "created_at": _observed(value.created_at, _date),
        "updated_at": _observed(value.updated_at, _date),
        "file_reference": file_reference(value.source),
        "trust": "untrusted",
    }


def downloaded_file(value: DownloadedFile) -> dict[str, Any]:
    return {
        "artifact_id": value.artifact_id,
        "managed_local_path": value.local_path,
        "safe_filename": value.safe_filename,
        "content_type": value.content_type,
        "size": value.size,
        "sha256": value.sha256,
        "trust": "untrusted",
    }


def file_content(value: FileContent) -> dict[str, Any]:
    """Positive allowlist: never serialize the ephemeral artifact or parser internals."""
    name = value.metadata.display_name
    return {
        "file": {
            "file_id": _entity(value.metadata.id),
            "file_reference": file_reference(value.metadata.source),
            "display_name": _text(
                replace(
                    name, text=name.text[:256], truncated=name.truncated or len(name.text) > 256
                )
            ),
            "content_type": value.metadata.content_type.value.text[:127]
            if value.metadata.content_type.value
            else "application/octet-stream",
            "size": value.size,
            "sha256": value.sha256,
            "trust": "untrusted",
            "original_download_available": value.original is not None,
            "original_download_reason": None
            if value.original is not None
            else value.original_unavailable_reason
            or (
                "original_too_large_for_chat_transfer"
                if value.size > REMOTE_ARTIFACT_MAX_BYTES
                else "content_unavailable"
                if not value.content_available
                else "original_not_prepared"
            ),
        },
        "content": {
            "format": value.format,
            "units": [
                {"kind": unit.kind, "number": unit.number, "text": unit.text}
                for unit in value.units
            ],
            "truncated": value.truncated,
            "content_available": value.content_available,
            "reason": value.reason,
            "total_units": value.total_units,
            "start_page": value.start_page,
            "end_page": value.end_page,
            "omissions": list(value.omissions),
            "extraction_mode": value.extraction_mode,
            "ocr_used": bool(value.ocr_pages),
            "ocr_pages": list(value.ocr_pages),
            "native_pages": list(value.native_pages),
            "page_count_processed": value.page_count_processed,
            "limitations": list(value.limitations),
            "trust": "untrusted",
        },
    }


def file_content_envelope(
    value: Result[FileContent], *, maximum_wire_bytes: int = MAX_MCP_OUTPUT_BYTES
) -> McpResult:
    """Fit actual duplicate MCP JSON including Unicode/escape/segment overhead."""
    current = value
    maximum = sum(len(unit.text) for unit in value.data.units)
    while True:
        try:
            return envelope(current, file_content, maximum_wire_bytes=maximum_wire_bytes)
        except BudgetExceededError:
            if maximum == 0:
                raise
            maximum //= 2
            remaining = maximum
            units = []
            for unit in value.data.units:
                if remaining <= 0:
                    break
                if value.data.format == "json":
                    if remaining < 2:
                        break
                    text, _ = bounded_json(json.loads(unit.text), remaining)
                else:
                    text = unit.text[:remaining]
                units.append(replace(unit, text=text))
                remaining -= len(text)
            available = any(unit.text.strip() for unit in units)
            content = replace(
                value.data,
                units=tuple(units),
                truncated=True,
                content_available=available,
                reason=value.data.reason if available else "output_limit_no_extractable_text",
                end_page=units[-1].number
                if units and value.data.format in ("pdf", "pptx")
                else None,
            )
            warning = Warning("file_content", "file_content_truncated")
            unavailable = (
                () if available else (Warning("file_content", "file_content_unavailable"),)
            )
            current = replace(
                value,
                data=content,
                complete=False,
                warnings=tuple(dict.fromkeys((*value.warnings, warning, *unavailable))),
            )


def for_transport(value: McpResult, transport: Literal["stdio", "http"]) -> McpResult:
    """Project an artifact descriptor without exposing server paths over HTTP.

    Downloads are withheld on HTTP in 6.1. This explicit allowlist also protects
    the descriptor when remote artifact handling is added in 6.3.
    """
    if transport == "stdio":
        return value
    data = value["data"]
    if "artifact_id" in data:
        data = {
            key: data[key]
            for key in ("artifact_id", "size", "sha256", "content_type", "trust")
            if key in data
        }
    return {**value, "data": data}
