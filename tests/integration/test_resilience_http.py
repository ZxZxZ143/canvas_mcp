import asyncio
import io
import json
import time
from datetime import datetime, timezone

import httpcore
import pytest

from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    MalformedUpstreamError,
    NotFoundError,
    UpstreamReason,
    UpstreamUnavailableError,
)
from canvas_mcp.domain.models import AssignmentFilter
from canvas_mcp.infrastructure.logging.events import Event, EventLogger
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from conftest import ORIGIN, PROFILE, TOKEN, response


def wire_requests(s):
    return [w for w in s.backend.writes if w.startswith(b"GET")]


@pytest.mark.parametrize(
    "path,label",
    [
        ("/api/v1/users/self", "profile"),
        ("/api/v1/courses", "courses"),
        ("/api/v1/courses/987654321", "courses.get"),
        ("/api/v1/courses/987654321/assignments", "assignments.list"),
        ("/api/v1/courses/987654321/assignments/123456789", "assignments.get"),
        ("/api/v1/courses/987654321/assignments/123456789/submissions/self", "submission.get"),
        ("/api/v1/courses/987654321/modules", "modules.list"),
        ("/api/v1/courses/987654321/modules/123456789/items", "modules.items"),
        ("/api/v1/courses/987654321/module_item_sequence", "module_sequence.get"),
        ("/api/v1/courses/987654321/enrollments", "grades.get"),
        ("/api/v1/calendar_events", "calendar.list"),
        ("/api/v1/announcements", "announcements.list"),
    ],
)
def test_every_route_has_safe_type_label_and_structured_503(stack, path, label):
    async def run():
        async with stack([response({"error": TOKEN + ORIGIN}, 503)]) as s:
            with pytest.raises(UpstreamUnavailableError) as caught:
                await s.client._get(s.ctx, path, (("search_term", "PRIVATE_SEARCH"),))
            error = caught.value
            assert error.reason is UpstreamReason.HTTP and error.http_status == 503
            assert error.diagnostic_code == "upstream_http_503" and error.retry_exhausted
            assert error.__context__ is None and error.__cause__ is None
            events = [json.loads(line) for line in s.logs.getvalue().splitlines()]
            assert {event["operation"] for event in events} == {label}
            assert events[-1]["reason"] == "upstream_http_503"
            assert events[-1]["retry_exhausted"] is True
            for private in (
                TOKEN,
                ORIGIN,
                "987654321",
                "123456789",
                "PRIVATE_SEARCH",
                "Authorization",
            ):
                assert private not in s.logs.getvalue() + str(error) + repr(error)

    asyncio.run(run())


@pytest.mark.parametrize(
    "exception,code,attempts",
    [
        (httpcore.ConnectTimeout, "upstream_timeout", 3),
        (httpcore.ReadTimeout, "upstream_timeout", 3),
        (TimeoutError, "upstream_timeout", 3),
        (httpcore.ConnectError, "upstream_connection_error", 3),
        (httpcore.ReadError, "upstream_connection_error", 3),
        (OSError, "upstream_connection_error", 3),
        (httpcore.RemoteProtocolError, "malformed_upstream", 1),
        (RuntimeError, "internal_error", 1),
    ],
)
def test_transport_reason_and_attempts_never_serialize_exception(
    stack, monkeypatch, exception, code, attempts
):
    async def run():
        async def no_sleep(delay):
            pass

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        async with stack([], max_get_attempts=3) as s:

            async def fail(*args, **kwargs):
                raise exception(TOKEN + ORIGIN + "PRIVATE_CONTENT")

            monkeypatch.setattr(s.backend, "connect_tcp", fail)
            with pytest.raises(Exception) as caught:
                await s.client.get_profile(s.ctx)
            assert caught.value.diagnostic_code == code
            assert caught.value.retry_exhausted is (attempts == 3)
            assert s.ctx.budget.http_attempts_used == attempts
            assert caught.value.__context__ is None
            log = s.logs.getvalue()
            assert json.loads(log.splitlines()[-1])["reason"] == code
            assert TOKEN not in log + repr(caught.value)
            assert ORIGIN not in log and "PRIVATE_CONTENT" not in log

    asyncio.run(run())


