"""Allowlisted academic mappings: inert content, own-user evidence, no source URLs."""

import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, TypeVar, Literal
from urllib.parse import urlsplit

from canvas_mcp.domain.errors import AuthorizationError, BudgetExceededError, MalformedUpstreamError
from canvas_mcp.domain.models import (
    Announcement,
    Assignment,
    AttachmentMetadata,
    Availability,
    CalendarEvent,
    CompletionRequirement,
    Course,
    CourseGrade,
    EntityId,
    ExternalText,
    MaterialReference,
    Module,
    ModuleItem,
    ModuleSequence,
    Observed,
    RubricCriterion,
    RubricRating,
    Submission,
    SubmissionState,
)
from canvas_mcp.infrastructure.canvas.mapping import _object, course, entity_id
from canvas_mcp.infrastructure.canvas.text import inert_text

T = TypeVar("T")


def content(value: object, maximum: int = 16000) -> ExternalText:
    if not isinstance(value, str):
        raise MalformedUpstreamError()
    # Plain content, never raw HTML. URLs, including signed query strings, are
    # omitted even if written visibly rather than inside an HTML attribute.
    clean = inert_text(value)
    clean = re.sub(
        r"(?i)(?:(?:https?|file|ftp|data|javascript):|(?<!\w)//(?=[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:[/:?#]|$)|\[[a-f0-9:]+\])|(?<!\w)/(?:api/v1|courses|files|users|calendar|announcements)/)[^\s<>]+",
        "[reference omitted]",
        clean,
    )
    clean = re.sub(
        r"(?i)[^\s<>]*[?&](?:verifier|access_token|token|signature|x-amz-signature)=[^\s<>]*",
        "[reference omitted]",
        clean,
    )
    return ExternalText(clean[:maximum], len(clean) > maximum)


def title(value: object) -> ExternalText:
    if not isinstance(value, str) or not value.strip():
        raise MalformedUpstreamError()
    item = content(value, 512)
    return ExternalText(item.text.replace("\n", " ").replace("\t", " "), item.truncated)


def number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MalformedUpstreamError()
    failed = False
    result = 0.0
    try:
        result = float(value)
    except (OverflowError, ValueError):
        failed = True
    if failed or not math.isfinite(result):
        raise MalformedUpstreamError()
    return result


def integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise MalformedUpstreamError()
    return value


def timestamp(value: object) -> datetime:
    failed = False
    result = None
    try:
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
        ):
            failed = True
        else:
            if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
                failed = True
            else:
                result = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
    except (ValueError, OverflowError):
        failed = True
    if failed or result is None:
        raise MalformedUpstreamError()
    return result


def observed(raw: dict[str, object], key: str, mapper: Callable[[object], T]) -> Observed[T]:
    if key not in raw:
        return Observed(Availability.UNAVAILABLE, None)
    return Observed(Availability.AVAILABLE, None if raw[key] is None else mapper(raw[key]))


def flag(raw: dict[str, object], key: str) -> bool | None:
    value = raw.get(key)
    if value is not None and type(value) is not bool:
        raise MalformedUpstreamError()
    return value


def array(value: object, maximum: int = 100) -> list[object]:
    if not isinstance(value, list):
        raise MalformedUpstreamError()
    if len(value) > maximum:
        raise BudgetExceededError()
    return value


def attachment(value: object) -> AttachmentMetadata:
    raw = _object(value)
    return AttachmentMetadata(
        entity_id(raw.get("id")),
        title(raw.get("display_name", raw.get("filename"))),
        observed(raw, "filename", title),
        observed(raw, "content-type", title),
        observed(raw, "size", integer),
        observed(raw, "created_at", timestamp),
        observed(raw, "updated_at", timestamp),
    )


def attachments(value: object) -> tuple[AttachmentMetadata, ...]:
    return tuple(attachment(item) for item in array(value))


