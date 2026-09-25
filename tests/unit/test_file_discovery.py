"""Deterministic synthetic smoke orchestration; no production behavior changes."""

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from canvas_mcp import file_smoke
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    RequestBudgetExceededError,
    UpstreamUnavailableError,
    MalformedUpstreamError,
)
from canvas_mcp.domain.models import FileReference, RequestBudget
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata


def metadata(course="8", identity="10", **overrides):
    raw = dict(
        id=int(identity),
        display_name="PRIVATE_TITLE",
        filename="PRIVATE.txt",
        size=5,
        **{"content-type": "text/plain"},
    )
    raw.update(overrides)
    return file_metadata(raw, FileReference(identity, course))


def page(items=(), *, complete=True):
    return SimpleNamespace(
        data=SimpleNamespace(
            items=tuple(items), complete=complete, next_cursor=None if complete else "opaque"
        )
    )


class DiscoveryConnection:
    def __init__(self, s, *, files=None, resolved=None, courses=("8", "9", "11"), complete=True):
        self.ctx, self._settings = s.ctx, s.settings
        self.created_contexts = []
        self.service = self
        self.calls, self.contexts = [], []
        self.course_ids, self.course_complete = courses, complete
        self.files = {"8": [metadata()]} if files is None else files
        self.resolved = resolved or {}
        self.downloaded = SimpleNamespace(
            artifact_id="a" * 32, size=5, sha256="b" * 64, redirects=0
        )

    def _context(self):
        ctx = (
            self.ctx
            if not self.created_contexts
            else replace(
                self.ctx,
                request_id=str(uuid4()),
                courses={},
                budget=RequestBudget(self.ctx.budget.limits, time.monotonic() + 60),
            )
        )
        self.created_contexts.append(ctx)
        return ctx

    def _files(self):
        return self

    def _academic(self):
        return self

    def request(self, ctx, operation):
        self.contexts.append(ctx)
        self.calls.append(operation)
        # Each separated stage contributes one logical GET. Production listing
        # and metadata reuse the course authorization memo on this same context.
        ctx.budget.http_attempts_used += 1
        if operation[0] in ("courses", "list"):
            ctx.budget.pages_used += 1

    async def get_profile(self):
        self.calls.append(("profile",))

    async def get_course(self, ctx, course):
        self.request(ctx, ("authorize", course))

    async def list_courses(self, ctx, request):
        assert request.limit == 10
        self.request(ctx, ("courses",))
        return page(
            [SimpleNamespace(id=identity) for identity in self.course_ids],
            complete=self.course_complete,
        )

    async def list_course_files(self, ctx, course, request, query):
        assert request.limit == 5 and query.sort == "size" and query.order == "asc"
        self.request(ctx, ("list", course))
        result = self.files.get(course, [])
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return await result(ctx)
        return page(result[: request.limit], complete=len(result) <= request.limit)

    async def get_file_metadata(self, ctx, ref):
        self.request(ctx, ("metadata", ref.course_id, ref.file_id))
        result = self.resolved.get(ref.course_id, metadata(ref.course_id, ref.file_id))
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = await result(ctx)
        return SimpleNamespace(data=result)

    async def download_file(self, ref):
        self.calls.append(("download", ref.course_id, ref.file_id))
        return SimpleNamespace(data=self.downloaded)

    async def resolve_download(self, artifact_id):
        assert artifact_id == self.downloaded.artifact_id
        self.calls.append(("verify",))
        return SimpleNamespace(data=self.downloaded)

    async def cleanup_download(self, artifact_id):
        assert artifact_id == self.downloaded.artifact_id
        self.calls.append(("cleanup",))


def install_connection(monkeypatch, connection):
    @asynccontextmanager
    async def opened(env):
        assert int(env["MAX_DOWNLOAD_BYTES"]) <= file_smoke.SAMPLE_LIMIT
        yield connection

    monkeypatch.setattr(file_smoke, "open_canvas_connection", opened)


