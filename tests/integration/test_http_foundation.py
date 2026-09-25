import asyncio
import logging
import time

import httpcore
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
from conftest import PROFILE, TOKEN, response, next_link


def test_profile_http_header_and_safe_log(stack, caplog):
    async def run():
        async with stack([response(PROFILE)]) as s:
            caplog.set_level(logging.DEBUG)
            result = await s.app.get_profile(s.ctx)
            wire = b"".join(s.backend.writes)
            assert b"GET /api/v1/users/self HTTP/1.1" in wire
            assert ("Authorization: Bearer " + TOKEN).encode() in wire
            assert b"Accept-Encoding: identity" in wire
            assert result.data.display_name.text == "Student Name"
            assert "private@example.edu" not in repr(result)
            assert TOKEN not in repr(result) + s.logs.getvalue() + caplog.text
            assert s.backend.sni[0][0:2] == ("canvas.example.edu", True)

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,error_type",
    [
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (429, RateLimitError),
        (500, UpstreamUnavailableError),
        (502, UpstreamUnavailableError),
        (302, MalformedUpstreamError),
        (400, MalformedUpstreamError),
    ],
)
def test_statuses_are_safe(stack, status, error_type, caplog):
    async def run():
        caplog.set_level(logging.DEBUG)
        async with stack(
            [
                response(
                    {"message": TOKEN}, status, headers=[(b"Location", b"https://evil.example/")]
                )
            ]
        ) as s:
            with pytest.raises(error_type) as error:
                await s.app.get_profile(s.ctx)
            assert (
                TOKEN not in str(error.value) + repr(error.value) + s.logs.getvalue() + caplog.text
            )
            assert error.value.__context__ is None
            assert len(s.backend.targets) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"id":1,"id":2}',
        b'{"id":NaN}',
        b'{"id":1e999}',
        b"\xff",
        b"[" * 40 + b"0" + b"]" * 40,
    ],
)
def test_bad_json(stack, body):
    async def run():
        async with stack([response(body=body)]) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.client.get_profile(s.ctx)

    asyncio.run(run())


@pytest.mark.parametrize("escaped", [False, True])
def test_upstream_cannot_echo_secret_into_normalized_result(stack, escaped):
    async def run():
        encoded = "".join(f"\\u{ord(c):04x}" for c in TOKEN) if escaped else TOKEN
        body = ('{"id":7,"name":"' + encoded + '"}').encode()
        async with stack([response(body=body)]) as s:
            with pytest.raises(MalformedUpstreamError) as error:
                await s.app.get_profile(s.ctx)
            assert TOKEN not in str(error.value) + s.logs.getvalue()

    asyncio.run(run())


def test_timeout_and_unsafe_library_exception(stack, monkeypatch):
    async def run():
        async with stack([]) as s:

            async def fail(*args, **kwargs):
                raise httpcore.ConnectTimeout(TOKEN)

            monkeypatch.setattr(s.backend, "connect_tcp", fail)
            with pytest.raises(UpstreamUnavailableError) as error:
                await s.client.get_profile(s.ctx)
            assert TOKEN not in str(error.value) + repr(error.value) + s.logs.getvalue()
            assert error.value.__context__ is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "transform",
    [
        lambda value: value.replace("S", "&#83;", 1),
        lambda value: value[:10] + "<b>" + value[10:15] + "</b>" + value[15:],
        lambda value: value[:10] + "\x00" + value[10:],
    ],
)
def test_normalization_cannot_reassemble_reflected_secret(stack, transform):
    async def run():
        async with stack([response({**PROFILE, "name": transform(TOKEN)})]) as s:
            with pytest.raises(MalformedUpstreamError) as error:
                await s.app.get_profile(s.ctx)
            assert TOKEN not in str(error.value) + s.logs.getvalue()

    asyncio.run(run())


def test_total_timeout_covers_stream_reads(stack, monkeypatch):
    async def run():
        async with stack([], request_timeout_seconds=0.01) as s:

            async def slow(*args, **kwargs):
                await asyncio.sleep(1)

            monkeypatch.setattr(s.backend, "connect_tcp", slow)
            with pytest.raises(UpstreamUnavailableError):
                await s.client.get_profile(s.ctx)

    asyncio.run(run())


def test_body_limit_and_encoding(stack):
    async def run():
        async with stack([response(PROFILE)], max_api_response_bytes=8) as s:
            with pytest.raises(BudgetExceededError):
                await s.client.get_profile(s.ctx)
        async with stack([response(PROFILE, headers=[(b"Content-Encoding", b"gzip")])]) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.client.get_profile(s.ctx)

    asyncio.run(run())


def test_retries_share_budget_and_do_not_retry_auth(stack, monkeypatch):
    async def run():
        delays = []

        async def sleep(delay):
            delays.append(delay)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        async with stack(
            [response({}, 503), response({}, 429, [(b"Retry-After", b"999")]), response(PROFILE)],
            max_get_attempts=3,
        ) as s:
            await s.client.get_profile(s.ctx)
            assert s.ctx.budget.http_attempts_used == 3
            assert len(delays) == 2 and max(delays) <= 5
        async with stack([response({}, 401)], max_get_attempts=3) as s:
            with pytest.raises(AuthenticationError):
                await s.client.get_profile(s.ctx)
            assert s.ctx.budget.http_attempts_used == 1
        async with stack([response({}, 500)] * 3, max_get_attempts=3) as s:
            with pytest.raises(UpstreamUnavailableError):
                await s.client.get_profile(s.ctx)
            assert s.ctx.budget.http_attempts_used == 3

    asyncio.run(run())


def test_expired_budget_makes_no_request(stack):
    async def run():
        async with stack([]) as s:
            s.ctx.budget.monotonic_deadline = time.monotonic() - 1
            with pytest.raises(BudgetExceededError):
                await s.client.get_profile(s.ctx)
            assert not s.backend.writes

    asyncio.run(run())


def test_pool_cleanup_errors_are_safe_and_close_is_idempotent(stack, monkeypatch):
    async def run():
        async with stack([]) as s:

            async def fail():
                raise RuntimeError(TOKEN)

            monkeypatch.setattr(s.client._pool, "aclose", fail)
            with pytest.raises(UpstreamUnavailableError) as error:
                await s.client.aclose()
            assert TOKEN not in str(error.value) + repr(error.value)
            assert error.value.__context__ is None
            await s.client.aclose()

    asyncio.run(run())


def test_external_targets_and_uncontrolled_paths_never_sent(stack):
    async def run():
        async with stack([]) as s:
            for path in (
                "https://evil.example/api/v1/users/self",
                "/api/v1/assignments",
                "//evil.example",
            ):
                with pytest.raises(ValidationError):
                    await s.client._get(s.ctx, path, ())
            with pytest.raises(MalformedUpstreamError):
                await s.client.get_courses_page(
                    s.ctx, 25, True, "https://evil.example/api/v1/courses?page=2"
                )
            assert not s.backend.writes

    asyncio.run(run())


def test_external_next_is_rejected_before_second_request(stack):
    async def run():
        async with stack(
            [response([], headers=[(b"lInK", next_link(origin="https://evil.example"))])]
        ) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.client.get_courses_page(s.ctx, 25, True)
            assert len([w for w in s.backend.writes if w.startswith(b"GET")]) == 1

    asyncio.run(run())
