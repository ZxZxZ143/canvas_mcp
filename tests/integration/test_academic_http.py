import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, parse_qsl, urlsplit

import pytest

from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    MalformedUpstreamError,
    NotFoundError,
    RateLimitError,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import AssignmentFilter, PageRequest
from conftest import ORIGIN, PROFILE, TOKEN, response

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def link(path, params, page=2, origin=ORIGIN):
    return (
        "<" + origin + path + "?" + urlencode([*params, ("page", str(page))]) + '>; rel="next"'
    ).encode()


def requests(s):
    return [
        write.split(b"\r\n", 1)[0].decode()
        for write in s.backend.writes
        if write.startswith(b"GET")
    ]


def test_context_has_four_requests_with_first_auth_no_duplicate_submission(
    stack, academic_payloads
):
    async def run():
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response(d["assignment"]),
                response(d["sequence"]),
            ]
        ) as s:
            result = await s.academic.get_assignment_context(s.ctx, "8", "10")
            assert result.data.assignment.id == "10"
            wire = requests(s)
            assert len(wire) == 4
            assert "module_item_sequence" in wire[-1] and "asset_type=Assignment" in wire[-1]
            assert (
                "include%5B%5D=submission" in wire[2]
                and "override_assignment_dates=true" in wire[2]
            )
            assert not any("/submissions/" in value for value in wire)
            assert TOKEN not in repr(result) + s.logs.getvalue()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["assignments", "modules", "items", "announcements", "calendar"])
def test_academic_pagination_uses_shared_scoped_cursors(stack, academic_payloads, kind):
    async def run():
        d = academic_payloads
        path, params, fixture = {
            "assignments": (
                "/api/v1/courses/8/assignments",
                [
                    ("include[]", "submission"),
                    ("override_assignment_dates", "true"),
                    ("order_by", "position"),
                    ("per_page", "25"),
                ],
                "assignment",
            ),
            "modules": ("/api/v1/courses/8/modules", [("per_page", "25")], "module"),
            "items": ("/api/v1/courses/8/modules/20/items", [("per_page", "25")], "module_item"),
            "announcements": (
                "/api/v1/announcements",
                [
                    ("context_codes[]", "course_8"),
                    ("start_date", NOW.isoformat()),
                    ("end_date", (NOW + timedelta(days=7)).isoformat()),
                    ("active_only", "true"),
                    ("per_page", "25"),
                ],
                "announcement",
            ),
            "calendar": (
                "/api/v1/calendar_events",
                [
                    ("context_codes[]", "course_8"),
                    ("start_date", NOW.isoformat()),
                    ("end_date", (NOW + timedelta(days=7)).isoformat()),
                    ("type", "event"),
                    ("excludes[]", "child_events"),
                    ("excludes[]", "assignment"),
                    ("per_page", "25"),
                ],
                "calendar",
            ),
        }[kind]
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response([d[fixture]], headers=[(b"Link", link(path, params))]),
                response([]),
            ]
        ) as s:

            async def fetch(page):
                if kind == "assignments":
                    return await s.academic.list_assignments(s.ctx, "8", page)
                if kind == "modules":
                    return await s.academic.list_modules(s.ctx, "8", page)
                if kind == "items":
                    return await s.academic.list_module_items(s.ctx, "8", "20", page)
                if kind == "announcements":
                    return await s.academic.list_announcements(
                        s.ctx, NOW, NOW + timedelta(days=7), courses=("8",), page=page
                    )
                return await s.academic.list_calendar_events(
                    s.ctx, NOW, NOW + timedelta(days=7), courses=("8",), page=page
                )

            first = await fetch(PageRequest())
            assert first.data.next_cursor and not first.data.complete
            assert "http" not in first.data.next_cursor
            last = await fetch(PageRequest(cursor=first.data.next_cursor))
            assert last.data.complete and s.ctx.budget.pages_used == 2
            assert len(requests(s)) == 4
            with pytest.raises(ValidationError):
                await fetch(PageRequest(cursor=first.data.next_cursor))

    asyncio.run(run())


