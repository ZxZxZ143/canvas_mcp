from copy import deepcopy
from datetime import datetime, timezone

import pytest

from canvas_mcp.domain.errors import AuthorizationError, BudgetExceededError, MalformedUpstreamError
from canvas_mcp.domain.models import Availability, SubmissionState
from canvas_mcp.infrastructure.canvas import academic_mapping as mapping
from conftest import ORIGIN


def test_assignment_full_mapping(academic_payloads):
    item = mapping.assignment(academic_payloads["assignment"], "8", "7", ORIGIN)
    assert item.id == "10" and item.course_id == "8"
    assert item.description.value.format == "plain" and "<p>" not in item.description.value.text
    assert item.due_at.value == datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    assert item.allowed_attempts.value == -1 and item.can_submit
    assert item.rubric.value[0].id.text == "crit1"
    assert item.rubric.value[0].ratings[0].points.value == 10
    assert item.attachments.value[0].filename.value.text == "worksheet.pdf"
    assert (
        item.references.value[0].target_id == "50"
        and item.references.value[0].state == "not_fetched"
    )
    assert item.submission.value.state is SubmissionState.NOT_SUBMITTED
    assert item.required is True
    for private in (
        "DO_NOT_EXPOSE",
        "PRIVATE_GRADE",
        "PRIVATE_COMMENT",
        "PRIVATE_SUBMISSION_BODY",
        "grader_id",
        "html_url",
        "all_dates",
    ):
        assert private not in repr(item)


def test_missing_optional_fields_not_corrupt_identity():
    item = mapping.assignment({"id": 10, "course_id": 8, "name": "Title"}, "8", "7", ORIGIN)
    assert item.rubric.value is None and item.rubric.state is Availability.AVAILABLE
    assert item.description.state is Availability.UNAVAILABLE
    assert item.due_at.state is Availability.UNAVAILABLE
    assert item.submission.value is None and item.submission.state is Availability.UNAVAILABLE
    assert item.required is None


@pytest.mark.parametrize(
    "patch",
    [
        {"id": True},
        {"id": "01"},
        {"course_id": 9},
        {"name": None},
        {"points_possible": float("inf")},
        {"due_at": "2026-01-01T00:00:00"},
        {"allowed_attempts": -2},
        {"submission_types": "upload"},
        {"can_submit": 1},
    ],
)
def test_bad_assignment_fields(academic_payloads, patch):
    with pytest.raises(MalformedUpstreamError):
        mapping.assignment({**academic_payloads["assignment"], **patch}, "8", "7", ORIGIN)


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-01-02T12:00:00Z", "2026-01-02T12:00:00+00:00"),
        ("2026-01-02T12:00:00+05:00", "2026-01-02T07:00:00+00:00"),
        ("2026-01-02T12:00:00-07:00", "2026-01-02T19:00:00+00:00"),
        ("2026-11-01T01:30:00-04:00", "2026-11-01T05:30:00+00:00"),
        ("2026-11-01T01:30:00-05:00", "2026-11-01T06:30:00+00:00"),
    ],
)
def test_timestamp_offsets(stamp, expected):
    assert mapping.timestamp(stamp).isoformat() == expected


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        0,
        "2026-01-01",
        "2026-01-01T12:00:00",
        "invalid",
        "2026-13-01T00:00:00Z",
        "0001-01-01T00:00:00+05:00",
    ],
)
def test_bad_timestamp_is_safe(stamp):
    with pytest.raises(MalformedUpstreamError) as error:
        mapping.timestamp(stamp)
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "workflow,submitted,expected,graded",
    [
        ("unsubmitted", None, SubmissionState.NOT_SUBMITTED, False),
        ("submitted", None, SubmissionState.SUBMITTED, False),
        ("pending_review", None, SubmissionState.SUBMITTED, False),
        ("graded", None, SubmissionState.UNKNOWN, True),
        ("graded", "2026-01-01T12:00:00Z", SubmissionState.SUBMITTED, True),
        ("future_status", None, SubmissionState.UNKNOWN, None),
    ],
)
def test_submission_independent_facts(academic_payloads, workflow, submitted, expected, graded):
    raw = {
        **academic_payloads["submission"],
        "workflow_state": workflow,
        "submitted_at": submitted,
        "late": True,
        "missing": True,
        "excused": True,
    }
    item = mapping.submission(raw, "8", "10", "7", True)
    assert item.state is expected and item.graded is graded
    assert item.late and item.missing and item.excused
    assert item.user_id == "7" and "score" not in item.__dataclass_fields__


@pytest.mark.parametrize(
    "types,required",
    [
        (["none"], False),
        (["on_paper"], True),
        (["online_upload"], True),
        ([], None),
        (["new_unknown_type"], None),
    ],
)
def test_offline_required_is_not_inferred_from_upload(academic_payloads, types, required):
    item = mapping.assignment(
        {**academic_payloads["assignment"], "submission_types": types}, "8", "7", ORIGIN
    )
    assert item.required is required and item.submission.value.required is required