@pytest.mark.parametrize(
    "exception",
    [OSError, TimeoutError, UpstreamUnavailableError, ConfigurationError, AuthenticationError],
)
def test_credential_failures_are_fatal_and_never_retried(stack, monkeypatch, exception):
    async def run():
        async with stack([], max_get_attempts=3) as s:

            async def fail(self, scope):
                raise exception()

            monkeypatch.setattr(type(s.creds), "get_access_token", fail)
            expected = (
                AuthenticationError if exception is AuthenticationError else ConfigurationError
            )
            with pytest.raises(expected) as caught:
                await s.client.get_profile(s.ctx)
            assert caught.value.__context__ is None
            assert s.ctx.budget.http_attempts_used == 1 and not s.backend.targets

    asyncio.run(run())


def test_logger_rejects_untrusted_fields():
    stream = io.StringIO()
    log = EventLogger(stream=stream)
    log.emit(Event.FAILED, TOKEN, "assignments.list", reason=TOKEN)
    log.emit(Event.FAILED, TOKEN, TOKEN, reason=TOKEN)
    log.emit(Event.FAILED, TOKEN, "assignments.list", attempt=TOKEN)
    event = json.loads(stream.getvalue())
    assert event["request_id"] == "invalid" and event["reason"] == "internal_error"
    assert TOKEN not in stream.getvalue()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reason": TOKEN},
        {"reason": UpstreamReason.HTTP, "http_status": TOKEN},
        {"reason": UpstreamReason.HTTP, "http_status": 404},
        {"reason": UpstreamReason.TIMEOUT, "http_status": 503},
        {"retry_exhausted": TOKEN},
    ],
)
def test_failure_details_accept_only_closed_safe_values(kwargs):
    with pytest.raises(TypeError):
        UpstreamUnavailableError(**kwargs)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (503, UpstreamUnavailableError),
    ],
)
def test_direct_assignment_list_still_raises(stack, academic_payloads, status, error):
    async def run():
        async with stack(
            [
                response(PROFILE),
                response(academic_payloads["course"]),
                response({"message": TOKEN}, status),
            ]
        ) as s:
            with pytest.raises(error):
                await s.academic.list_assignments(
                    s.ctx, "8", query=AssignmentFilter(search_term="PRIVATE_SEARCH")
                )
            assert len(wire_requests(s)) == 3
            assert "PRIVATE_SEARCH" not in s.logs.getvalue()

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 404, 503])
@pytest.mark.parametrize("kind", ["calendar", "announcements"])
def test_batched_multicourse_endpoint_failure_cannot_be_attributed_to_one_course(
    stack, academic_payloads, status, kind
):
    async def run():
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response(d["course"]),
                response({**d["course"], "id": 9}),
                response({}, status),
            ]
        ) as s:
            fetch = (
                s.academic.list_calendar_events
                if kind == "calendar"
                else s.academic.list_announcements
            )
            expected = {403: AuthorizationError, 404: NotFoundError, 503: UpstreamUnavailableError}[
                status
            ]
            with pytest.raises(expected):
                await fetch(
                    s.ctx,
                    datetime(2026, 10, 1, tzinfo=timezone.utc),
                    datetime(2026, 10, 2, tzinfo=timezone.utc),
                    courses=("8", "9"),
                )
            assert len(wire_requests(s)) == 4

    asyncio.run(run())


