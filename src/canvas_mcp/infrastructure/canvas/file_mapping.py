"""Projection deliberately ignores source/preview URLs and user metadata."""

from canvas_mcp.domain.errors import MalformedUpstreamError
from canvas_mcp.domain.models import FileMetadata, FileReference
from canvas_mcp.infrastructure.canvas import academic_mapping as academic
from canvas_mcp.infrastructure.canvas.mapping import _object, entity_id


def file_metadata(value: object, reference: FileReference) -> FileMetadata:
    raw = _object(value)
    identity = entity_id(raw.get("id"))
    if identity != reference.file_id or (
        "course_id" in raw and entity_id(raw["course_id"]) != reference.course_id
    ):
        raise MalformedUpstreamError()
    return FileMetadata(
        identity,
        reference.course_id,
        reference,
        academic.title(raw.get("display_name", raw.get("filename"))),
        academic.observed(raw, "filename", academic.title),
        academic.observed(raw, "content-type", academic.title),
        academic.observed(raw, "size", academic.integer),
        academic.observed(raw, "created_at", academic.timestamp),
        academic.observed(raw, "updated_at", academic.timestamp),
        academic.flag(raw, "locked"),
        academic.flag(raw, "hidden"),
        academic.flag(raw, "locked_for_user"),
        academic.flag(raw, "hidden_for_user"),
    )