def rubric(value: object) -> tuple[RubricCriterion, ...]:
    rows = []
    for item in array(value):
        raw = _object(item)
        ratings = []
        for rating in array([] if raw.get("ratings") is None else raw["ratings"]):
            r = _object(rating)
            ratings.append(
                RubricRating(
                    title(r.get("id")),
                    content(r.get("description", "")),
                    observed(r, "points", number),
                    observed(r, "long_description", content),
                )
            )
        # Canvas rubric IDs are opaque strings (e.g. crit1), not REST numeric IDs.
        rows.append(
            RubricCriterion(
                title(raw.get("id")),
                content(raw.get("description", "")),
                observed(raw, "points", number),
                tuple(ratings),
                observed(raw, "long_description", content),
            )
        )
    return tuple(rows)


class _References(HTMLParser):
    def __init__(self, origin: str, course_id: EntityId) -> None:
        super().__init__(convert_charrefs=True)
        self.origin, self.course_id = origin, course_id
        self.items: list[MaterialReference] = []
        self.truncated = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key not in ("href", "src", "data-api-endpoint") or not value:
                continue
            kind: Literal["file", "assignment", "page", "external", "unsupported"] = "external"
            target = None
            try:
                parts = urlsplit(value)
                same = (
                    not parts.scheme and not parts.netloc and parts.path.startswith("/")
                ) or f"{parts.scheme}://{parts.netloc}" == self.origin
                match = re.fullmatch(
                    r"(?:/api/v1)?/courses/([1-9][0-9]*)/(files|assignments)/([1-9][0-9]*)(?:/download|/preview)?",
                    parts.path,
                )
                if same and match and match[1] == self.course_id:
                    target = entity_id(match[3])
                    kind = "file" if match[2] == "files" else "assignment"
            except (ValueError, MalformedUpstreamError):
                pass
            reference = MaterialReference(
                ExternalText("Embedded reference"), kind, target, "not_fetched"
            )
            if reference not in self.items:
                if len(self.items) >= 100:
                    self.truncated = True
                else:
                    self.items.append(reference)


def references(
    value: object, origin: str, course_id: EntityId
) -> Observed[tuple[MaterialReference, ...]]:
    if value is None:
        return Observed(Availability.AVAILABLE, ())
    if not isinstance(value, str):
        raise MalformedUpstreamError()
    parser = _References(origin, course_id)
    parser.feed(value)
    parser.close()
    return Observed(Availability.AVAILABLE, tuple(parser.items), parser.truncated)


def submission(
    value: object,
    course_id: EntityId,
    assignment_id: EntityId,
    subject: EntityId,
    required: bool | None = None,
) -> Submission:
    raw = _object(value)
    if entity_id(raw.get("user_id")) != subject:
        raise AuthorizationError()
    if entity_id(raw.get("assignment_id")) != assignment_id:
        raise MalformedUpstreamError()
    submitted_at = observed(raw, "submitted_at", timestamp)
    workflow = raw.get("workflow_state")
    workflow_text = observed(raw, "workflow_state", title)
    submission_type = observed(raw, "submission_type", title)
    state = SubmissionState.UNKNOWN
    if submitted_at.value is not None or workflow in ("submitted", "pending_review"):
        state = SubmissionState.SUBMITTED
    elif workflow == "unsubmitted":
        state = SubmissionState.NOT_SUBMITTED
    # Graded alone is NOT evidence of submission (manual/offline/automatic zero).
    graded = (
        True
        if workflow == "graded"
        else False
        if workflow in ("submitted", "pending_review", "unsubmitted")
        else None
    )
    return Submission(
        course_id,
        assignment_id,
        state,
        submitted_at,
        graded,
        flag(raw, "late"),
        flag(raw, "missing"),
        flag(raw, "excused"),
        required,
        subject,
        workflow_text,
        submission_type,
        observed(raw, "attempt", integer),
        observed(raw, "graded_at", timestamp),
        observed(raw, "attachments", attachments),
    )