@pytest.mark.parametrize("size,retry_each", [(5, False), (5, True), (20, False), (20, True)])
def test_workload_actual_page_and_attempt_caps_without_retry_amplification(
    stack, academic_payloads, monkeypatch, size, retry_each
):
    async def run():
        async def no_sleep(delay):
            pass

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        courses = [{**academic_payloads["course"], "id": 100 + i} for i in range(size)]
        replies = [response(PROFILE)]

        def add(payload):
            if retry_each:
                replies.extend([response({}, 503), response({}, 503)])
            replies.append(response(payload))

        add(courses)
        for course in courses:
            add(course)
            add([])
        async with stack(replies, max_get_attempts=3) as s:
            # Bind profile before the measured workload, as in the live smoke.
            await s.provider.get_profile(s.ctx)
            s.ctx.budget.http_attempts_used = 0
            before = len(wire_requests(s))
            result = await s.academic.get_upcoming(s.ctx)
            expected = {(5, False): 11, (5, True): 33, (20, False): 40, (20, True): 100}[
                (size, retry_each)
            ]
            assert len(wire_requests(s)) - before == expected == s.ctx.budget.http_attempts_used
            assert s.ctx.budget.pages_used <= 20
            if size == 5:
                assert result.complete and len(result.data.coverage.scanned) == 5
            else:
                assert not result.complete
                assert len(result.data.coverage.scanned) == (19 if not retry_each else 16)
            assert not any(b"/submissions/" in w for w in wire_requests(s))

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 404, 503])
def test_real_cross_course_failure_classification_and_continuation(
    stack, academic_payloads, status
):
    async def run():
        courses = [{**academic_payloads["course"], "id": 100 + i} for i in range(5)]
        replies = [response(PROFILE), response(courses)]
        for i, course in enumerate(courses):
            replies.extend([response(course), response({}, status) if i == 2 else response([])])
        async with stack(replies) as s:
            if status == 403:
                with pytest.raises(AuthorizationError):
                    await s.academic.get_upcoming(s.ctx)
                assert len(wire_requests(s)) == 8
            else:
                result = await s.academic.get_upcoming(s.ctx)
                assert len(result.data.coverage.scanned) == 4
                assert result.data.coverage.failed == ("102",)
                assert len(result.warnings) == 1 and not result.complete
                assert len(wire_requests(s)) == 12

    asyncio.run(run())


def test_shared_deadline_retains_successful_course_and_stops_network(
    stack, academic_payloads, monkeypatch
):
    async def run():
        d = academic_payloads
        async with stack(
            [
                response(PROFILE),
                response([d["course"], {**d["course"], "id": 9}]),
                response(d["course"]),
                response([]),
            ]
        ) as s:
            original = s.provider.list_assignments

            async def consume_time(ctx, *args, **kwargs):
                result = await original(ctx, *args, **kwargs)
                ctx.budget.monotonic_deadline = time.monotonic() - 1
                return result

            monkeypatch.setattr(s.provider, "list_assignments", consume_time)
            result = await s.academic.get_upcoming(s.ctx)
            assert result.data.coverage.scanned == ("8",) and result.data.coverage.failed == ("9",)
            assert len(wire_requests(s)) == 4 and not result.complete

    asyncio.run(run())


def test_malformed_transport_response_has_safe_reason_no_retry(stack):
    async def run():
        async with stack([response(body=TOKEN.encode())], max_get_attempts=3) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.client.get_profile(s.ctx)
            assert s.ctx.budget.http_attempts_used == 1
            assert json.loads(s.logs.getvalue().splitlines()[-1])["reason"] == "malformed_upstream"
            assert TOKEN not in s.logs.getvalue()

    asyncio.run(run())


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpcore.ConnectTimeout, "upstream_timeout"),
        (httpcore.ConnectError, "upstream_connection_error"),
    ],
)
def test_actual_pinned_backend_preserves_final_dial_category(stack, monkeypatch, exception, code):
    async def run():
        backend = PublicOriginBackend(ORIGIN)

        async def dns(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 443))]

        async def fail(*args, **kwargs):
            raise exception(TOKEN + ORIGIN)

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        monkeypatch.setattr(backend._backend, "connect_tcp", fail)
        async with stack([]) as s:
            await s.client._pool.aclose()
            s.client._pool = httpcore.AsyncConnectionPool(network_backend=backend)
            with pytest.raises(UpstreamUnavailableError) as caught:
                await s.client.get_profile(s.ctx)
            assert caught.value.diagnostic_code == code
            assert caught.value.__context__ is None
            assert TOKEN not in s.logs.getvalue() and ORIGIN not in s.logs.getvalue()

    asyncio.run(run())


def test_retry_budget_stop_retains_last_reason_without_another_attempt(stack):
    async def run():
        async with stack([response({}, 503)], max_get_attempts=3) as s:
            from dataclasses import replace

            s.ctx.budget.limits = replace(s.ctx.budget.limits, max_http_attempts=1)
            with pytest.raises(UpstreamUnavailableError) as caught:
                await s.client.get_profile(s.ctx)
            assert caught.value.diagnostic_code == "upstream_http_503"
            event = json.loads(s.logs.getvalue().splitlines()[-1])
            assert event["retry_stop"] == "request_budget" and not event["retry_exhausted"]
            assert s.ctx.budget.http_attempts_used == 1

    asyncio.run(run())
