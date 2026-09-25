"""Synthetic candidate retention/fallback and diagnostic boundary regressions."""

import asyncio

import pytest

from canvas_mcp import file_smoke
from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason as Reason,
    DownloadTimeoutError,
    FileTooLargeError,
    MalformedUpstreamError,
    StorageError,
    UnsafeRedirectError,
)
from test_file_discovery import DiscoveryConnection, VirtualClock, install_connection, metadata


@pytest.mark.parametrize("debug", [False, True])
def test_policy_rejected_first_sample_second_succeeds(stack, monkeypatch, capsys, debug):
    class Connection(DiscoveryConnection):
        async def download_file(self, ref):
            result = await super().download_file(ref)
            if ref.file_id == "10":
                raise DownloadRejectedError(reason=Reason.MIME_MISMATCH)
            return result

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 15)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=debug) == 0
            assert [c for c in connection.calls if c[0] == "download"] == [
                ("download", "8", "10"),
                ("download", "8", "11"),
            ]
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]
            assert len([c for c in connection.calls if c[0] == "metadata"]) == 3
            assert connection.calls[-2:] == [("verify",), ("cleanup",)]

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "download                 PASS" in output and "cleanup                  PASS" in output
    assert "metadata                 PASS" in output and "PRIVATE" not in output
    assert ("mime_evidence_mismatch" in output) == debug
    assert "REJECTED (" in output


@pytest.mark.parametrize("reason", list(file_smoke.SAMPLE_FALLBACK_REASONS))
def test_all_policy_rejections_stop_at_three_without_rediscovery(
    stack, monkeypatch, capsys, reason
):
    class Connection(DiscoveryConnection):
        async def download_file(self, ref):
            await super().download_file(ref)
            raise DownloadRejectedError(reason=reason)

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 15)]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 1
            assert len([c for c in connection.calls if c[0] == "download"]) == 3
            assert len([c for c in connection.calls if c[0] == "courses"]) == 1
            assert len([c for c in connection.calls if c[0] == "list"]) == 1
            assert not any(c[0] in ("verify", "cleanup") for c in connection.calls)

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "download                 FAIL (no_policy_downloadable_candidate)" in output
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError(),
        AuthorizationError(),
        ConfigurationError(),
        StorageError(),
        UnsafeRedirectError(),
        FileTooLargeError(),
        DownloadTimeoutError(),
        MalformedUpstreamError(),
        *(
            DownloadRejectedError(reason=r)
            for r in Reason
            if r not in file_smoke.SAMPLE_FALLBACK_REASONS
        ),
        # Even a subclass with a permitted reason cannot impersonate a file-policy rejection.
        UnsafeRedirectError(reason=Reason.MIME_MISMATCH),
    ],
)
def test_fatal_failure_aborts_without_next_candidate(stack, monkeypatch, capsys, error):
    class Connection(DiscoveryConnection):
        async def download_file(self, ref):
            await super().download_file(ref)
            raise error

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity="10"), metadata(identity="11")]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True, debug=True) == 1
            assert len([c for c in connection.calls if c[0] == "download"]) == 1
            assert not any(c[0] in ("verify", "cleanup") for c in connection.calls)

    asyncio.run(run())
    output = capsys.readouterr().out
    assert f"download                 FAIL ({error.diagnostic_code})" in output
    assert "no_policy_downloadable_candidate" not in output


@pytest.mark.parametrize("failure", ["verify", "cleanup"])
def test_after_download_verification_or_cleanup_never_triggers_fallback(
    stack, monkeypatch, failure
):
    class Connection(DiscoveryConnection):
        async def resolve_download(self, identity):
            result = await super().resolve_download(identity)
            if failure == "verify":
                raise DownloadRejectedError(reason=Reason.MIME_MISMATCH)
            return result

        async def cleanup_download(self, identity):
            await super().cleanup_download(identity)
            if failure == "cleanup":
                raise StorageError()

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(identity="10"), metadata(identity="11")]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True) == 1
            assert len([c for c in connection.calls if c[0] == "download"]) == 1
            assert connection.calls[-1] == ("cleanup",)

    asyncio.run(run())


