import pytest
from canvas_mcp.domain.errors import AuthorizationError, MalformedUpstreamError
from canvas_mcp.domain.models import Course, ExternalText, Observed, Availability
from canvas_mcp.infrastructure.canvas.grade_mapping import grade

COURSE = Course(
    "8", ExternalText("Course"), ExternalText("C"), Observed(Availability.AVAILABLE, None)
)


def raw(**kwargs):
    return {
        "id": 10,
        "course_id": 8,
        "name": "Homework",
        "points_possible": 100,
        "description": "DO NOT STORE",
        "submission": {
            "user_id": 7,
            "assignment_id": 10,
            "attempt": 1,
            "score": 80,
            "grade": "B",
            "posted_at": "2026-09-30T12:00:00+05:00",
            "graded_at": "2026-09-30T07:00:00Z",
            "workflow_state": "graded",
            "body": "SECRET BODY",
            "grade_matches_current_submission": True,
            **kwargs,
        },
    }


def test_normalized_grade_and_no_coursework_payload():
    item = grade(raw(), COURSE, "7")
    assert item.fact.score == 80 and item.fact.grade == "B"
    assert item.fact.posted_at == item.fact.graded_at
    assert "DO NOT STORE" not in repr(item) and "SECRET BODY" not in repr(item)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"posted_at": None},
        {"assignment_visible": False},
        {"hidden": True},
        {"grade_matches_current_submission": False},
    ],
)
def test_hidden_and_old_attempt_grades_are_not_stored(kwargs):
    fact = grade(raw(**kwargs), COURSE, "7").fact
    assert not fact.exposed and fact.score is None and fact.grade is None


def test_missing_visibility_preserves_course_instead_of_guessing():
    payload = raw()
    del payload["submission"]["posted_at"]
    with pytest.raises(MalformedUpstreamError):
        grade(payload, COURSE, "7")


@pytest.mark.parametrize("kwargs", [{"user_id": 9}, {"assignment_id": 11}])
def test_grade_subject_and_assignment_binding(kwargs):
    with pytest.raises((AuthorizationError, MalformedUpstreamError)):
        grade(raw(**kwargs), COURSE, "7")


@pytest.mark.parametrize(
    "score,display", [(None, "A-"), (None, "complete"), (0, "0"), (90.5, None)]
)
def test_grade_types(score, display):
    fact = grade(raw(score=score, grade=display), COURSE, "7").fact
    assert fact.exposed and fact.score == score and fact.grade == display


def test_hidden_course_totals_do_not_hide_assignment_grade():
    from dataclasses import replace

    assert grade(raw(), replace(COURSE, grades_hidden=True), "7").fact.exposed


@pytest.mark.parametrize("match", [None, "omitted"])
def test_missing_match_does_not_invent_new_attempt_grade(match):
    payload = raw(attempt=2, workflow_state="submitted", grade_matches_current_submission=match)
    if match == "omitted":
        del payload["submission"]["grade_matches_current_submission"]
    fact = grade(payload, COURSE, "7").fact
    assert fact.visibility == "previous_attempt" and not fact.exposed and fact.attempt is None


def test_excused_visible_grade_is_preserved():
    fact = grade(raw(score=None, grade="EX", excused=True), COURSE, "7").fact
    assert fact.exposed and fact.grade == "EX"
