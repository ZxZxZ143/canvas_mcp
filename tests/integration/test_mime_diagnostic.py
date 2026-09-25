import asyncio
import hashlib
import io
import json
import zipfile

import pytest

from canvas_mcp.domain.errors import (
    ApplicationError,
    ConfigurationError,
    DownloadRejectedError,
    StorageError,
)
from canvas_mcp.domain.mime import DiagnosticStatus, ExpectedFormat, MimeScope
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES, identify
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic
from canvas_mcp.infrastructure.files.mime_registry import JsonMimeCompatibilityRegistry
from canvas_mcp.infrastructure.files.mime_runtime import MimeRuntime
from canvas_mcp.infrastructure.files.policy import MEDIA
from conftest import ORIGIN, response
from test_files import CDN, CDN2, FILE, REF, TOKEN, Reply, redirect, file_stack as file_stack

MIME = "application/x-weird-server-type"
SCOPE = MimeScope(ORIGIN, "personal", "7")


def metadata(extension, body):
    return {
        **FILE,
        "filename": "private-course-file." + extension,
        "content-type": MEDIA[extension],
        "size": len(body),
    }


def archive(*names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as handle:
        for name in names:
            handle.writestr(name, b"inert fixture")
    return output.getvalue()


@pytest.mark.parametrize(
    "extension,body,learnable",
    [
        ("pdf", b"%PDF-1.7\nfixture", True),
        ("txt", b" " * 1024 + b"<html>", False),
        ("docx", archive("[Content_Types].xml", "word/document.xml"), True),
        ("docx", archive("hello.txt"), False),
        (
            "xlsx",
            archive("[Content_Types].xml", "xl/workbook.xml", "xl/macrosheets/sheet1.xml"),
            False,
        ),
    ],
)
def test_quarantine_diagnostic_report_learning_and_cleanup(
    file_stack, tmp_path, extension, body, learnable
):
    async def run():
        replies = [
            redirect(CDN + "/one?signature=PRIVATE"),
            redirect(CDN2 + "/two?signature=PRIVATE"),
            Reply(
                (body,),
                headers=[
                    (b"content-type", (MIME + '; filename="SECRET_NAME"').encode()),
                    (b"content-length", str(len(body)).encode()),
                ],
            ),
        ]
        async with file_stack(
            replies, metadata(extension, body), max_download_bytes=MAX_PROBE_BYTES
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                registry = JsonMimeCompatibilityRegistry(runtime)
                diagnostic = MimeDiagnostic(s.manager, registry)
                record = await diagnostic.probe(s.ctx, REF, allow_mismatch=True, remember=True)
                assert (
                    record["size"] == len(body)
                    and record["sha256"] == hashlib.sha256(body).hexdigest()
                )
                assert record["redirect_count"] == 2 and record["http_mime"] == MIME
                assert record["state"] == "erased" and record["trust"] == "untrusted"
                assert (record["registry_action"] == "learned") is learnable
                assert not s.store._pending and not s.store._records
                assert not list((tmp_path / "owned").rglob("*.part"))
                assert not list((tmp_path / "owned").rglob("*.blob"))
                assert s.pool.calls[0][2]["Authorization"] == "Bearer " + TOKEN
                assert all("Authorization" not in call[2] for call in s.pool.calls[1:])
                runtime.write_report([record])
                exposed = json.dumps(record) + s.logs.getvalue()
                exposed += "".join(p.read_text() for p in (tmp_path / "diagnostics").glob("*.json"))
                for secret in (
                    TOKEN,
                    "PRIVATE",
                    "SECRET_NAME",
                    "private-course-file",
                    ORIGIN,
                    CDN,
                    "NEVER_FETCH",
                    "DO_NOT_RETURN",
                ):
                    assert secret not in exposed
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                rule = JsonMimeCompatibilityRegistry(runtime).lookup(
                    SCOPE, ExpectedFormat(extension), MIME
                )
                assert (rule is not None) is learnable

    asyncio.run(run())


@pytest.mark.parametrize(
    "body,headers",
    [
        (b"<html>private", [(b"content-type", MIME.encode())]),
        (b"MZprivate", [(b"content-type", MIME.encode())]),
        (b"not a PDF", [(b"content-type", MIME.encode())]),
        (b"%PDF-" + TOKEN.encode(), [(b"content-type", MIME.encode())]),
        (b"%PDF-fixture", [(b"content-type", MIME.encode()), (b"content-length", b"1")]),
        (b"%PDF-fixture", [(b"content-type", MIME.encode()), (b"content-length", b"-1")]),
        (b"%PDF-fixture", [(b"content-type", MIME.encode()), (b"content-encoding", b"gzip")]),
        (b"%PDF-fixture", []),
        (
            b"%PDF-fixture",
            [(b"content-type", MIME.encode()), (b"content-type", b"application/pdf")],
        ),
        (b"%PDF-fixture", [(b"content-type", b"application/" + TOKEN.lower().encode())]),
        (b"%PDF-fixture", [(b"content-type", b"text/private?url=https://private.example")]),
    ],
    ids=[
        "html",
        "exe",
        "prefix",
        "reflection",
        "length",
        "bad_length",
        "encoding",
        "missing_mime",
        "duplicate_mime",
        "header_secret",
        "header_url",
    ],
)
def test_non_mime_failure_stops_before_inspection_and_learning(
    file_stack, tmp_path, monkeypatch, body, headers
):
    async def run():
        async with file_stack(
            [Reply((body,), headers=headers)],
            metadata("pdf", body),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                registry = JsonMimeCompatibilityRegistry(runtime)
                diagnostic = MimeDiagnostic(s.manager, registry)
                monkeypatch.setattr(
                    "canvas_mcp.infrastructure.files.mime_diagnostic.identify",
                    lambda *a: pytest.fail("must not inspect"),
                )
                with pytest.raises(ApplicationError):
                    await diagnostic.probe(s.ctx, REF, allow_mismatch=True, remember=True)
                assert not s.store._pending and not s.store._records
                assert registry.lookup(SCOPE, ExpectedFormat.PDF, MIME) is None
                assert diagnostic.report["sha256"] is None
                if body.startswith(b"<html"):
                    assert diagnostic.report["result"] == DiagnosticStatus.UNSAFE.value
                runtime.write_report([diagnostic.report])
                assert TOKEN.lower() not in json.dumps(diagnostic.report).lower()
                assert "private.example" not in json.dumps(diagnostic.report)

    asyncio.run(run())


def test_known_alias_reconfirmed_then_contradiction_disables_and_normal_stays_strict(
    file_stack, tmp_path
):
    async def run():
        good = b"%PDF-fixture"
        async with file_stack(
            [
                Reply((good,), headers=[(b"content-type", MIME.encode())]),
                Reply((good,), headers=[(b"content-type", MIME.encode())]),
                Reply((b"<html>",), headers=[(b"content-type", MIME.encode())]),
            ],
            metadata("pdf", good),
            api_extra=[response(metadata("pdf", good)), response(metadata("pdf", good))],
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                registry = JsonMimeCompatibilityRegistry(runtime)
                registry.record_validated(
                    SCOPE, MIME, identify(good, ExpectedFormat.PDF), remember=True, ticket=None
                )
                with pytest.raises(DownloadRejectedError):
                    await s.manager.download_file(s.ctx, REF)
                assert registry.lookup(SCOPE, ExpectedFormat.PDF, MIME).evidence_count == 1
                diagnostic = MimeDiagnostic(s.manager, registry)
                record = await diagnostic.probe(s.ctx, REF, allow_mismatch=True)
                assert record["registry_action"] == "reconfirmed"
                assert registry.lookup(SCOPE, ExpectedFormat.PDF, MIME).evidence_count == 2
                with pytest.raises(DownloadRejectedError):
                    await diagnostic.probe(s.ctx, REF, allow_mismatch=True, remember=True)
                assert registry.lookup(SCOPE, ExpectedFormat.PDF, MIME).state == "disabled"
                assert not s.store._pending and not s.store._records

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://trusted-cdn.example/f",
        "https://127.0.0.1/f",
        "https://evil.example/f",
        CDN + "/f?secret=" + TOKEN,
    ],
)
def test_diagnostic_never_overrides_redirect_policy(file_stack, tmp_path, url):
    async def run():
        async with file_stack(
            [redirect(url)], metadata("pdf", b"%PDF-"), max_download_bytes=MAX_PROBE_BYTES
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                registry = JsonMimeCompatibilityRegistry(runtime)
                with pytest.raises(ApplicationError):
                    await MimeDiagnostic(s.manager, registry).probe(
                        s.ctx, REF, allow_mismatch=True, remember=True
                    )
                assert len(s.pool.calls) == 1 and not s.store._pending
                assert runtime.read_registry() is None

    asyncio.run(run())


def test_no_opt_in_means_no_io(file_stack, tmp_path):
    async def run():
        async with file_stack(max_download_bytes=MAX_PROBE_BYTES) as s:

            class NoRegistry:
                def __getattr__(self, name):
                    pytest.fail("registry touched")

            with pytest.raises(ConfigurationError):
                await MimeDiagnostic(s.manager, NoRegistry()).probe(
                    s.ctx, REF, allow_mismatch=False
                )
            assert not s.pool.calls and not (tmp_path / "owned").exists()

    asyncio.run(run())


def test_cleanup_failure_never_learns(file_stack, tmp_path, monkeypatch):
    async def run():
        body = b"%PDF-fixture"
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MIME.encode())])],
            metadata("pdf", body),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                registry = JsonMimeCompatibilityRegistry(runtime)
                original = s.store.abort

                def broken(pending):
                    original(pending)
                    raise StorageError()

                monkeypatch.setattr(s.store, "abort", broken)
                diagnostic = MimeDiagnostic(s.manager, registry)
                with pytest.raises(StorageError):
                    await diagnostic.probe(s.ctx, REF, allow_mismatch=True, remember=True)
                assert runtime.read_registry() is None
                assert diagnostic.report["state"] == "cleanup_unconfirmed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "canvas_mime",
        "dangerous_extension",
        "metadata_size",
        "actual_size",
        "timeout",
        "authorization",
        "anonymous_401",
        "text_nul",
        "text_utf8",
    ],
)
def test_other_security_checks_remain_fatal(file_stack, tmp_path, monkeypatch, kind):
    async def run():
        body = b"%PDF-fixture"
        meta = metadata("pdf", body)
        settings = {"max_download_bytes": MAX_PROBE_BYTES}
        replies = [Reply((body,), headers=[(b"content-type", MIME.encode())])]
        if kind == "canvas_mime":
            meta["content-type"] = "application/x-conflicting-canvas-claim"
        elif kind == "dangerous_extension":
            meta["filename"] = "danger.exe"
        elif kind == "metadata_size":
            meta["size"] = MAX_PROBE_BYTES + 1
        elif kind == "actual_size":
            meta["size"] = 5
            settings["max_download_bytes"] = 8
        elif kind == "timeout":
            settings["download_timeout_seconds"] = 0.02
            replies[0].delay = 0.1
        elif kind == "anonymous_401":
            replies = [redirect(CDN + "/f"), Reply(status=401)]
        elif kind in ("text_nul", "text_utf8"):
            body = b"a\0b" if kind == "text_nul" else b"\xff"
            meta = metadata("txt", body)
            replies = [Reply((body,), headers=[(b"content-type", MIME.encode())])]
        async with file_stack(replies, meta, **settings) as s:
            if kind == "authorization":
                from canvas_mcp.domain.errors import AuthorizationError

                async def deny(*a):
                    raise AuthorizationError()

                monkeypatch.setattr(s.provider, "get_file_metadata", deny)
            monkeypatch.setattr(
                "canvas_mcp.infrastructure.files.mime_diagnostic.identify",
                lambda *a: pytest.fail("must not inspect"),
            )
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                with pytest.raises(ApplicationError):
                    await MimeDiagnostic(s.manager, JsonMimeCompatibilityRegistry(runtime)).probe(
                        s.ctx, REF, allow_mismatch=True, remember=True
                    )
                assert (
                    runtime.read_registry() is None
                    and not s.store._pending
                    and not s.store._records
                )

    asyncio.run(run())


def test_connection_close_cancels_diagnostic_and_erases_pending(file_stack, tmp_path):
    async def run():
        started = asyncio.Event()

        class BlockedReply(Reply):
            async def aiter_stream(self):
                yield b"%PDF-"
                started.set()
                await asyncio.Event().wait()

        async with file_stack(
            [BlockedReply(headers=[(b"content-type", MIME.encode())])],
            metadata("pdf", b"%PDF-"),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                diagnostic = MimeDiagnostic(s.manager, JsonMimeCompatibilityRegistry(runtime))
                task = asyncio.create_task(
                    diagnostic.probe(s.ctx, REF, allow_mismatch=True, remember=True)
                )
                await asyncio.wait_for(started.wait(), 2)
                await s.manager.aclose()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert runtime.read_registry() is None
                assert not s.store._pending and not s.store._records
                assert not list((tmp_path / "owned").rglob("*.part"))

    asyncio.run(run())
