import asyncio
import time
from dataclasses import replace

import pytest

from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    MalformedUpstreamError,
    ValidationError,
)
from canvas_mcp.domain.models import AccessScope, Availability, ConnectionId, PageRequest
from canvas_mcp.infrastructure.canvas import mapping
from conftest import COURSE, PROFILE, next_link, response


def test_allowlisted_mapping_and_optional_absence():
    profile = mapping.profile(
        {"id": "7", "name": "Student", "email": "private", "token": "ignored"}
    )
    assert profile.id == "7" and profile.timezone.state is Availability.UNAVAILABLE
    assert "private" not in repr(profile) and "ignored" not in repr(profile)
    course = mapping.course({"id": 8, "name": "Class", "course_code": "C1"})
    assert course.term.state is Availability.UNAVAILABLE
    assert mapping.course({**COURSE, "term": None}).term.state is Availability.AVAILABLE


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        None,
        {"id": True, "name": "x"},
        {"id": 0, "name": "x"},
        {"id": "../1", "name": "x"},
        {"id": 2**63, "name": "x"},
        {"id": 1, "name": None},
        {"id": 1, "name": "x", "time_zone": "../bad"},
    ],
)
def test_malformed_profiles(payload):
    with pytest.raises(MalformedUpstreamError):
        mapping.profile(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**COURSE, "id": "01"},
        {**COURSE, "name": 123},
        {**COURSE, "course_code": None},
        {**COURSE, "term": []},
        {**COURSE, "term": {}},
    ],
)
def test_malformed_courses(payload):
    with pytest.raises(MalformedUpstreamError):
        mapping.course(payload)


def test_untrusted_content_inert_and_truncation_visible():
    profile = mapping.profile({"id": 1, "name": "<script>evil()</script><b>Student</b>\x1b\x00"})
    assert profile.display_name.text == "Student"
    assert profile.display_name.trust == "untrusted"
    assert mapping.text("x" * 513).truncated


def test_malformed_timezone_does_not_retain_private_exception_chain():
    with pytest.raises(MalformedUpstreamError) as error:
        mapping.profile({**PROFILE, "time_zone": "UNTRUSTED_PRIVATE_COURSE_NAME"})
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_multiple_course_pages_use_opaque_bound_cursors(stack):
    async def run():
        async with stack(
            [
                response(PROFILE),
                response([COURSE], headers=[(b"Link", next_link())]),
                response([{**COURSE, "id": 9}]),
            ]
        ) as s:
            first = await s.app.list_courses(s.ctx)
            cursor = first.data.next_cursor
            assert cursor and "https" not in cursor and not first.complete
            second = await s.app.list_courses(s.ctx, PageRequest(cursor=cursor))
            assert [c.id for c in first.data.items + second.data.items] == ["8", "9"]
            assert second.complete and second.data.next_cursor is None
            assert s.ctx.budget.pages_used == 2
            assert s.ctx.budget.http_attempts_used == 3
            assert b"enrollment_type=student" in b"".join(s.backend.writes)
            with pytest.raises(ValidationError):
                await s.app.list_courses(s.ctx, PageRequest(cursor=cursor))

    asyncio.run(run())


def test_cursor_scope_filter_and_expiry(stack):
    async def run():
        async with stack(
            [response(PROFILE), response([COURSE], headers=[(b"Link", next_link())])]
        ) as s:
            first = await s.provider.list_courses(s.ctx, PageRequest(), True)
            page = PageRequest(cursor=first.next_cursor)
            with pytest.raises(AuthorizationError):
                await s.provider.list_courses(
                    replace(s.ctx, scope=AccessScope(s.scope.principal_id, ConnectionId("other"))),
                    page,
                    True,
                )
            with pytest.raises(ValidationError):
                await s.provider.list_courses(s.ctx, page, False)
            with pytest.raises(ValidationError):
                await s.provider.list_courses(s.ctx, replace(page, limit=10), True)
            key = first.next_cursor
            s.provider._cursors[key] = replace(s.provider._cursors[key], expires=0)
            with pytest.raises(ValidationError):
                await s.provider.list_courses(s.ctx, page, True)

    asyncio.run(run())


@pytest.mark.parametrize(
    "limit_name,limit_value",
    [
        ("max_pages", 1),
        ("max_cursor_state_bytes", 1),
        ("max_session_cursor_bytes", 1),
        ("max_session_cursors", 0),
    ],
)
def test_pagination_bounds_fail_explicitly(stack, limit_name, limit_value):
    async def run():
        async with stack(
            [response(PROFILE), response([COURSE], headers=[(b"Link", next_link())])],
            **{limit_name: limit_value},
        ) as s:
            with pytest.raises(BudgetExceededError):
                await s.provider.list_courses(s.ctx, PageRequest(), True)

    asyncio.run(run())


def test_repeated_pagination_and_subject_change(stack):
    async def run():
        async with stack(
            [
                response(PROFILE),
                response([COURSE], headers=[(b"Link", next_link())]),
                response([COURSE], headers=[(b"Link", next_link())]),
            ]
        ) as s:
            first = await s.provider.list_courses(s.ctx, PageRequest(), True)
            with pytest.raises(MalformedUpstreamError):
                await s.provider.list_courses(s.ctx, PageRequest(cursor=first.next_cursor), True)
        async with stack([response(PROFILE), response({**PROFILE, "id": 99})]) as s:
            await s.provider.get_profile(s.ctx)
            with pytest.raises(AuthenticationError):
                await s.provider.get_profile(s.ctx)
            with pytest.raises(AuthenticationError):
                await s.provider.list_courses(s.ctx, PageRequest(), True)

    asyncio.run(run())


def test_profile_queue_honors_deadline_before_lock_release(stack):
    async def run():
        async with stack([]) as s:
            await s.provider._auth_lock.acquire()
            s.ctx.budget.monotonic_deadline = time.monotonic() + 0.01
            try:
                with pytest.raises(BudgetExceededError):
                    await asyncio.wait_for(s.provider.get_profile(s.ctx), timeout=0.5)
                assert s.provider._auth_lock.locked()
                assert not s.backend.writes
            finally:
                s.provider._auth_lock.release()

    asyncio.run(run())
