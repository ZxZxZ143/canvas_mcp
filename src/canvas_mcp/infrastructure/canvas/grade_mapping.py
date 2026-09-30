"""Allowlisted current-student grades; never copy hidden or prior-attempt scores."""

from canvas_mcp.domain.errors import AuthorizationError, MalformedUpstreamError
from canvas_mcp.domain.grade_state import GradeFact, NamedGrade
from canvas_mcp.domain.models import Course, EntityId
from canvas_mcp.infrastructure.canvas import academic_mapping as mapping
from canvas_mcp.infrastructure.canvas.mapping import _object, entity_id


def grade(value: object, course: Course, subject: EntityId) -> NamedGrade:
    raw = _object(value)
    assignment_id = entity_id(raw.get("id"))
    if entity_id(raw.get("course_id")) != course.id:
        raise MalformedUpstreamError()
    name = mapping.content(raw.get("name"), 160)
    points = mapping.observed(raw, "points_possible", mapping.number).value
    sub = _object(raw.get("submission"))
    if entity_id(sub.get("user_id")) != subject:
        raise AuthorizationError()
    if entity_id(sub.get("assignment_id")) != assignment_id:
        raise MalformedUpstreamError()
    workflow = sub.get("workflow_state")
    if workflow not in ("graded", "submitted", "pending_review", "unsubmitted"):
        workflow = "unknown"
    attempt = mapping.observed(sub, "attempt", mapping.integer).value
    graded_at = mapping.observed(sub, "graded_at", mapping.timestamp).value
    posted_at = mapping.observed(sub, "posted_at", mapping.timestamp).value
    hidden = (
        mapping.flag(sub, "assignment_visible") is False
        or mapping.flag(sub, "hidden") is True
        or ("posted_at" in sub and posted_at is None)
    )
    matches = mapping.flag(sub, "grade_matches_current_submission")
    prior_attempt = matches is False or (
        matches is None
        and workflow != "graded"
        and (sub.get("score") is not None or sub.get("grade") is not None)
    )
    if matches is not True:
        attempt = None  # Submission attempt is not proven to be the grade's attempt.
    score = display = None
    visibility = "hidden" if hidden else "previous_attempt" if prior_attempt else "ungraded"
    if not hidden and not prior_attempt:
        # Missing visibility evidence is not proof of an exposed grade.
        if "posted_at" not in sub and (
            sub.get("score") is not None or sub.get("grade") is not None
        ):
            raise MalformedUpstreamError()
        score = mapping.observed(sub, "score", mapping.number).value
        text = mapping.observed(sub, "grade", lambda x: mapping.content(x, 64)).value
        if text is not None:
            if text.truncated:
                raise MalformedUpstreamError()
            display = text.text
        if score is not None or display is not None:
            visibility = "visible"
    fact = GradeFact(
        course.id,
        assignment_id,
        attempt,
        score,
        display,
        points,
        graded_at,
        posted_at,
        workflow,  # type: ignore[arg-type]
        visibility,  # type: ignore[arg-type]
    )
    return NamedGrade(fact, mapping.content(course.name.text, 160), name)