def assignment(
    value: object, course_id: EntityId, subject: EntityId, origin: str, *, detail: bool = True
) -> Assignment:
    raw = _object(value)
    identity = entity_id(raw.get("id"))
    if entity_id(raw.get("course_id")) != course_id:
        raise MalformedUpstreamError()
    types = tuple(title(item) for item in array(raw.get("submission_types", []), 20))
    kinds = {item.text for item in types}
    supported = {
        "discussion_topic",
        "online_quiz",
        "on_paper",
        "external_tool",
        "online_text_entry",
        "online_url",
        "online_upload",
        "media_recording",
        "student_annotation",
    }
    required = False if kinds == {"none"} else True if kinds and kinds <= supported else None
    current = observed(
        raw, "submission", lambda item: submission(item, course_id, identity, subject, required)
    )
    # Missing embedded submission means unknown, never "not submitted".
    desc = (
        observed(raw, "description", content)
        if detail
        else Observed[ExternalText](Availability.NOT_REQUESTED, None)
    )
    refs = (
        references(raw.get("description"), origin, course_id)
        if detail and "description" in raw
        else Observed[tuple[MaterialReference, ...]](
            Availability.NOT_REQUESTED if not detail else Availability.UNAVAILABLE, None
        )
    )
    rub = (
        observed(raw, "rubric", rubric)
        if detail
        else Observed[tuple[RubricCriterion, ...]](Availability.NOT_REQUESTED, None)
    )
    if detail and "rubric" not in raw:
        rub = Observed(
            Availability.AVAILABLE, None
        )  # Canvas omits rubric for non-rubric assignments.
    return Assignment(
        identity,
        course_id,
        title(raw.get("name")),
        desc,
        observed(raw, "due_at", timestamp),
        observed(raw, "points_possible", number),
        types,
        refs,
        observed(raw, "unlock_at", timestamp),
        observed(raw, "lock_at", timestamp),
        observed(raw, "allowed_attempts", lambda value: integer(value, -1)),
        rub,
        observed(raw, "attachments", attachments),
        current,
        flag(raw, "can_submit"),
        flag(raw, "published"),
        required,
    )


def module(value: object, course_id: EntityId) -> Module:
    raw = _object(value)
    return Module(
        entity_id(raw.get("id")),
        course_id,
        title(raw.get("name")),
        integer(raw["position"], 1) if raw.get("position") is not None else None,
        observed(raw, "unlock_at", timestamp),
        observed(raw, "state", title),
        observed(raw, "completed_at", timestamp),
        integer(raw["items_count"]) if raw.get("items_count") is not None else None,
        flag(raw, "require_sequential_progress"),
    )


def completion(value: object) -> CompletionRequirement:
    raw = _object(value)
    return CompletionRequirement(
        title(raw.get("type")),
        flag(raw, "completed"),
        observed(raw, "min_score", number),
        observed(raw, "min_percentage", number),
    )


def module_item(
    value: object, course_id: EntityId, module_id: EntityId | None = None
) -> ModuleItem:
    raw = _object(value)
    parent = entity_id(raw.get("module_id"))
    if module_id is not None and parent != module_id:
        raise MalformedUpstreamError()
    item_type = raw.get("type")
    kind: Literal["assignment", "file", "page", "external", "other"] = "other"
    if item_type == "Assignment":
        kind = "assignment"
    elif item_type == "File":
        kind = "file"
    elif item_type == "Page":
        kind = "page"
    elif item_type in ("ExternalUrl", "ExternalTool"):
        kind = "external"
    return ModuleItem(
        entity_id(raw.get("id")),
        parent,
        course_id,
        title(raw.get("title")),
        kind,
        entity_id(raw["content_id"]) if raw.get("content_id") is not None else None,
        integer(raw["position"], 1) if raw.get("position") is not None else None,
        observed(raw, "type", title),
        integer(raw["indent"]) if raw.get("indent") is not None else None,
        observed(raw, "completion_requirement", completion),
    )


