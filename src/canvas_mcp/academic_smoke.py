"""Opt-in count/availability-only academic reads: --live [--course-id ID] [--debug]."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import TypeVar

from canvas_mcp.application.contracts import Result
from canvas_mcp.composition import CanvasConnection, open_canvas_connection
from canvas_mcp.domain.errors import (
    ApplicationError,
    BudgetExceededError,
    NotFoundError,
    RequestBudgetExceededError,
    UpstreamUnavailableError,
)
from canvas_mcp.domain.models import Assignment, Course, EntityId, Page, PageRequest
from canvas_mcp.domain.validation import canvas_id

T = TypeVar("T")


def warnings(response: Result[T], debug: bool) -> None:
    if debug:
        # Only fixed application codes/components; no IDs or content.
        for component, code in sorted({(item.component, item.code) for item in response.warnings}):
            print(f"Metadata warning: {component}/{code}")


def step(name: str, status: str, debug: bool, reason: str | None = None) -> None:
    # All callers supply source-code constants or fixed diagnostic codes only.
    if debug:
        print(f"step: {name:26} {status}" + (f" reason: {reason}" if reason else ""))


async def count(
    fetch: Callable[[PageRequest], Awaitable[Result[Page[T]]]],
    debug: bool,
    first: Result[Page[T]] | None = None,
) -> int:
    total = 0
    page = PageRequest(100)
    for _ in range(20):
        response = first if first is not None else await fetch(page)
        first = None
        warnings(response, debug)
        total += len(response.data.items)
        if response.data.next_cursor is None:
            if not response.data.complete:
                raise BudgetExceededError()
            return total
        page = PageRequest(100, response.data.next_cursor)
    raise BudgetExceededError()


async def locate_assignment(
    connection: CanvasConnection,
    courses: list[EntityId],
    debug: bool,
) -> tuple[EntityId, Result[Page[Assignment]], bool]:
    """At most 50 first-page probes and 60 seconds total; reuse the chosen page."""
    if not courses or len(courses) > 50:
        raise BudgetExceededError()
    first_empty: tuple[EntityId, Result[Page[Assignment]]] | None = None
    failure: ApplicationError | None = None
    try:
        async with asyncio.timeout(60):
            for candidate in courses:
                try:
                    response = await connection.list_assignments(candidate, PageRequest(100))
                except (UpstreamUnavailableError, NotFoundError) as error:
                    failure = failure or error.sanitized()
                    step("locate_test_assignment", "PARTIAL", debug, error.diagnostic_code)
                    continue
                if response.data.items:
                    return candidate, response, failure is None
                if not response.data.complete or response.data.next_cursor is not None:
                    # Do not infer no assignments from a non-exhaustive empty page.
                    raise BudgetExceededError()
                first_empty = first_empty or (candidate, response)
    except TimeoutError:
        failure = RequestBudgetExceededError()
        # The deadline expired, so remaining courses were not checked.
        if first_empty is not None:
            step("locate_test_assignment", "PARTIAL", debug, failure.diagnostic_code)
    if first_empty is not None:
        return *first_empty, failure is None
    assert failure is not None
    raise failure


async def run(course_id: EntityId | None = None, *, debug: bool = False) -> int:
    current_step = "connection"
    try:
        if course_id is not None:
            canvas_id(course_id)
        async with open_canvas_connection() as connection:
            current_step = "profile"
            profile = await connection.get_profile()
            warnings(profile, debug)
            step(current_step, "PASS", debug)
            courses: list[EntityId] = []

            async def course_page(page: PageRequest) -> Result[Page[Course]]:
                response = await connection.list_courses(page)
                courses.extend(item.id for item in response.data.items if item.id not in courses)
                if len(courses) > 50:
                    raise BudgetExceededError()
                return response

            current_step = "list_courses"
            await count(course_page, debug)
            step(current_step, "PASS", debug)
            print("Connected: yes")
            print(f"Courses: {len(courses)}")
            candidates = [course_id] if course_id is not None else courses
            if not candidates:
                print("Academic read smoke: no active courses")
                step("locate_test_assignment", "NOT_TESTED", debug, "no_active_courses")
                return 0
            current_step = "locate_test_assignment"
            selected, first, selection_complete = await locate_assignment(
                connection, candidates, debug
            )
            step(
                current_step,
                "PARTIAL"
                if not selection_complete
                else "PASS"
                if first.data.items
                else "NOT_TESTED",
                debug,
                None if first.data.items else "no_assignments_in_checked_courses",
            )
            current_step = "list_assignments"
            assignments = await count(
                lambda page: connection.list_assignments(selected, page), debug, first
            )
            step(current_step, "PASS", debug)
            current_step = "list_modules"
            modules = await count(lambda page: connection.list_modules(selected, page), debug)
            step(current_step, "PASS", debug)
            print(f"Assignments accessible: yes; count: {assignments}")
            print(f"Modules accessible: yes; count: {modules}")
            # One detailed context read also checks submission, rubric, attachments,
            # and direct module-sequence mapping without printing any of that data.
            if first.data.items:
                current_step = "assignment_context"
                context = await connection.get_assignment_context(selected, first.data.items[0].id)
                warnings(context, debug)
                step(current_step, "PASS" if context.complete else "PARTIAL", debug)
                step("assignment_read", "PASS", debug)
                step(
                    "submission_read",
                    "PASS" if context.data.submission.value is not None else "PARTIAL",
                    debug,
                )
                print(
                    "Submission data accessible: "
                    + ("yes" if context.data.submission.value is not None else "unavailable")
                )
            else:
                print(
                    "Submission data accessible: not tested (no assignments in checked courses"
                    + ("; coverage incomplete)" if not selection_complete else ")")
                )
                step("assignment_read", "NOT_TESTED", debug, "no_assignment")
                step("assignment_context", "NOT_TESTED", debug, "no_assignment")
                step("submission_read", "NOT_TESTED", debug, "no_assignment")
            current_step = "upcoming"
            upcoming = await connection.get_upcoming()
            warnings(upcoming, debug)
            step(current_step, "PASS" if upcoming.complete else "PARTIAL", debug)
            print(
                f"Upcoming items: {len(upcoming.data.items.items)}; complete: {str(upcoming.complete).lower()}"
            )
            now = datetime.now(timezone.utc)
            current_step = "announcements"
            announcements = await count(
                lambda page: connection.list_announcements(
                    now - timedelta(days=14), now, courses=(selected,), page=page
                ),
                debug,
            )
            step(current_step, "PASS", debug)
            current_step = "calendar"
            calendar = await count(
                lambda page: connection.list_calendar_events(
                    now, now + timedelta(days=7), courses=(selected,), page=page
                ),
                debug,
            )
            step(current_step, "PASS", debug)
            print(f"Announcements API: yes; count: {announcements}")
            print(f"Calendar API: yes; count: {calendar}")
            current_step = "grade_availability"
            grade = await connection.get_course_grade(selected)
            warnings(grade, debug)
            available = any(
                item.value is not None
                for item in (
                    grade.data.current_score,
                    grade.data.current_grade,
                    grade.data.final_score,
                    grade.data.final_grade,
                    grade.data.current_points,
                )
            )
            print("Grades API: " + ("available" if available else "unavailable/hidden"))
            step(current_step, "PASS", debug)
            current_step = "connection_close"
        return 0
    except ApplicationError as error:
        step(current_step, "FAIL", debug, error.diagnostic_code)
        print(f"Academic read smoke failed: {error.diagnostic_code}")
        if debug and error.retry_exhausted:
            print("Diagnostic: retry_exhausted")
        return 1
    except Exception:
        step(current_step, "FAIL", debug, "internal_error")
        print("Academic read smoke failed: internal_error")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--course-id")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="step outcomes and fixed diagnostic codes; never private content",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("the optional smoke test requires --live")
    try:
        return asyncio.run(run(args.course_id, debug=args.debug))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