@pytest.mark.parametrize("first_timeout", [False, True])
def test_first_match_short_circuits_and_downloads_after_partial_scan(
    stack, monkeypatch, capsys, first_timeout
):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(
                s,
                files={
                    "8": TimeoutError() if first_timeout else [metadata()],
                    "9": [metadata("9")],
                },
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True) == 0
            probes = [call for call in connection.calls if call[0] == "list"]
            assert (
                probes == [("list", "8"), ("list", "9")]
                if first_timeout
                else probes == [("list", "8")]
            )
            assert not any(call == ("list", "11") for call in connection.calls)
            assert connection.calls[-3][0] == "download"
            assert connection.calls[-2:] == [("verify",), ("cleanup",)]
            assert len(connection.created_contexts) == 2
            assert connection.created_contexts[0] is s.ctx
            assert all(ctx.scope == s.ctx.scope for ctx in connection.contexts)
            assert all(
                any(ctx is phase for phase in connection.created_contexts)
                for ctx in connection.contexts
            )

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "PARTIAL" in output and "metadata                 PASS" in output
    assert "download                 PASS" in output and "PRIVATE" not in output
    if first_timeout:
        assert (
            "TIMEOUT (probe_timeout)" in output
            and "attempted=2; listings_completed=1; partial_or_failed=1" in output
        )


def test_candidate_committed_at_ledger_boundary_is_not_discarded(stack, monkeypatch):
    async def run():
        async with stack([]) as s:

            async def last_success(ctx):
                ctx.budget.http_attempts_used = ctx.budget.limits.max_http_attempts
                ctx.budget.monotonic_deadline = time.monotonic() - 1
                return metadata()

            connection = DiscoveryConnection(s, resolved={"8": last_success})
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True) == 0
            assert [call for call in connection.calls if call[0] == "list"] == [("list", "8")]
            assert connection.calls[-3][0] == "download"
            assert connection.calls[-1] == ("cleanup",)

    asyncio.run(run())


@pytest.mark.parametrize("exhaustion", ["deadline", "attempts", "pages", "adapter"])
def test_budget_stop_preserves_completed_listing_and_no_later_probe(stack, capsys, exhaustion):
    async def run():
        async with stack([]) as s:

            async def exhaust(ctx):
                if exhaustion == "adapter":
                    raise RequestBudgetExceededError()
                if exhaustion == "deadline":
                    ctx.budget.monotonic_deadline = time.monotonic() - 1
                elif exhaustion == "attempts":
                    ctx.budget.http_attempts_used = ctx.budget.limits.max_http_attempts
                else:
                    ctx.budget.pages_used = ctx.budget.limits.max_pages
                return page([metadata()])

            connection = DiscoveryConnection(s, files={"8": exhaust})
            outcome = await file_smoke.discover(connection)
            assert outcome.candidate is None and outcome.reason == "request_budget_exceeded"
            assert outcome.courses_attempted == 1 and outcome.courses_partial_or_failed == 1
            assert outcome.courses_completed == (0 if exhaustion == "adapter" else 1)
            assert outcome.files_seen == (0 if exhaustion == "adapter" else 1)
            assert not any(call[0] == "metadata" for call in connection.calls)
            assert not outcome.complete

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "NOT_TESTED (request_budget_exceeded)" in output and "no_eligible" not in output


@pytest.mark.parametrize(
    "items,reason",
    [
        ([], "no_files"),
        ([metadata(filename="blocked.exe")], "no_policy_eligible_file"),
        ([metadata(size=None)], "no_policy_eligible_file"),
        ([metadata(size=1_048_577)], "no_policy_eligible_file"),
    ],
)
def test_complete_negative_scan_has_precise_reason(stack, capsys, items, reason):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(s, files={"8": items}, courses=("8",))
            outcome = await file_smoke.discover(connection)
            assert outcome.complete and outcome.reason == reason and outcome.candidate is None
            assert outcome.courses_attempted == outcome.courses_completed == 1
            assert outcome.metadata_probes == 0

    asyncio.run(run())
    assert f"NOT_TESTED ({reason})" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure,reason",
    [
        (UpstreamUnavailableError(), "all_course_probes_failed"),
        (NotFoundError(), "all_course_probes_failed"),
        (AuthorizationError(), "authorization_unavailable"),
    ],
)
def test_all_probes_failed_not_empty(stack, failure, reason):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(s, files={c: failure for c in ("8", "9", "11")})
            outcome = await file_smoke.discover(connection)
            assert not outcome.complete and outcome.reason == reason
            assert outcome.courses_attempted == outcome.courses_partial_or_failed == 3
            assert outcome.courses_completed == outcome.files_seen == 0

    asyncio.run(run())


