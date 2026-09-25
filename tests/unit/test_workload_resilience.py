import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    ConfigurationError,
    MalformedUpstreamError,
    NotFoundError,
    RateLimitError,
    RequestBudgetExceededError,
    UpstreamReason,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    Assignment,
    Availability,
    Course,
    ExternalText,
    Observed,
    Page,
    Submission,
    SubmissionState,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
KNOWN_NONE = Observed(Availability.AVAILABLE, None)


class IndependentCourses:
    def __init__(self, *, overdue=False, failures=None, fail_at="assignments"):
        self.courses = tuple(
            Course(str(i), ExternalText("PRIVATE_COURSE"), ExternalText("PRIVATE_CODE"), KNOWN_NONE)
            for i in range(1, 6)
        )
        self.calls = []
        self.failures = failures or {}
        self.fail_at = fail_at
        self.empty = set()
        self.paginated_failure = False
        due = NOW + timedelta(days=-1 if overdue else 1)
        sub = Submission(
            "1",
            "101",
            SubmissionState.NOT_SUBMITTED,
            KNOWN_NONE,
            False,
            False,
            False,
            False,
            True,
            workflow_state=KNOWN_NONE,
            submission_type=KNOWN_NONE,
            attempt=KNOWN_NONE,
            graded_at=KNOWN_NONE,
            attachments=KNOWN_NONE,
        )
        self.assignment = Assignment(
            "101",
            "1",
            ExternalText("PRIVATE_ASSIGNMENT"),
            KNOWN_NONE,
            Observed(Availability.AVAILABLE, due),
            KNOWN_NONE,
            (),
            KNOWN_NONE,
            unlock_at=KNOWN_NONE,
            lock_at=KNOWN_NONE,
            allowed_attempts=KNOWN_NONE,
            rubric=KNOWN_NONE,
            attachments=KNOWN_NONE,
            submission=Observed(Availability.AVAILABLE, sub),
            required=True,
        )

    async def list_courses(self, ctx, page, active_only):
        self.calls.append(("discovery", None))
        if self.fail_at == "discovery":
            raise UpstreamUnavailableError()
        return Page(self.courses, None, True)

    async def get_course(self, ctx, course_id):
        self.calls.append(("course", course_id))
        if self.fail_at == "course" and course_id in self.failures:
            raise self.failures[course_id]
        return next(c for c in self.courses if c.id == course_id)

    async def list_assignments(self, ctx, course_id, page, query=None):
        self.calls.append(("assignments", course_id))
        if self.fail_at == "assignments" and course_id in self.failures:
            if not self.paginated_failure or page.cursor is not None:
                raise self.failures[course_id]
        sub = replace(self.assignment.submission.value, course_id=course_id)
        item = replace(
            self.assignment,
            course_id=course_id,
            submission=Observed(Availability.AVAILABLE, sub),
        )
        cursor = "opaque" if self.paginated_failure and course_id in self.failures else None
        return Page(() if course_id in self.empty else (item,), cursor, cursor is None)