def test_other_student_and_wrong_assignment_rejected(academic_payloads):
    with pytest.raises(AuthorizationError):
        mapping.submission({**academic_payloads["submission"], "user_id": 99}, "8", "10", "7")
    with pytest.raises(MalformedUpstreamError):
        mapping.submission(academic_payloads["submission"], "8", "11", "7")


def test_modules_sequences_calendar_announcements_grades(academic_payloads):
    data = academic_payloads
    mod = mapping.module(data["module"], "8")
    assert mod.items_count == 1 and mod.require_sequential_progress
    item = mapping.module_item(data["module_item"], "8", "20")
    assert item.kind == "assignment" and item.completion_requirement.value.completed is False
    seq = mapping.module_sequence(data["sequence"], "8", "10")
    assert seq.value[0].module.id == "20" and seq.value[0].current_item.target_id == "10"
    event = mapping.calendar_event(data["calendar"], ("8",), "event")
    assert event.description.value.text == "Bring notes" and "PRIVATE_OTHER_USER" not in repr(event)
    notice = mapping.announcement(data["announcement"], ("8",))
    assert notice.body.trust == "untrusted" and "PRIVATE_AUTHOR" not in repr(notice)
    grade = mapping.course_grade((data["enrollment"],), "8", "7")
    assert grade.current_score.value == 90 and "unposted" not in repr(grade)


def test_grade_absence_conflicts_and_scope(academic_payloads):
    row = academic_payloads["enrollment"]
    assert (
        mapping.course_grade(({**row, "grades": {}},), "8", "7").current_score.state
        is Availability.UNAVAILABLE
    )
    assert mapping.course_grade(({**row, "grades": None},), "8", "7").current_score.value is None
    assert mapping.course_grade((), "8", "7").current_score.value is None
    assert (
        mapping.course_grade(
            (row, {**row, "grades": {"current_score": 99}}), "8", "7"
        ).current_score.state
        is Availability.UNAVAILABLE
    )
    for patch in ({"user_id": 99}, {"course_id": 99}, {"type": "TeacherEnrollment"}):
        with pytest.raises(AuthorizationError):
            mapping.course_grade(({**row, **patch},), "8", "7")


@pytest.mark.parametrize(
    "patch",
    [
        {"enrollments": []},
        {"enrollments": [{"type": "teacher"}]},
        {"enrollments": [{"type": "student", "user_id": 99}]},
    ],
)
def test_student_course_membership_required(academic_payloads, patch):
    with pytest.raises(AuthorizationError):
        mapping.student_course({**academic_payloads["course"], **patch}, "8", "7")


def test_mapping_bounds_and_no_silent_clipping(academic_payloads):
    raw = deepcopy(academic_payloads["assignment"])
    raw["rubric"] *= 101
    with pytest.raises(BudgetExceededError):
        mapping.assignment(raw, "8", "7", ORIGIN)
    raw = {**academic_payloads["assignment"], "description": "x" * 16001}
    assert mapping.assignment(raw, "8", "7", ORIGIN).description.value.truncated
    sequence = {
        **academic_payloads["sequence"],
        "items": academic_payloads["sequence"]["items"] * 10,
    }
    assert mapping.module_sequence(sequence, "8", "10").truncated


def test_calendar_assignment_and_wrong_context(academic_payloads):
    raw = {**academic_payloads["calendar"], "id": "assignment_10"}
    assert mapping.calendar_event(raw, ("8",), "assignment").assignment_id == "10"
    with pytest.raises(AuthorizationError):
        mapping.announcement(academic_payloads["announcement"], ("9",))
    with pytest.raises(MalformedUpstreamError):
        mapping.module_item(academic_payloads["module_item"], "8", "99")
    with pytest.raises(MalformedUpstreamError):
        mapping.module_sequence(academic_payloads["sequence"], "8", "99")


@pytest.mark.parametrize(
    "item_type,kind",
    [
        ("File", "file"),
        ("Page", "page"),
        ("Discussion", "other"),
        ("Assignment", "assignment"),
        ("Quiz", "other"),
        ("SubHeader", "other"),
        ("ExternalUrl", "external"),
        ("ExternalTool", "external"),
    ],
)
def test_module_types_preserve_upstream_kind_without_following_urls(
    academic_payloads, item_type, kind
):
    raw = {**academic_payloads["module_item"], "type": item_type}
    item = mapping.module_item(raw, "8", "20")
    assert item.kind == kind and item.item_type.value.text == item_type
    assert "https://" not in repr(item)


def test_sequence_neighbors_can_cross_modules(academic_payloads):
    data = deepcopy(academic_payloads["sequence"])
    data["modules"].append({"id": 22, "name": "Adjacent module"})
    data["items"][0]["next"] = {"id": 23, "module_id": 22, "type": "Page", "title": "Read next"}
    result = mapping.module_sequence(data, "8", "10")
    assert result.value[0].module.id == "20" and result.value[0].next_item.module_id == "22"