def module_sequence(
    value: object, course_id: EntityId, assignment_id: EntityId
) -> Observed[tuple[ModuleSequence, ...]]:
    raw = _object(value)
    modules = {
        item.id: item for item in (module(row, course_id) for row in array(raw.get("modules"), 30))
    }
    rows = array(raw.get("items"), 10)
    result = []
    for value in rows:
        row = _object(value)
        current = module_item(row.get("current"), course_id)
        if (
            current.kind != "assignment"
            or current.target_id != assignment_id
            or current.module_id not in modules
        ):
            raise MalformedUpstreamError()
        previous = module_item(row["prev"], course_id) if row.get("prev") is not None else None
        following = module_item(row["next"], course_id) if row.get("next") is not None else None
        result.append(ModuleSequence(modules[current.module_id], current, previous, following))
    # Canvas caps this API at 10 appearances; cannot assert exhaustive at the cap.
    return Observed(Availability.AVAILABLE, tuple(result), len(rows) == 10)


def context_course(raw: dict[str, object], allowed: tuple[EntityId, ...]) -> EntityId:
    context = raw.get("effective_context_code") or raw.get("context_code")
    if not isinstance(context, str) or not context.startswith("course_"):
        raise MalformedUpstreamError()
    course_id = entity_id(context[7:])
    if course_id not in allowed:
        raise AuthorizationError()
    return course_id


def calendar_event(
    value: object, allowed: tuple[EntityId, ...], event_type: Literal["event", "assignment"]
) -> CalendarEvent:
    raw = _object(value)
    assignment_id = None
    identity = raw.get("id")
    if event_type == "assignment":
        if not isinstance(identity, str) or not identity.startswith("assignment_"):
            raise MalformedUpstreamError()
        assignment_id = entity_id(identity[11:])
        key = EntityId("assignment_" + assignment_id)
    else:
        key = entity_id(identity)
    return CalendarEvent(
        key,
        context_course(raw, allowed),
        title(raw.get("title")),
        observed(raw, "start_at", timestamp),
        observed(raw, "end_at", timestamp),
        flag(raw, "all_day"),
        assignment_id,
        observed(raw, "description", content),
        observed(raw, "location_name", content),
        observed(raw, "location_address", content),
        event_type,
    )


def announcement(value: object, allowed: tuple[EntityId, ...]) -> Announcement:
    raw = _object(value)
    return Announcement(
        entity_id(raw.get("id")),
        context_course(raw, allowed),
        title(raw.get("title")),
        content(raw.get("message", "")),
        observed(raw, "posted_at", timestamp),
        observed(raw, "delayed_post_at", timestamp),
        observed(raw, "read_state", title),
    )


def student_course(value: object, course_id: EntityId, subject: EntityId) -> Course:
    raw = _object(value)
    result = course(raw)
    if result.id != course_id:
        raise MalformedUpstreamError()
    enrollments = array(raw.get("enrollments", []))
    student = False
    for value in enrollments:
        enrollment = _object(value)
        if enrollment.get("user_id") is not None and entity_id(enrollment["user_id"]) != subject:
            raise AuthorizationError()
        student |= enrollment.get("type") in ("student", "StudentEnrollment") and enrollment.get(
            "enrollment_state", "active"
        ) in ("active", "completed")
    if not student:
        raise AuthorizationError()
    return result


def course_grade(values: tuple[object, ...], course_id: EntityId, subject: EntityId) -> CourseGrade:
    grades = []
    for value in values:
        row = _object(value)
        if (
            entity_id(row.get("user_id")) != subject
            or entity_id(row.get("course_id")) != course_id
            or row.get("type") != "StudentEnrollment"
        ):
            raise AuthorizationError()
        raw = _object(row["grades"]) if row.get("grades") is not None else {}
        grades.append(
            CourseGrade(
                course_id,
                observed(raw, "current_score", number),
                observed(raw, "current_grade", title),
                observed(raw, "final_score", number),
                observed(raw, "final_grade", title),
                observed(raw, "current_points", number),
            )
        )
    if not grades or any(row != grades[0] for row in grades[1:]):
        unknown: Observed[float] = Observed(Availability.UNAVAILABLE, None)
        unknown_text: Observed[ExternalText] = Observed(Availability.UNAVAILABLE, None)
        return CourseGrade(course_id, unknown, unknown_text, unknown, unknown_text, unknown)
    return grades[0]
