import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    MalformedUpstreamError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    AssignmentFilter,
    Availability,
    Observed,
    Page,
    SubmissionState,
)
from canvas_mcp.infrastructure.canvas import academic_mapping as mapping
from conftest import ORIGIN

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


class FakeAcademic:
    def __init__(self, data):
        self.course = mapping.student_course(data["course"], "8", "7")
        self.assignment = mapping.assignment(data["assignment"], "8", "7", ORIGIN)
        self.assignments = {"8": [self.assignment]}
        self.courses = (self.course,)
        self.sequence = mapping.module_sequence(data["sequence"], "8", "10")
        self.grade = mapping.course_grade((), "8", "7")
        self.calls = []
        self.module_error = None
        self.assignment_error = None

    async def get_course(self, ctx, course_id):
        self.calls.append("course")
        return self.course

    async def get_assignment(self, ctx, course_id, assignment_id):
        self.calls.append("assignment")
        if self.assignment_error:
            raise self.assignment_error()
        return self.assignment

    async def get_submission(self, ctx, course_id, assignment_id):
        self.calls.append("submission")
        return replace(self.assignments["8"][0].submission.value, required=None)

    async def get_assignment_module_context(self, ctx, course_id, assignment_id):
        self.calls.append("sequence")
        if self.module_error:
            raise self.module_error()
        return self.sequence

    async def list_courses(self, ctx, page, active_only):
        self.calls.append(("courses", active_only))
        return Page(self.courses, None, True)

    async def list_assignments(self, ctx, course_id, page, query=AssignmentFilter()):
        self.calls.append(("assignments", course_id, query))
        return Page(tuple(self.assignments[course_id]), None, True)

    async def get_course_grade(self, ctx, course_id):
        self.calls.append("grade")
        return self.grade

    async def list_calendar_events(self, ctx, courses, start, end, page, event_type="event"):
        self.calls.append(("calendar", courses, start, end))
        return self.events

    async def list_announcements(self, ctx, courses, start, end, page):
        self.calls.append(("announcements", courses, start, end))
        return self.notices