def test_partial_empty_page_never_means_no_files(stack):
    async def run():
        async with stack([]) as s:

            async def incomplete(ctx):
                return page(complete=False)

            connection = DiscoveryConnection(s, files={"8": incomplete}, courses=("8",))
            outcome = await file_smoke.discover(connection)
            assert outcome.reason == "discovery_incomplete" and not outcome.complete
            assert outcome.courses_completed == outcome.courses_partial_or_failed == 1

    asyncio.run(run())


@pytest.mark.parametrize("part", ["course_page", "file_page", "metadata"])
def test_real_timeout_context_cancels_and_awaits_active_stage(stack, monkeypatch, capsys, part):
    async def run():
        async with stack([]) as s:
            stopped = asyncio.Event()

            async def blocked(ctx):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            connection = DiscoveryConnection(
                s,
                files={"8": blocked} if part == "file_page" else {"8": [metadata()]},
                resolved={"8": blocked} if part == "metadata" else {},
            )
            if part == "course_page":

                async def course_page(ctx, page):
                    await blocked(ctx)

                connection.list_courses = course_page
            outcome = await file_smoke.discover(
                connection, timing=file_smoke.SmokeTiming(discovery_timeout=0.02)
            )
            assert stopped.is_set() and outcome.reason == "request_budget_exceeded"
            assert outcome.candidate is None
            assert not any(
                task is not asyncio.current_task() and not task.done()
                for task in asyncio.all_tasks()
            )
            if part == "metadata":
                assert outcome.files_seen == outcome.courses_completed == 1
                assert outcome.eligible_candidates == outcome.metadata_probes == 1

    asyncio.run(run())
    output = capsys.readouterr().out
    assert (
        "CANCELLED (request_budget_exceeded)" in output
        or "TIMEOUT (request_budget_exceeded)" in output
    )


def test_listing_and_metadata_have_separate_stage_allowances(stack, monkeypatch):
    async def run():
        async with stack([]) as s:
            original_stage = file_smoke._stage
            stages = []

            @asynccontextmanager
            async def record(name, ctx, timeout, debug):
                stages.append((name, timeout))
                async with original_stage(name, ctx, timeout, debug):
                    yield

            monkeypatch.setattr(file_smoke, "_stage", record)
            outcome = await file_smoke.discover(DiscoveryConnection(s))
            assert outcome.candidate is not None
            assert stages == [
                ("course_discovery", 20),
                ("course_authorization", 20),
                ("course_file_probe", 35),
                ("file_metadata_probe", 20),
            ]
            assert file_smoke.SmokeTiming().discovery_timeout == 120

    asyncio.run(run())


def test_course_and_metadata_bounds_without_hidden_fanout(stack):
    async def run():
        async with stack([]) as s:
            courses = tuple(str(n) for n in range(8, 18))
            connection = DiscoveryConnection(s, courses=courses, files={}, complete=False)
            outcome = await file_smoke.discover(connection)
            assert outcome.courses_attempted == 10 and outcome.reason == "discovery_incomplete"
            assert s.ctx.budget.http_attempts_used == 21 and s.ctx.budget.pages_used == 11
        async with stack([]) as s:
            candidates = [metadata(identity=str(n)) for n in range(10, 15)]
            connection = DiscoveryConnection(
                s,
                files={c: candidates for c in ("8", "9", "11")},
                resolved={c: metadata(size=1_048_577) for c in ("8", "9", "11")},
            )
            outcome = await file_smoke.discover(connection)
            assert outcome.candidate is None and outcome.reason == "request_budget_exceeded"
            assert (
                outcome.metadata_probes == 10
                and outcome.files_seen == outcome.eligible_candidates == 15
            )
            assert outcome.courses_attempted == 3
            assert s.ctx.budget.http_attempts_used == 17

    asyncio.run(run())


