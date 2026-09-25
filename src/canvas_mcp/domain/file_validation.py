"""File input validation and inert reference conversion; no I/O."""

import re

from canvas_mcp.domain.errors import ValidationError
from canvas_mcp.domain.models import (
    Assignment,
    AttachmentMetadata,
    FileFilter,
    FileReference,
    ModuleItem,
    Submission,
)
from canvas_mcp.domain.validation import assignment_filter, canvas_id
from canvas_mcp.domain.models import AssignmentFilter


def file_reference(value: FileReference) -> None:
    if not isinstance(value, FileReference):
        raise ValidationError()
    canvas_id(value.file_id)
    canvas_id(value.course_id)
    if value.source_kind == "course_file":
        if value.source_id is not None or value.module_id is not None:
            raise ValidationError()
    elif value.source_kind in ("assignment_attachment", "submission_attachment", "module_file"):
        canvas_id(value.source_id)
        if value.source_kind == "module_file":
            canvas_id(value.module_id)
        elif value.module_id is not None:
            raise ValidationError()
    else:
        raise ValidationError()


def file_filter(value: FileFilter) -> None:
    if not isinstance(value, FileFilter):
        raise ValidationError()
    assignment_filter(AssignmentFilter(search_term=value.search_term))
    if value.sort not in (
        "name",
        "size",
        "created_at",
        "updated_at",
        "content_type",
    ) or value.order not in ("asc", "desc"):
        raise ValidationError()
    if value.content_type is not None and (
        not isinstance(value.content_type, str)
        or len(value.content_type) > 127
        or re.fullmatch(r"[a-z0-9][a-z0-9.+-]*(?:/[a-z0-9][a-z0-9.+-]*)?", value.content_type)
        is None
    ):
        raise ValidationError()


def assignment_attachment_reference(
    assignment: Assignment, attachment: AttachmentMetadata
) -> FileReference:
    if attachment.id not in {item.id for item in assignment.attachments.value or ()}:
        raise ValidationError()
    reference = FileReference(
        attachment.id, assignment.course_id, "assignment_attachment", assignment.id
    )
    file_reference(reference)
    return reference


def submission_attachment_reference(
    submission: Submission, attachment: AttachmentMetadata
) -> FileReference:
    if attachment.id not in {item.id for item in submission.attachments.value or ()}:
        raise ValidationError()
    reference = FileReference(
        attachment.id, submission.course_id, "submission_attachment", submission.assignment_id
    )
    file_reference(reference)
    return reference


def module_file_reference(item: ModuleItem) -> FileReference:
    if item.kind != "file" or item.target_id is None:
        raise ValidationError()
    reference = FileReference(
        item.target_id, item.course_id, "module_file", item.id, item.module_id
    )
    file_reference(reference)
    return reference