def test_later_metadata_timeout_preserves_first_and_deduplicates(stack, monkeypatch):
    class Connection(DiscoveryConnection):
        async def get_file_metadata(self, ctx, ref):
            result = await super().get_file_metadata(ctx, ref)
            if ref.file_id == "11":
                raise TimeoutError()
            return result

    async def run():
        async with stack([]) as s:
            connection = Connection(
                s, files={"8": [metadata(), metadata(), metadata(identity="11")]}
            )
            install_connection(monkeypatch, connection)
            assert await file_smoke.run(download_sample=True) == 0
            assert [c for c in connection.calls if c[0] == "metadata"] == [
                ("metadata", "8", "10"),
                ("metadata", "8", "11"),
            ]
            assert len([c for c in connection.calls if c[0] == "download"]) == 1
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]

    asyncio.run(run())


def test_alternate_global_timeout_does_not_discard_committed_candidate(stack, monkeypatch):
    async def run():
        async with stack([]) as s:
            clock = VirtualClock(monkeypatch)

            class Connection(DiscoveryConnection):
                async def get_file_metadata(self, ctx, ref):
                    result = await super().get_file_metadata(ctx, ref)
                    if ref.file_id == "11":
                        await clock.advance(60.01)
                        pytest.fail("global timeout must cancel alternate")
                    return result

            connection = Connection(
                s, files={"8": [metadata(), metadata(identity="11"), metadata(identity="12")]}
            )
            discovery = await file_smoke.discover(connection, collect_samples=True)
            outcome = await file_smoke.validate_candidates(connection, discovery.identities)
            assert outcome.reason == "candidate_validation_budget_exceeded"
            assert len(outcome.candidates) == 1 and outcome.candidates[0].source.file_id == "10"
            assert outcome.probes == 2
            assert [c for c in connection.calls if c[0] == "list"] == [("list", "8")]

    asyncio.run(run())


def test_metadata_only_mode_retains_just_first_candidate(stack):
    async def run():
        async with stack([]) as s:
            connection = DiscoveryConnection(
                s, files={"8": [metadata(identity=str(n)) for n in range(10, 15)]}
            )
            outcome = await file_smoke.discover(connection)
            assert len(outcome.candidates) == outcome.metadata_probes == 1
            assert not any(c[0] == "download" for c in connection.calls)

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields,reason",
    [
        ({"locked": True}, Reason.METADATA_RESTRICTED),
        ({"filename": "PRIVATE.exe"}, Reason.UNSUPPORTED_EXTENSION),
        ({"content-type": "PRIVATE_MIME"}, Reason.MIME_MISMATCH),
    ],
)
def test_pre_download_metadata_policy_reason_is_specific(fields, reason):
    from canvas_mcp.infrastructure.files.policy import metadata_policy

    with pytest.raises(DownloadRejectedError) as caught:
        metadata_policy(metadata(**fields), file_smoke.SAMPLE_LIMIT)
    assert caught.value.reason is reason
    assert "PRIVATE" not in repr(vars(caught.value))


@pytest.mark.parametrize("reason", list(Reason))
def test_safe_reason_is_fixed_and_survives_sanitization(reason):
    error = DownloadRejectedError(reason=reason)
    clean = error.sanitized().sanitized()
    assert type(clean) is DownloadRejectedError and clean.reason is reason
    assert clean.code == str(clean) == "download_rejected"
    assert clean.diagnostic_code == reason.value
    assert clean.__context__ is clean.__cause__ is None
    assert set(vars(clean)) == {"reason", "mime_reason", "retry_exhausted"}
    assert clean.mime_reason is None
    assert all(c.islower() or c == "_" for c in clean.diagnostic_code)


@pytest.mark.parametrize(
    "value", ["text/html", "PRIVATE.txt", "https://private/?token=SECRET", {}, None]
)
def test_reason_rejects_arbitrary_values(value):
    with pytest.raises(TypeError) as caught:
        DownloadRejectedError(reason=value)
    assert str(caught.value) == ""


@pytest.mark.parametrize("kind", [UnsafeRedirectError, FileTooLargeError])
def test_rejection_subclass_keeps_existing_public_code(kind):
    error = kind().sanitized()
    assert type(error) is kind and error.diagnostic_code == kind.code
