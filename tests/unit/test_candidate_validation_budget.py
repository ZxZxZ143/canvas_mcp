"""Separate, finite smoke phase budgets driven by the asyncio virtual clock."""

import asyncio

import pytest

from canvas_mcp import file_smoke
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason as Reason,
    FileTooLargeError,
    MalformedUpstreamError,
    StorageError,
    UnsafeRedirectError,
)
from test_file_discovery import (
    DiscoveryConnection,
    VirtualClock,
    install_connection,
    metadata,
    page,
)


def test_three_identities_at_119_seconds_get_fresh_validation_window(stack, monkeypatch, capsys):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()

            class Connection(DiscoveryConnection):
                async def list_course_files(self, ctx, course, request, query):
                    self.request(ctx, ("list", course))
                    await clock.advance(29 if course == "12" else 30)
                    return page(
                        [metadata("12", str(n)) for n in range(10, 14)] if course == "12" else []
                    )

                async def get_file_metadata(self, ctx, ref):
                    assert ctx is not s.ctx
                    assert ctx.budget.monotonic_deadline == started + 179
                    await clock.advance(10)
                    return await super().get_file_metadata(ctx, ref)

            connection = Connection(s, courses=("8", "9", "11", "12", "13"))
            discovery = await file_smoke.discover(connection, collect_samples=True, debug=True)
            assert clock.now() == started + 119 and len(discovery.identities) == 3
            assert not discovery.candidates and discovery.metadata_probes == 0
            assert not any(c[0] == "metadata" for c in connection.calls)
            assert discovery.courses_attempted == 4 and discovery.eligible_candidates == 4
            validation = await file_smoke.validate_candidates(
                connection, discovery.identities, debug=True
            )
            assert len(validation.candidates) == validation.probes == 3
            assert not validation.warnings and clock.now() == started + 149
            assert s.ctx.budget.monotonic_deadline == started + 120
            assert s.ctx.budget.http_attempts_used == 9 and s.ctx.budget.pages_used == 5
            assert len(connection.created_contexts) == 2
            assert connection.created_contexts[1].budget.http_attempts_used == 3
            assert connection.created_contexts[1].courses == {}
            assert ("list", "13") not in connection.calls
            assert s.settings.request_timeout_seconds == 20
            assert s.settings.aggregate_timeout_seconds == 60

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "candidate_validation     STARTED budget_ms=60000" in output
    assert "validated_candidates     3" in output
    assert "PRIVATE" not in output


def test_first_metadata_timeout_second_validates_and_downloads(stack, monkeypatch, capsys):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()

            class Connection(DiscoveryConnection):
                async def get_file_metadata(self, ctx, ref):
                    result = await super().get_file_metadata(ctx, ref)
                    await clock.advance(20.01 if ref.file_id == "10" else 5)
                    return result

            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 13)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 0
            assert clock.now() == started + 30.01
            assert [c for c in connection.calls if c[0] == "download"] == [("download", "8", "11")]
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]
            assert connection.created_contexts[1].budget.monotonic_deadline == started + 60
            assert connection.calls[-2:] == [("verify",), ("cleanup",)]

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "candidate_validation     PARTIAL" in output
    assert "validated_candidates     2" in output
    assert "download_candidate       PASS" in output and "download                 PASS" in output


def test_all_three_metadata_timeouts_end_at_sixty_not_ninety_or_infinite(
    stack, monkeypatch, capsys
):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)
            started = clock.now()
            unwound = []

            class Connection(DiscoveryConnection):
                async def get_file_metadata(self, ctx, ref):
                    await super().get_file_metadata(ctx, ref)
                    try:
                        await clock.advance(
                            min(20, ctx.budget.monotonic_deadline - clock.now()) + 0.01
                        )
                        pytest.fail("metadata timeout must cancel")
                    finally:
                        unwound.append(True)

            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 15)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 0
            assert len(unwound) == 3
            assert 60 <= clock.now() - started <= 60.02
            assert not any(c[0] == "download" for c in connection.calls)
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]
            assert not any(
                t is not asyncio.current_task() and not t.done() for t in asyncio.all_tasks()
            )

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "candidate_validation     PARTIAL (candidate_validation_budget_exceeded)" in output
    assert "download                 NOT_TESTED (candidate_validation_budget_exceeded)" in output
    assert "validated_candidates     0" in output and "no_files" not in output


@pytest.mark.parametrize("count", [1, 2, 4, 5])
def test_first_plausible_page_stops_discovery_before_any_metadata(stack, count):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 10 + count)]}
            )
            outcome = await file_smoke.discover(connection, collect_samples=True)
            assert len(outcome.identities) == min(count, 3)
            assert outcome.metadata_probes == 0 and not outcome.candidates
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]
            assert not any(c[0] == "metadata" for c in connection.calls)

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError(),
        AuthorizationError(),
        ConfigurationError(),
        StorageError(),
        UnsafeRedirectError(),
        FileTooLargeError(),
        MalformedUpstreamError(),
        DownloadRejectedError(reason=Reason.CREDENTIAL_REFLECTION),
        DownloadRejectedError(reason=Reason.METADATA_RESTRICTED),
        DownloadRejectedError(),
    ],
)
def test_validation_fatal_failures_abort_even_with_saved_identities(stack, monkeypatch, error):
    class Connection(DiscoveryConnection):
        async def get_file_metadata(self, ctx, ref):
            await super().get_file_metadata(ctx, ref)
            raise error

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 13)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 1
            assert len([c for c in connection.calls if c[0] == "metadata"]) == 1
            assert not any(c[0] == "download" for c in connection.calls)

    asyncio.run(run())


def test_unvalidated_or_policy_rejected_metadata_never_downloads(stack, monkeypatch, capsys):
    class Connection(DiscoveryConnection):
        async def get_file_metadata(self, ctx, ref):
            result = await super().get_file_metadata(ctx, ref)
            result.data = metadata(identity=ref.file_id, **{"content-type": "PRIVATE_MIME"})
            return result

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 13)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 0
            assert len([c for c in connection.calls if c[0] == "metadata"]) == 3
            assert not any(c[0] == "download" for c in connection.calls)

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "mime_evidence_mismatch/canvas_mime_vs_extension" in output
    assert "PRIVATE" not in output and "validated_candidates     0" in output


def test_external_validation_cancellation_is_not_swallowed(stack):
    async def run():
        async with stack([]) as s:
            entered, stopped = asyncio.Event(), asyncio.Event()

            class Connection(DiscoveryConnection):
                async def get_file_metadata(self, ctx, ref):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        stopped.set()

            task = asyncio.create_task(
                file_smoke.validate_candidates(Connection(s), (metadata().source,))
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped.is_set()

    asyncio.run(run())