@pytest.mark.parametrize(
    "error", [AuthenticationError(), MalformedUpstreamError(), ConfigurationError()]
)
def test_fatal_security_errors_not_treated_as_empty_or_downloadable(stack, error):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(s, files={"8": error})
            with pytest.raises(type(error)):
                await file_smoke.discover(connection)
            assert not any(call[0] == "download" for call in connection.calls)

    asyncio.run(run())


def test_external_cancellation_propagates_with_safe_terminal_diagnostics(stack, capsys):
    async def run():
        async with stack([]) as s:
            entered, stopped = asyncio.Event(), asyncio.Event()

            async def blocked(ctx):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            task = asyncio.create_task(
                file_smoke.discover(DiscoveryConnection(s, files={"8": blocked}))
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped.is_set()

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "CANCELLED (cancelled)" in output and "PRIVATE" not in output


def test_smaller_operator_download_limit_also_applies_to_discovery(stack):
    async def run():
        async with stack([], max_download_bytes=4) as s:
            outcome = await file_smoke.discover(DiscoveryConnection(s, courses=("8",)))
            assert outcome.reason == "no_policy_eligible_file" and outcome.metadata_probes == 0

    asyncio.run(run())


class VirtualClock:
    """Advance the real asyncio timer queue without sleeping for virtual seconds."""

    def __init__(self, monkeypatch):
        self.value = time.monotonic()
        monkeypatch.setattr(asyncio.get_running_loop(), "time", self.now)
        # Replace only the smoke module's time dependency, not stdlib time globally.
        monkeypatch.setattr(file_smoke, "time", SimpleNamespace(monotonic=self.now))

    def now(self):
        return self.value

    async def advance(self, seconds):
        self.value += seconds
        # First yield makes overdue timers runnable; second delivers cancellation.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@pytest.mark.parametrize("probe_timeout,selected", [(20, False), (35, True)])
def test_virtual_slow_listing_passes_new_cap_but_not_old_cap(
    stack, monkeypatch, capsys, probe_timeout, selected
):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()

            async def slow(ctx):
                # Total service/GET retry duration, NOT a single 25s HTTP attempt.
                assert s.settings.request_timeout_seconds == 20
                await clock.advance(25)
                return page([metadata()])

            connection = DiscoveryConnection(s, files={"8": slow}, courses=("8",))
            outcome = await file_smoke.discover(
                connection, timing=file_smoke.SmokeTiming(probe_timeout=probe_timeout), debug=True
            )
            assert (outcome.candidate is not None) == selected
            assert s.ctx.budget.monotonic_deadline == started + 120
            assert s.settings.aggregate_timeout_seconds == 60
            assert s.settings.download_timeout_seconds == 120

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "duration_ms=25000" in output and f"budget_ms={probe_timeout * 1000}" in output
    assert "PRIVATE" not in output


def test_virtual_first_timeout_then_later_candidate_after_old_global_deadline(stack, monkeypatch):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()

            async def timeout(ctx):
                await clock.advance(36)
                pytest.fail("stage must cancel")

            async def success(ctx):
                await clock.advance(25)
                return page([metadata("9")])

            connection = DiscoveryConnection(s, files={"8": timeout, "9": success})
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 0
            assert clock.now() == started + 61
            assert s.ctx.budget.monotonic_deadline == started + 120
            assert connection.calls[-3][0] == "download" and connection.calls[-1] == ("cleanup",)
            assert not any(call == ("list", "11") for call in connection.calls)

    asyncio.run(run())


def test_virtual_global_120_second_deadline_cancels_fourth_probe(stack, monkeypatch, capsys):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()
            unwound = []

            async def slow(ctx):
                remaining = ctx.budget.monotonic_deadline - clock.now()
                try:
                    await clock.advance(min(35, remaining) + 0.01)
                    pytest.fail("bounded stage must cancel")
                finally:
                    unwound.append(True)

            courses = tuple(str(n) for n in range(8, 18))
            connection = DiscoveryConnection(s, courses=courses, files={c: slow for c in courses})
            outcome = await file_smoke.discover(connection, debug=True)
            assert outcome.reason == "request_budget_exceeded" and outcome.candidate is None
            assert (
                outcome.courses_attempted == outcome.courses_partial_or_failed == len(unwound) == 4
            )
            assert 120 <= clock.now() - started <= 120.02
            assert s.ctx.budget.http_attempts_used == 9 and s.ctx.budget.pages_used == 5
            assert not any(
                task is not asyncio.current_task() and not task.done()
                for task in asyncio.all_tasks()
            )

    asyncio.run(run())
    assert "CANCELLED (request_budget_exceeded)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stage", ["course_authorization", "course_file_probe", "file_metadata_probe"]
)
def test_debug_distinguishes_authorization_listing_and_metadata_timeouts(
    stack, monkeypatch, capsys, stage
):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)

            async def blocked(ctx):
                await clock.advance(36 if stage == "course_file_probe" else 21)
                pytest.fail("stage should cancel")

            connection = DiscoveryConnection(s, courses=("8",))
            if stage == "course_authorization":

                async def authorize(ctx, course):
                    await blocked(ctx)

                connection.get_course = authorize
            elif stage == "course_file_probe":
                connection.files = {"8": blocked}
            else:
                connection.resolved = {"8": blocked}
            result = await file_smoke.discover(connection, debug=True)
            assert result.candidate is None

    asyncio.run(run())
    output = capsys.readouterr().out
    assert f"{stage:24} TIMEOUT (probe_timeout) duration_ms=" in output
    assert "PRIVATE" not in output
    if stage == "course_authorization":
        assert "course_file_probe" not in output
    if stage == "file_metadata_probe":
        assert "candidate_filter         PASS duration_ms=" in output


