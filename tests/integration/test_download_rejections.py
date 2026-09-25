"""Real policy/manager/storage diagnostics; only upstream transports are synthetic."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpcore
import pytest

from canvas_mcp import file_smoke
from canvas_mcp.application.files import FileService
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.domain.errors import (
    DownloadRejectedError,
    DownloadRejectionReason as Reason,
    FileTooLargeError,
    MalformedUpstreamError,
    StorageError,
)
from canvas_mcp.infrastructure.files.download import CanvasDownloadClient
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.storage import ManagedStore
from conftest import PROFILE, response
from test_files import CDN, CDN2, COURSE, FILE, REF, TOKEN, Pool, Reply, redirect
from test_files import file_stack as file_stack


PDF = dict(FILE, filename="PRIVATE.pdf", **{"content-type": "application/pdf"})
ZIP = dict(FILE, filename="PRIVATE.zip", **{"content-type": "application/zip"})


@pytest.mark.parametrize(
    "reply,metadata,reason,count",
    [
        (Reply(status=401), FILE, Reason.ANONYMOUS_UNAUTHORIZED, 0),
        (Reply(headers=[(b"content-encoding", b"gzip")]), FILE, Reason.UNSUPPORTED_ENCODING, 0),
        (Reply(headers=[(b"content-length", b"-1")]), FILE, Reason.INVALID_LENGTH, 0),
        (Reply(headers=[(b"content-type", b"PRIVATE_MIME")]), FILE, Reason.MIME_MISMATCH, 0),
        (Reply(headers=[(b"content-type", b"\xffPRIVATE_MIME")]), FILE, Reason.PROTOCOL_ERROR, 0),
        (
            Reply([httpcore.RemoteProtocolError("https://PRIVATE/?token=SECRET")]),
            FILE,
            Reason.PROTOCOL_ERROR,
            0,
        ),
        (Reply([TOKEN.encode()]), FILE, Reason.CREDENTIAL_REFLECTION, 0),
        (Reply([b"\xff"]), FILE, Reason.INVALID_TEXT, 0),
        (Reply([b"\x00"]), FILE, Reason.INVALID_TEXT, 0),
        (
            Reply([], headers=[(b"content-type", b"text/plain"), (b"content-length", b"5")]),
            FILE,
            Reason.LENGTH_MISMATCH,
            0,
        ),
        (
            Reply([], headers=[(b"content-type", b"application/pdf")]),
            PDF,
            Reason.PREFIX_MISMATCH,
            0,
        ),
        (
            Reply([], headers=[(b"content-type", b"application/zip")]),
            ZIP,
            Reason.PREFIX_MISMATCH,
            0,
        ),
        (Reply([b"<html>PRIVATE"]), FILE, Reason.UNSAFE_PREFIX, 13),
        (
            Reply([b"wrong"], headers=[(b"content-type", b"application/pdf")]),
            PDF,
            Reason.PREFIX_MISMATCH,
            5,
        ),
        (Reply([b"\xe2"]), FILE, Reason.INVALID_TEXT, 1),
        (
            Reply([b"hi"], headers=[(b"content-type", b"text/plain"), (b"content-length", b"5")]),
            FILE,
            Reason.LENGTH_MISMATCH,
            2,
        ),
    ],
)
def test_two_redirects_preserve_exact_safe_reason_and_real_temp_count(
    file_stack, reply, metadata, reason, count
):
    async def run():
        async with file_stack(
            [
                redirect(CDN + "/PRIVATE?signature=SECRET"),
                redirect(CDN2 + "/PRIVATE?signature=SECRET"),
                reply,
            ],
            metadata=metadata,
        ) as s:
            with pytest.raises(DownloadRejectedError) as caught:
                await s.files.download_file(s.ctx, REF)
            error = caught.value
            assert type(error) is DownloadRejectedError and error.reason is reason
            expected_code = reason.value + (
                "/http_mime_vs_extension" if reason is Reason.MIME_MISMATCH else ""
            )
            assert error.diagnostic_code == expected_code and str(error) == "download_rejected"
            assert error.__context__ is error.__cause__ is None
            assert len(s.pool.calls) == 3
            assert all("Authorization" not in c[2] for c in s.pool.calls)
            assert not s.store._pending and not s.store._records
            assert not list(s.settings.download_directory.rglob("*.part"))
            assert not list(s.settings.download_directory.rglob("*.blob"))
            events = [json.loads(line) for line in s.logs.getvalue().splitlines()]
            failure = [
                e
                for e in events
                if e["event"] == "canvas_request_failed" and e["operation"] == "files.download"
            ][-1]
            assert failure["bytes_written"] == count
            assert failure["reason"] == "download_rejected"
            assert len([e for e in events if e["event"] == "download_redirect_validated"]) == 2
            exposed = s.logs.getvalue() + repr(error) + repr(vars(error)) + error.diagnostic_code
            for private in (
                TOKEN,
                "PRIVATE",
                "SECRET",
                "signature",
                "report.txt",
                "Synthetic course",
                "Content-Disposition",
            ):
                assert private not in exposed

    asyncio.run(run())


@pytest.mark.parametrize(
    "reply,error",
    [
        (Reply(headers=[(b"content-length", b"6")]), FileTooLargeError),
        (
            Reply(headers=[(b"content-length", b"5"), (b"content-length", b"5")]),
            MalformedUpstreamError,
        ),
        (Reply(status=204), MalformedUpstreamError),
    ],
)
def test_distinct_public_errors_do_not_become_generic_rejection(file_stack, reply, error):
    async def run():
        async with file_stack(
            [redirect(CDN + "/a"), redirect(CDN2 + "/b"), reply], max_download_bytes=5
        ) as s:
            with pytest.raises(error) as caught:
                await s.files.download_file(s.ctx, REF)
            assert caught.value.diagnostic_code == error.code
            assert not s.store._pending and not s.store._records

    asyncio.run(run())


def test_abort_failure_overrides_policy_reason_and_is_fatal(file_stack, monkeypatch):
    async def run():
        async with file_stack([Reply(headers=[(b"content-type", b"text/html")])]) as s:
            original = s.store.abort

            def failed(pending):
                original(pending)
                raise StorageError()

            monkeypatch.setattr(s.store, "abort", failed)
            with pytest.raises(StorageError):
                await s.files.download_file(s.ctx, REF)
            assert not s.store._pending and not s.store._records

    asyncio.run(run())


def test_saved_second_candidate_uses_real_secure_download_then_cleanup(
    stack, tmp_path, monkeypatch, capsys
):
    async def run():
        files = [dict(FILE, id=i) for i in range(10, 14)]
        api = [
            response(PROFILE),
            response([COURSE, dict(COURSE, id=9)]),
            response(COURSE),
            response(files),
            response(COURSE),  # separate validation context freshly authorizes
            *(response(f) for f in files[:3]),
            response(COURSE),
            response(files[0]),  # rejected candidate: fresh authorization/metadata
            response({"public_url": "https://canvas.example.edu/signed/10?Signature=PRIVATE"}),
            response(COURSE),
            response(files[1]),  # next saved candidate: fresh authorization/metadata
            response({"public_url": "https://canvas.example.edu/signed/11?Signature=PRIVATE"}),
            response(COURSE),
            response(files[1]),  # hash verification authorization/metadata
        ]
        async with stack(
            api,
            download_directory=tmp_path / "managed",
            download_origins=(CDN, CDN2),
            max_download_bytes=file_smoke.SAMPLE_LIMIT,
        ) as s:
            pool = Pool(
                [
                    redirect(CDN + "/PRIVATE?signature=SECRET"),
                    redirect(CDN2 + "/PRIVATE"),
                    Reply(headers=[(b"content-type", b"text/html")]),
                    Reply(),
                ]
            )
            store = ManagedStore(s.settings, s.scope)
            downloader = CanvasDownloadClient(s.settings, s.client, s.logger, _pool=pool)
            manager = FileDownloadManager(s.provider, downloader, store, s.settings, s.logger)
            original_abort = store.abort
            aborts = []

            def abort(pending):
                original_abort(pending)
                assert not store._pending and not store._records
                aborts.append(True)

            monkeypatch.setattr(store, "abort", abort)

            @asynccontextmanager
            async def opened(env):
                assert int(env["MAX_DOWNLOAD_BYTES"]) == file_smoke.SAMPLE_LIMIT
                try:
                    yield CanvasConnection(
                        s.app, s.scope, s.settings, s.academic, FileService(s.provider, manager)
                    )
                finally:
                    await manager.aclose()

            monkeypatch.setattr(file_smoke, "open_canvas_connection", opened)
            assert await file_smoke.run(download_sample=True, debug=True) == 0
            assert len(pool.calls) == 4 and aborts == [True]
            assert not store._pending and not store._records
            assert list(s.settings.download_directory.iterdir()) == []
            requests = [w for w in s.backend.writes if w.startswith(b"GET")]
            assert len(requests) == 16
            assert sum(b"sort=size" in w for w in requests) == 1
            assert b"/courses/9" not in b"".join(requests)
            assert b"/files/13" not in b"".join(requests)
            assert (
                "Authorization" not in pool.calls[1][2] and "Authorization" not in pool.calls[2][2]
            )

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "REJECTED (mime_evidence_mismatch/http_mime_vs_extension)" in output
    assert "download                 PASS" in output and "cleanup                  PASS" in output
    for private in ("PRIVATE", "SECRET", "report.txt", "Synthetic course", "signature"):
        assert private not in output