def test_assignment_context_no_duplicate_submission_or_rubric(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            result = await AcademicService(fake).get_assignment_context(s.ctx, "8", "10")
            assert result.data.assignment.id == "10" and result.data.submission.value is not None
            assert fake.calls == ["course", "assignment", "sequence"]
            assert not s.backend.writes

    asyncio.run(run())


@pytest.mark.parametrize("error", [NotFoundError, UpstreamUnavailableError])
def test_partial_module_failure_and_submission_fallback(stack, academic_payloads, error):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            fake.assignment = replace(
                fake.assignment, submission=Observed(Availability.UNAVAILABLE, None)
            )
            fake.module_error = error
            result = await AcademicService(fake).get_assignment_context(s.ctx, "8", "10")
            assert result.data.module_context.state is Availability.UNAVAILABLE
            assert result.data.submission.value is not None and not result.complete
            assert fake.calls.count("submission") == 1
            assert any(
                w.component == "module_context" and w.code == error.code for w in result.warnings
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "error", [AuthenticationError, AuthorizationError, MalformedUpstreamError, BudgetExceededError]
)
def test_context_must_not_swallow_security_or_schema_failures(stack, academic_payloads, error):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            fake.module_error = error
            with pytest.raises(error):
                await AcademicService(fake).get_assignment_context(s.ctx, "8", "10")

    asyncio.run(run())


def test_primary_assignment_failure_always_fails(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            fake.assignment_error = NotFoundError
            with pytest.raises(NotFoundError):
                await AcademicService(fake).get_assignment_context(s.ctx, "8", "10")
            assert "sequence" not in fake.calls

    asyncio.run(run())


def test_upcoming_cross_course_boundaries_dedupe_status_no_n_plus_one(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, as_of=NOW)
            fake = FakeAcademic(academic_payloads)
            original = fake.assignment

            def item(identity, due, **flags):
                sub = replace(original.submission.value, **flags)
                return replace(
                    original,
                    id=str(identity),
                    due_at=Observed(Availability.AVAILABLE, due),
                    submission=Observed(Availability.AVAILABLE, sub),
                )

            exact_start = item(11, NOW)
            fake.assignments["8"] = [
                item(12, NOW + timedelta(days=7)),
                exact_start,
                exact_start,
                item(13, None),
                item(14, NOW - timedelta(seconds=1)),
                item(15, NOW + timedelta(days=1), state=SubmissionState.SUBMITTED),
                item(16, NOW + timedelta(days=1), state=SubmissionState.UNKNOWN),
            ]
            second = replace(fake.course, id="9")
            fake.courses = (second, fake.course)
            fake.assignments["9"] = [replace(item(17, NOW), course_id="9")]
            result = await AcademicService(fake).get_upcoming(ctx)
            assert [(x.course.id, x.assignment.id) for x in result.data.items.items] == [
                ("8", "11"),
                ("9", "17"),
                ("8", "16"),
            ]
            assert result.data.coverage.scanned == ("8", "9")
            assert not result.complete and any(
                w.code == "submission_unknown" for w in result.warnings
            )
            assert len(fake.calls) == 3 and "submission" not in fake.calls
            assert not s.backend.writes

    asyncio.run(run())


def test_overdue_is_not_merely_past_due(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, as_of=NOW)
            fake = FakeAcademic(academic_payloads)
            base = replace(
                fake.assignment, due_at=Observed(Availability.AVAILABLE, NOW - timedelta(days=1))
            )
            variants = [
                ({}, True),
                ({"state": SubmissionState.SUBMITTED, "late": True}, False),
                ({"excused": True}, False),
                ({"graded": True}, False),
                ({"required": False}, False),
                ({"state": SubmissionState.UNKNOWN}, False),
                ({"excused": None}, False),
            ]
            fake.assignments["8"] = [
                replace(
                    base,
                    id=str(i + 10),
                    submission=Observed(
                        Availability.AVAILABLE, replace(base.submission.value, **patch)
                    ),
                )
                for i, (patch, _) in enumerate(variants)
            ]
            result = await AcademicService(fake).get_overdue(ctx)
            assert [x.assignment.id for x in result.data.items.items] == ["10"]
            assert result.data.items.items[0].submission.value.missing is False
            assert not result.complete

    asyncio.run(run())


def test_hidden_grade_returns_unknown_not_zero(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            result = await AcademicService(FakeAcademic(academic_payloads)).get_course_grade(
                s.ctx, "8"
            )
            assert result.data.current_score.value is None and not result.complete

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"days": 0},
        {"days": 91},
        {"days": True},
        {"start_at": NOW},
        {"start_at": NOW, "end_at": NOW},
        {"start_at": NOW.replace(tzinfo=None), "end_at": NOW + timedelta(days=1)},
        {"start_at": NOW, "end_at": NOW - timedelta(days=1)},
        {"start_at": NOW, "end_at": NOW + timedelta(days=91)},
        {"include_overdue": "yes"},
        {"include_overdue": True, "days": 46},
        {"courses": ("../8",)},
        {"courses": tuple(str(i + 1) for i in range(51))},
    ],
)
def test_bad_workload_input_before_provider(stack, academic_payloads, kwargs):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            with pytest.raises(ValidationError):
                await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW), **kwargs)
            assert not fake.calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "identity",
    ["0", "01", "../8", "https://evil.invalid", 8, True, "8/assignments", "9223372036854775808"],
)
def test_bad_ids_before_provider(stack, academic_payloads, identity):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            with pytest.raises(ValidationError):
                await AcademicService(fake).get_assignment(s.ctx, identity, "10")
            assert not fake.calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "query",
    [
        AssignmentFilter(bucket="wrong"),
        AssignmentFilter(order_by="random"),
        AssignmentFilter(search_term=""),
        AssignmentFilter(search_term="a" * 101),
        AssignmentFilter(search_term="line\nfeed"),
    ],
)
def test_assignment_filter_validation(stack, academic_payloads, query):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            with pytest.raises(ValidationError):
                await AcademicService(fake).list_assignments(s.ctx, "8", query=query)
            assert not fake.calls

    asyncio.run(run())


def test_calendar_and_announcement_end_exclusive_and_defaults(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            fake = FakeAcademic(academic_payloads)
            event = mapping.calendar_event(academic_payloads["calendar"], ("8",), "event")
            notice = mapping.announcement(academic_payloads["announcement"], ("8",))
            fake.events = Page(
                (
                    replace(event, starts_at=Observed(Availability.AVAILABLE, NOW)),
                    replace(
                        event, starts_at=Observed(Availability.AVAILABLE, NOW + timedelta(days=1))
                    ),
                ),
                None,
                True,
            )
            fake.notices = Page(
                (replace(notice, published_at=Observed(Availability.AVAILABLE, NOW)),), None, True
            )
            service = AcademicService(fake)
            assert (
                len(
                    (
                        await service.list_calendar_events(
                            s.ctx, NOW, NOW + timedelta(days=1), courses=("8",)
                        )
                    ).data.items
                )
                == 1
            )
            assert (
                len(
                    (
                        await service.list_announcements(s.ctx, NOW, NOW + timedelta(days=1))
                    ).data.items
                )
                == 1
            )
            assert ("courses", True) in fake.calls

    asyncio.run(run())


def test_result_size_limit_applies_to_context(stack, academic_payloads):
    async def run():
        async with stack([]) as s:
            s.ctx.budget.limits = replace(s.ctx.budget.limits, max_response_bytes=100)
            with pytest.raises(BudgetExceededError):
                await AcademicService(FakeAcademic(academic_payloads)).get_assignment_context(
                    s.ctx, "8", "10"
                )

    asyncio.run(run())