@pytest.mark.parametrize(
    "field,value",
    [
        ("probe_timeout", 0),
        ("probe_timeout", -1),
        ("probe_timeout", 60.01),
        ("discovery_timeout", 0),
        ("discovery_timeout", 180.01),
        ("probe_timeout", float("inf")),
        ("discovery_timeout", float("nan")),
        ("probe_timeout", True),
        ("discovery_timeout", "120"),
    ],
)
def test_smoke_only_configuration_rejects_invalid_bounds(field, value):
    from canvas_mcp.domain.errors import ValidationError

    with pytest.raises(ValidationError):
        file_smoke.SmokeTiming(**{field: value})


def test_timing_hard_maxima_are_accepted():
    assert file_smoke.SmokeTiming(60, 180).probe_timeout == 60


@pytest.mark.parametrize(
    "option,value",
    [
        ("--probe-timeout", "0"),
        ("--probe-timeout", "61"),
        ("--probe-timeout", "nan"),
        ("--discovery-timeout", "181"),
        ("--discovery-timeout", "inf"),
        ("--probe-timeout", "PRIVATE_TOKEN_92831"),
    ],
)
def test_cli_rejects_invalid_timeout_without_echoing_value(monkeypatch, capsys, option, value):
    monkeypatch.setattr(sys, "argv", ["file_smoke", "--live", option, value])
    with pytest.raises(SystemExit) as caught:
        file_smoke.main()
    assert caught.value.code == 2
    assert "PRIVATE_TOKEN_92831" not in capsys.readouterr().err


def test_cli_forwards_valid_smoke_timing_and_debug_only(monkeypatch):
    received = []

    async def run(**kwargs):
        received.append(kwargs)
        return 0

    monkeypatch.setattr(file_smoke, "run", run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "file_smoke",
            "--live",
            "--download-sample",
            "--debug",
            "--probe-timeout",
            "40",
            "--discovery-timeout",
            "150",
        ],
    )
    assert file_smoke.main() == 0
    assert received == [
        dict(download_sample=True, debug=True, timing=file_smoke.SmokeTiming(40, 150))
    ]