def test_search_is_encoded_and_bound_into_cursor(stack, academic_payloads):
    async def run():
        params = [
            ("per_page", "25"),
            ("include[]", "submission"),
            ("override_assignment_dates", "true"),
            ("order_by", "due_at"),
            ("search_term", "Лаба & x=1"),
            ("bucket", "upcoming"),
        ]
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response(
                    [d["assignment"]],
                    headers=[(b"Link", link("/api/v1/courses/8/assignments", params))],
                ),
                response([]),
            ]
        ) as s:
            query = AssignmentFilter("Лаба & x=1", "upcoming", "due_at")
            first = await s.academic.list_assignments(s.ctx, "8", query=query)
            cursor = PageRequest(cursor=first.data.next_cursor)
            with pytest.raises(ValidationError):
                await s.academic.list_assignments(s.ctx, "8", cursor, AssignmentFilter())
            await s.academic.list_assignments(s.ctx, "8", cursor, query)
            target = requests(s)[-1].split(" ")[1]
            assert dict(parse_qsl(urlsplit(target).query))["search_term"] == "Лаба & x=1"
            assert "Лаба" not in s.logs.getvalue()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (429, RateLimitError),
        (503, UpstreamUnavailableError),
    ],
)
def test_new_endpoints_reuse_safe_failure_mapping(stack, academic_payloads, status, error):
    async def run():
        async with stack(
            [
                response(PROFILE),
                response(academic_payloads["course"]),
                response({"message": TOKEN}, status),
            ]
        ) as s:
            with pytest.raises(error) as caught:
                await s.academic.get_assignment(s.ctx, "8", "10")
            assert TOKEN not in str(caught.value) + s.logs.getvalue()
            assert caught.value.__context__ is None
            if status == 401:
                with pytest.raises(AuthenticationError):
                    await s.academic.get_course(s.ctx, "8")

    asyncio.run(run())


def test_current_submission_and_grades_requests_are_subject_bound(stack, academic_payloads):
    async def run():
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response(d["submission"]),
                response([d["enrollment"]]),
            ]
        ) as s:
            sub = await s.academic.get_submission(s.ctx, "8", "10")
            grade = await s.academic.get_course_grade(s.ctx, "8")
            assert sub.data.user_id == "7" and grade.data.current_score.value == 90
            wire = requests(s)
            assert "/assignments/10/submissions/self " in wire[2]
            assert "user_id=7" in wire[3] and "type%5B%5D=StudentEnrollment" in wire[3]
            assert "unposted" not in repr(grade)

    asyncio.run(run())


def test_cross_course_workload_request_count_is_per_course_not_assignment(stack, academic_payloads):
    async def run():
        d = academic_payloads
        second_course = {**d["course"], "id": 9}
        second_assignment = {
            **d["assignment"],
            "id": 11,
            "course_id": 9,
            "submission": {**d["submission"], "assignment_id": 11},
        }
        replies = [
            response(PROFILE),
            response([d["course"], second_course]),
            response(d["course"]),
            response([d["assignment"]] * 20),
            response(second_course),
            response([second_assignment] * 20),
        ]
        async with stack(replies) as s:
            result = await s.academic.get_upcoming(replace(s.ctx, as_of=NOW))
            assert len(result.data.items.items) == 2
            assert (
                len(requests(s)) == 6
            )  # profile + discovery + 2*(authorization + assignment page)
            assert not any("/submissions/" in value for value in requests(s))

    asyncio.run(run())


def test_shared_aggregate_budget_does_not_reset(stack, academic_payloads):
    async def run():
        d = academic_payloads
        async with stack([response(PROFILE), response([d["course"]]), response(d["course"])]) as s:
            s.ctx.budget.limits = replace(s.ctx.budget.limits, max_http_attempts=3)
            with pytest.raises(BudgetExceededError):
                await s.academic.get_upcoming(s.ctx)
            assert len(requests(s)) == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "target", ["https://evil.invalid", "http://canvas.example.edu", "https://127.0.0.1"]
)
def test_new_external_pagination_targets_rejected(stack, academic_payloads, target):
    async def run():
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response(
                    [d["module"]],
                    headers=[
                        (
                            b"Link",
                            link("/api/v1/courses/8/modules", [("per_page", "25")], origin=target),
                        )
                    ],
                ),
            ]
        ) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.academic.list_modules(s.ctx, "8")
            assert len(requests(s)) == 3 and all(
                host == "canvas.example.edu" for host, _ in s.backend.targets
            )

    asyncio.run(run())
