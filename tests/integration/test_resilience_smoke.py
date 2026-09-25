import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from canvas_mcp import academic_smoke
from canvas_mcp.application.contracts import Result, Warning
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    ConfigurationError,
    NotFoundError,
    UpstreamReason,
    UpstreamUnavailableError,
)
from canvas_mcp.domain.models import Availability, Observed, Page
from conftest import TOKEN, ORIGIN


def result(data, complete=True, warnings=()):
    return Result(data, "opaque", datetime.now(timezone.utc), complete, warnings)


class SmokeConnection:
    def __init__(self, assignment_course="2", error=None):
        self.assignment_course = assignment_course
        self.error = error
        self.calls = []

    async def get_profile(self):
        return result(SimpleNamespace(display_name=SimpleNamespace(text="PRIVATE_STUDENT")))

    async def list_courses(self, page):
        return result(Page(tuple(SimpleNamespace(id=str(i)) for i in range(1, 6)), None, True))

    async def list_assignments(self, course_id, page):
        self.calls.append(("assignments", course_id))
        items = (SimpleNamespace(id="101"),) if course_id == self.assignment_course else ()
        return result(Page(items, None, True))

    async def list_modules(self, course_id, page):
        self.calls.append(("modules", course_id))
        return result(Page((), None, True))

    async def get_assignment_context(self, course_id, assignment_id):
        self.calls.append(("context", course_id, assignment_id))
        return result(SimpleNamespace(submission=Observed(Availability.AVAILABLE, object())))

    async def get_upcoming(self):
        self.calls.append(("upcoming",))
        if self.error:
            raise self.error
        return result(
            SimpleNamespace(items=Page((), None, False)),
            False,
            (Warning("workload", "upstream_timeout", "987654321"),),
        )

    async def list_announcements(self, *args, **kwargs):
        return result(Page((), None, True))

    async def list_calendar_events(self, *args, **kwargs):
        return result(Page((), None, True))

    async def get_course_grade(self, course_id):
        return result(
            SimpleNamespace(
                **{
                    key: Observed(Availability.AVAILABLE, 99)
                    for key in (
                        "current_score",
                        "current_grade",
                        "final_score",
                        "final_grade",
                        "current_points",
                    )
                }
            )
        )


@pytest.mark.parametrize("assignment_course,override", [("2", None), (None, None), ("2", "1")])
def test_smoke_search_context_reuse_no_assignments_and_explicit_override(
    monkeypatch, capsys, assignment_course, override
):
    connection = SmokeConnection(assignment_course)

    @asynccontextmanager
    async def opened():
        yield connection

    monkeypatch.setattr(academic_smoke, "open_canvas_connection", opened)
    assert asyncio.run(academic_smoke.run(override, debug=True)) == 0
    output = capsys.readouterr().out
    if assignment_course and override is None:
        assert connection.calls[:3] == [
            ("assignments", "1"),
            ("assignments", "2"),
            ("modules", "2"),
        ]
        assert ("context", "2", "101") in connection.calls
        assert "Submission data accessible: yes" in output
    else:
        assert not any(call[0] == "context" for call in connection.calls)
        assert "NOT_TESTED" in output and "no assignments in checked courses" in output
        assert len([c for c in connection.calls if c[0] == "assignments"]) == (1 if override else 5)
    assert "upcoming                   PARTIAL" in output
    for private in ("PRIVATE_STUDENT", "987654321", "99", TOKEN, ORIGIN, "Authorization"):
        assert private not in output


@pytest.mark.parametrize(
    "error,code",
    [
        (
            UpstreamUnavailableError(reason=UpstreamReason.TIMEOUT, retry_exhausted=True),
            "upstream_timeout",
        ),
        (
            UpstreamUnavailableError(reason=UpstreamReason.HTTP, http_status=503),
            "upstream_http_503",
        ),
        (RuntimeError(TOKEN + ORIGIN), "internal_error"),
    ],
)
def test_failed_smoke_identifies_actual_step_and_safe_reason(monkeypatch, capsys, error, code):
    connection = SmokeConnection(error=error)

    @asynccontextmanager
    async def opened():
        yield connection

    monkeypatch.setattr(academic_smoke, "open_canvas_connection", opened)
    assert asyncio.run(academic_smoke.run(debug=True)) == 1
    output = capsys.readouterr().out
    assert f"upcoming                   FAIL reason: {code}" in output
    assert f"Academic read smoke failed: {code}" in output
    assert TOKEN not in output and ORIGIN not in output and "Traceback" not in output
    if getattr(error, "retry_exhausted", False):
        assert "Diagnostic: retry_exhausted" in output


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError,
        ConfigurationError,
        AuthorizationError,
        UpstreamUnavailableError,
        NotFoundError,
    ],
)
def test_selection_recovers_only_independent_failures_and_stops_on_fatal(error):
    async def run():
        connection = SmokeConnection()
        original = connection.list_assignments

        async def first_fails(course_id, page):
            if course_id == "1":
                raise error()
            return await original(course_id, page)

        connection.list_assignments = first_fails
        if error in (UpstreamUnavailableError, NotFoundError):
            selected, first, complete = await academic_smoke.locate_assignment(
                connection, ["1", "2"], True
            )
            assert selected == "2" and first.data.items and not complete
        else:
            with pytest.raises(error):
                await academic_smoke.locate_assignment(connection, ["1", "2"], True)
            assert not connection.calls

    asyncio.run(run())


def test_selection_zero_success_and_bounds():
    async def run():
        connection = SmokeConnection(None)
        courses = [str(i) for i in range(1, 51)]
        selected, first, complete = await academic_smoke.locate_assignment(
            connection, courses, False
        )
        assert selected == "1" and not first.data.items and complete
        assert len(connection.calls) == 50
        with pytest.raises(BudgetExceededError):
            await academic_smoke.locate_assignment(connection, [*courses, "51"], False)
        assert len(connection.calls) == 50

        async def failure(*args):
            raise UpstreamUnavailableError(reason=UpstreamReason.CONNECTION)

        connection.list_assignments = failure
        with pytest.raises(UpstreamUnavailableError) as caught:
            await academic_smoke.locate_assignment(connection, courses, False)
        assert caught.value.diagnostic_code == "upstream_connection_error"
        assert caught.value.__context__ is None

    asyncio.run(run())


def test_selection_deadline_reports_partial_not_exhaustive_absence(monkeypatch, capsys):
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: original_timeout(0.001))

    async def run():
        connection = SmokeConnection(None)
        original = connection.list_assignments

        async def slow(course_id, page):
            if course_id == "2":
                await asyncio.sleep(10)
            return await original(course_id, page)

        connection.list_assignments = slow
        selected, first, complete = await academic_smoke.locate_assignment(
            connection, ["1", "2"], True
        )
        assert selected == "1" and not first.data.items and not complete

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "request_budget_exceeded" in output and "upstream_timeout" not in output
