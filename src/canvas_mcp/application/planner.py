"""Calendar-aware planner window and transparent evidence; no priorities or estimates."""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from canvas_mcp.application.contracts import WorkloadFeatures
from canvas_mcp.domain.errors import ValidationError
from canvas_mcp.domain.models import Assignment, Availability, ExternalText
from canvas_mcp.domain.validation import instant

# Deliberately bounded prose for planning, not the authoritative drill-down source.
EXCERPT_CHARACTERS = 600
MAX_UPCOMING = 40
MAX_OVERDUE = 30
MAX_UNDATED = 10


def planning_window(now: datetime, days: int, zone: str | None) -> tuple[str, datetime, datetime]:
    if type(days) is not int or not 1 <= days <= 30:
        raise ValidationError()
    name = "UTC" if zone is None else zone
    if not isinstance(name, str) or not 1 <= len(name) <= 64:
        raise ValidationError()
    try:
        tz = ZoneInfo(name)
        start = instant(now)
        local = start.astimezone(tz)
        end = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
        return name, start, end.astimezone(timezone.utc)
    except (ZoneInfoNotFoundError, ValueError, OverflowError):
        raise ValidationError() from None


def features(assignment: Assignment, now: datetime) -> WorkloadFeatures:
    flags: list[str] = []
    desc = assignment.description
    text = desc.value.text if desc.value else ""
    available = bool(text.strip()) if desc.state is Availability.AVAILABLE else None
    truncated = desc.truncated or bool(desc.value and desc.value.truncated)
    excerpt = replace(
        desc,
        value=ExternalText(text[:EXCERPT_CHARACTERS], truncated or len(text) > EXCERPT_CHARACTERS)
        if desc.value
        else None,
        truncated=truncated or len(text) > EXCERPT_CHARACTERS,
    )
    if excerpt.truncated:
        flags.append("description_excerpt_truncated")
    if assignment.description_redacted:
        flags.append("source_text_redacted")
    if assignment.description_nontext_content:
        flags.append("nontext_description_not_inspected")
    if available is not True:
        flags.append("requirements_unknown")
    if assignment.due_at.state is not Availability.AVAILABLE:
        flags.append("deadline_unknown")
    rub = assignment.rubric
    att = assignment.attachments
    refs = assignment.references
    for part in (desc, rub, att, refs):
        if part.state in (Availability.UNAVAILABLE, Availability.NOT_SUPPORTED):
            flags.append("optional_metadata_unavailable")
        if part.truncated:
            flags.append("content_truncated")
    availability = "unknown"
    if assignment.published is False:
        availability = "unpublished"
    elif assignment.lock_at.value is not None and assignment.lock_at.value <= now:
        availability = "closed"
    elif assignment.unlock_at.value is not None and assignment.unlock_at.value > now:
        availability = "not_yet_open"
    elif assignment.can_submit is False:
        availability = "cannot_submit"
    elif assignment.can_submit is True:
        availability = "open"
    else:
        flags.append("availability_unknown")
    if assignment.submission.value is None or assignment.submission.value.state.value == "unknown":
        flags.append("submission_unknown")
    if (
        assignment.submission.value
        and assignment.submission.value.graded is True
        and assignment.submission.value.state.value != "submitted"
    ):
        flags.append("graded_without_submission")
    # Signals name literal description evidence; they do not prove deliverables,
    # required quantities, relevance of files, or workload duration.
    patterns = {
        "quiz": r"\bquiz\b|\bтест\b",
        "report": r"\breport\b|\bотч[её]т\b",
        "programming": r"\b(?:programming|notebook|dataset|implement)\b|\b(?:код|ноутбук)\b",
        "lab": r"\blab(?:oratory)?\b|\bлабораторн\w*",
        "exercises": r"\bexercises?\b|\bзадач\w*",
    }
    signals = tuple(key for key, pattern in patterns.items() if re.search(pattern, text, re.I))
    linked_count = None
    if refs.state is Availability.AVAILABLE:
        linked_count = len(
            {r.target_id for r in refs.value or () if r.kind == "file" and r.target_id}
        )
        if any(r.state != "resolved" for r in refs.value or ()):
            flags.append("references_not_fetched")
    return WorkloadFeatures(
        excerpt,
        available,
        "unknown"
        if available is None
        else "empty"
        if not text
        else "short"
        if len(text) <= 500
        else "medium"
        if len(text) <= 2000
        else "long",
        bool(rub.value) if rub.state is Availability.AVAILABLE else None,
        len(rub.value or ()) if rub.state is Availability.AVAILABLE else None,
        len(att.value or ()) if att.state is Availability.AVAILABLE else None,
        linked_count,
        signals,
        availability,
        tuple(dict.fromkeys(flags)),
    )