@pytest.mark.parametrize("overdue", [False, True])
@pytest.mark.parametrize("failed", [(), ("3",), ("1", "2", "3", "4", "5")])
def test_full_partial_and_zero_authoritative_sources(stack, overdue, failed):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(
                overdue=overdue,
                failures={
                    i: UpstreamUnavailableError(reason=UpstreamReason.TIMEOUT) for i in failed
                },
            )
            service = AcademicService(fake)
            fetch = service.get_overdue if overdue else service.get_upcoming
            if len(failed) == 5:
                with pytest.raises(UpstreamUnavailableError) as caught:
                    await fetch(replace(s.ctx, as_of=NOW))
                assert caught.value.diagnostic_code == "upstream_timeout"
                assert caught.value.__context__ is None
            else:
                result = await fetch(replace(s.ctx, as_of=NOW))
                expected = tuple(c.id for c in fake.courses if c.id not in failed)
                assert tuple(x.course.id for x in result.data.items.items) == expected
                assert result.data.coverage.scanned == expected
                assert result.data.coverage.failed == failed
                assert result.complete is (not failed)
                assert result.data.items.complete is (not failed)
                assert len(result.warnings) == len(failed)
                assert all(w.code == "upstream_timeout" for w in result.warnings)
                assert "PRIVATE" not in repr(result.warnings)
            assert fake.calls == [("discovery", None)] + [
                ("assignments", str(i)) for i in range(1, 6)
            ]  # no aggregate retry and no submission calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError,
        ConfigurationError,
        AuthorizationError,
        ValidationError,
        MalformedUpstreamError,
        BudgetExceededError,
        RateLimitError,
        RuntimeError,
    ],
)
@pytest.mark.parametrize("overdue", [False, True])
def test_fatal_errors_abort_immediately_even_after_success(stack, error, overdue):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(overdue=overdue, failures={"3": error()})
            service = AcademicService(fake)
            fetch = service.get_overdue if overdue else service.get_upcoming
            with pytest.raises(error):
                await fetch(replace(s.ctx, as_of=NOW))
            assert fake.calls[-1] == ("assignments", "3")
            assert len(fake.calls) == 4

    asyncio.run(run())


@pytest.mark.parametrize("fail_at", ["course", "assignments"])
@pytest.mark.parametrize("error", [NotFoundError, UpstreamUnavailableError, AuthorizationError])
def test_explicit_course_permission_scope_and_direct_semantics(stack, fail_at, error):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(fail_at=fail_at, failures={"3": error()})
            service = AcademicService(fake)
            if error is AuthorizationError:
                with pytest.raises(error):
                    await service.get_upcoming(replace(s.ctx, as_of=NOW), courses=("1", "3", "5"))
                assert not any(identity == "5" for _, identity in fake.calls)
            else:
                result = await service.get_upcoming(
                    replace(s.ctx, as_of=NOW), courses=("1", "3", "5")
                )
                assert result.data.coverage.scanned == ("1", "5")
                assert result.data.coverage.failed == ("3",)
                assert not result.complete
            with pytest.raises(error):
                if fail_at == "course":
                    await service.get_course(s.ctx, "3")
                else:
                    await service.list_assignments(s.ctx, "3")

    asyncio.run(run())


def test_failed_later_page_discards_earlier_pages_from_that_course_only(stack):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(failures={"3": UpstreamUnavailableError()})
            fake.paginated_failure = True
            result = await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))
            assert len(result.data.items.items) == 4
            assert result.data.coverage.failed == ("3",)
            assert fake.calls.count(("assignments", "3")) == 2
            assert len(result.warnings) == 1

    asyncio.run(run())


def test_empty_success_is_authoritative_but_discovery_failure_is_fatal(stack):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(
                failures={str(i): UpstreamUnavailableError() for i in range(2, 6)}
            )
            fake.empty = {"1"}
            result = await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))
            assert not result.data.items.items and not result.complete
            assert result.data.coverage.scanned == ("1",)
            fake.courses = ()
            result = await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))
            assert result.complete and not result.data.items.items
            fake.fail_at = "discovery"
            with pytest.raises(UpstreamUnavailableError):
                await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))

    asyncio.run(run())


@pytest.mark.parametrize("at", ["1", "3"])
def test_only_shared_budget_exhaustion_preserves_completed_courses_and_stops(stack, at):
    async def run():
        async with stack([]) as s:
            fake = IndependentCourses(failures={at: RequestBudgetExceededError()})
            if at == "1":
                with pytest.raises(RequestBudgetExceededError):
                    await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))
            else:
                result = await AcademicService(fake).get_upcoming(replace(s.ctx, as_of=NOW))
                assert result.data.coverage.scanned == ("1", "2")
                assert result.data.coverage.failed == ("3", "4", "5")
                assert not result.complete and len(result.warnings) == 1
            assert fake.calls[-1] == ("assignments", at)

    asyncio.run(run())
