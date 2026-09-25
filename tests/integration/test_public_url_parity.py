import asyncio
import socket
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from canvas_mcp import public_url_probe
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.domain.errors import (
    MalformedUpstreamError,
    NetworkPolicyError,
    PublicUrlOriginUnapprovedError,
)
from canvas_mcp.domain.models import FileReference
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.infrastructure.files.capability import parse_public_url, public_url_origin
from canvas_mcp.infrastructure.files.download import DownloadBackend
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic
from canvas_mcp.infrastructure.files.policy import MEDIA
from conftest import ORIGIN, response
from test_files import CDN, COURSE, REF, TOKEN, Pool, Reply, file_stack as file_stack
from test_mime_diagnostic import archive, metadata
from canvas_mcp.route_probe import _NoRuleRegistry


SIGNED = CDN + "/private-file?Signature=PRIVATE_SIGNED_QUERY&Expires=12345"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "http://trusted-cdn.example/file",
        "file:///tmp/file",
        "https://user@trusted-cdn.example/file",
        "https://trusted-cdn.example:444/file",
        "https://trusted-cdn.example/file#fragment",
        "https://trusted-cdn.example/file%0aescape",
        "https://trusted-cdn.example/file%zz",
        "https://trusted-cdn.example/" + TOKEN,
        "https://trusted-cdn.example/file?token=" + TOKEN,
        "https://trusted-cdn.example/file?token=%53" + TOKEN[1:],
        "https://trusted-cdn.example/file?token="
        + "".join(f"%25{ord(char):02X}" for char in TOKEN),
        "https://127.0.0.1/file",
        "https://169.254.169.254/file",
        "https://trusted-cdn.example\\@evil.example/file",
    ],
)
def test_capability_parser_rejects_malformed_or_unsafe_url(value):
    with pytest.raises(MalformedUpstreamError) as caught:
        parse_public_url({"public_url": value}, TOKEN, (CDN,))
    assert value is None or str(value) not in repr(caught.value)
    assert caught.value.__cause__ is caught.value.__context__ is None


def test_capability_parser_requires_exact_preapproved_origin():
    with pytest.raises(PublicUrlOriginUnapprovedError) as caught:
        parse_public_url(
            {"public_url": "https://other.example/file?Signature=PRIVATE"}, TOKEN, (CDN,)
        )
    assert "other.example" not in repr(caught.value)
    cap = parse_public_url({"public_url": SIGNED}, TOKEN, (CDN,))
    assert cap.target == SIGNED
    assert "PRIVATE_SIGNED_QUERY" not in repr(cap)
    assert parse_public_url({"public_url": CDN}, TOKEN, (CDN,)).target == CDN + "/"
    assert public_url_origin({"public_url": SIGNED}, TOKEN) == CDN


def test_approved_capability_origin_still_rejects_private_dns(monkeypatch):
    async def run():
        backend = DownloadBackend(frozenset((ORIGIN, CDN)))

        async def dns(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.4.200", 443))
            ]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        with pytest.raises(NetworkPolicyError):
            await backend.connect_tcp("trusted-cdn.example", 443)

    asyncio.run(run())


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--live"],
        ["--public-url-parity"],
        ["--public-url-origin"],
        ["--live", "--public-url-parity", "--public-url-origin"],
    ],
)
def test_cli_requires_explicit_opt_in_before_io(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["public_url_probe", *args])
    monkeypatch.setattr(
        public_url_probe, "open_canvas_connection", lambda *a: pytest.fail("network")
    )
    with pytest.raises(SystemExit) as error:
        public_url_probe.main()
    assert error.value.code == 2


def test_local_origin_inspection_prints_no_signed_url_or_secret(file_stack, monkeypatch, capsys):
    body = archive("[Content_Types].xml", "word/document.xml")
    meta = metadata("docx", body)
    selected = file_metadata(meta, REF)
    saved = {}

    @asynccontextmanager
    async def opened(env):
        async with file_stack(
            [],
            meta,
            api_extra=[response({"public_url": SIGNED})],
            mock_public_url=False,
            max_download_bytes=MAX_PROBE_BYTES,
        ) as stack:
            saved["stack"] = stack
            yield CanvasConnection(
                stack.app, stack.scope, stack.settings, stack.academic, stack.files
            )

    async def discovery(*args, **kwargs):
        return SimpleNamespace(identities=(REF,))

    async def validation(*args, **kwargs):
        return SimpleNamespace(candidates=(selected,))

    monkeypatch.setattr(public_url_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(public_url_probe, "discover", discovery)
    monkeypatch.setattr(public_url_probe, "validate_candidates", validation)
    assert asyncio.run(public_url_probe.run_origin(public_url_origin=True)) == 0
    assert capsys.readouterr().out == f"public_url_origin={CDN}\n"
    assert not saved["stack"].pool.calls


@pytest.mark.parametrize(
    "api_case", ["valid", "mime_mismatch", "denied", "unapproved", "malformed"]
)
def test_public_url_parity_is_anonymous_and_safe(file_stack, monkeypatch, capsys, api_case):
    body = archive("[Content_Types].xml", "word/document.xml")
    meta = metadata("docx", body)
    selected = file_metadata(meta, REF)
    api_reply = (
        response({}, status=403)
        if api_case == "denied"
        else response({"public_url": "https://other.example/file?Signature=PRIVATE"})
        if api_case == "unapproved"
        else response({"public_url": "http://trusted-cdn.example/file"})
        if api_case == "malformed"
        else response({"public_url": SIGNED})
    )
    saved = {}

    @asynccontextmanager
    async def opened(env):
        replies = [Reply((b"<html>wrapped</html>",), headers=[(b"content-type", b"text/html")])]
        if api_case in ("valid", "mime_mismatch"):
            mime = MEDIA["docx"].encode() if api_case == "valid" else b"text/plain"
            replies.append(Reply((body,), headers=[(b"content-type", mime)]))
        async with file_stack(
            replies,
            meta,
            api_extra=[response(COURSE), response(meta), api_reply],
            mock_public_url=False,
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            saved["stack"] = s
            yield CanvasConnection(s.app, s.scope, s.settings, s.academic, s.files)

    async def discovery(*args, **kwargs):
        return SimpleNamespace(identities=(REF,))

    async def validation(*args, **kwargs):
        return SimpleNamespace(candidates=(selected,))

    monkeypatch.setattr(public_url_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(public_url_probe, "discover", discovery)
    monkeypatch.setattr(public_url_probe, "validate_candidates", validation)
    assert asyncio.run(public_url_probe.run(public_url_parity=True)) == (
        0 if api_case == "valid" else 1
    )
    s = saved["stack"]
    assert len(s.pool.calls) == (2 if api_case in ("valid", "mime_mismatch") else 1)
    assert "Authorization" in s.pool.calls[0][2]
    if api_case in ("valid", "mime_mismatch"):
        assert s.pool.calls[1][1] == SIGNED
        assert "Authorization" not in s.pool.calls[1][2]
        assert "Cookie" not in s.pool.calls[1][2]
        assert "Referer" not in s.pool.calls[1][2]
        assert TOKEN not in repr(s.pool.calls[1])
    assert not s.store._pending and not s.store._records
    wire = b"".join(s.backend.writes)
    assert b"/api/v1/files/10/public_url" in wire
    assert b"Authorization: Bearer " + TOKEN.encode() in wire
    output = capsys.readouterr().out
    assert "variant=browser_route api_authorized=true status=200 final_class=html" in output
    if api_case == "valid":
        assert (
            "variant=public_url api_authorized=true public_url_received=true download_status=200 final_class=binary detected_format=docx reason=none"
            in output
        )
    elif api_case == "mime_mismatch":
        assert "detected_format=docx reason=mime_evidence_mismatch" in output
    elif api_case == "denied":
        assert "reason=download_permission_unavailable" in output
    elif api_case == "unapproved":
        assert "reason=public_url_origin_unapproved" in output
        assert "public_url_received=true" in output
    else:
        assert "reason=malformed_upstream" in output
    for private in (SIGNED, "PRIVATE", TOKEN, ORIGIN, CDN, "other.example", "private-course-file"):
        assert private not in output + s.logs.getvalue()


def test_submission_capability_uses_verified_own_submission_id(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        ref = FileReference("10", "8", "submission_attachment", "9")
        own = {
            "id": 47,
            "assignment_id": 9,
            "user_id": 7,
            "workflow_state": "submitted",
            "attachments": [{"id": 10, "filename": "private-course-file.docx"}],
        }
        async with file_stack(
            [],
            metadata("docx", body),
            api_extra=[
                response(own),
                response(metadata("docx", body)),
                response(own),
                response({"public_url": SIGNED}),
            ],
            mock_public_url=False,
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            # Resolution rechecks course, own-submission membership, and the
            # actual submission id before the public_url API request.
            await s.provider.get_profile(s.ctx)
            await s.provider.get_file_metadata(s.ctx, REF)
            resolved, cap = await s.provider.resolve_file_capability(s.ctx, ref)
            assert resolved.source == ref
            assert cap.target == SIGNED
            wire = b"".join(s.backend.writes)
            assert b"/api/v1/files/10/public_url?submission_id=47" in wire

    asyncio.run(run())


def test_same_canvas_origin_capability_uses_exact_private_pool_without_auth(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        url = ORIGIN + "/signed?Signature=PRIVATE"
        async with file_stack(
            [],
            metadata("docx", body),
            api_extra=[response({"public_url": url})],
            mock_public_url=False,
            allow_private_origin=True,
            trusted_private_ips=("192.168.4.200",),
            download_origins=(),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            assert s.downloader._initial_pool is not None
            assert s.downloader._initial_pool._network_backend._trusted_private_ips == (
                "192.168.4.200",
            )
            await s.downloader._initial_pool.aclose()
            private = Pool([Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])])
            s.downloader._initial_pool = private
            report = await MimeDiagnostic(s.manager, _NoRuleRegistry()).probe(
                s.ctx, REF, allow_mismatch=True, public_url=True
            )
            assert report["detected_format"] == "docx"
            assert not s.pool.calls
            assert len(private.calls) == 1
            assert private.calls[0][1] == url
            assert "Authorization" not in private.calls[0][2]
            assert not s.store._pending and not s.store._records

    asyncio.run(run())


def test_same_canvas_origin_capability_uses_public_pool_without_private_trust(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        url = ORIGIN + "/signed?Signature=PRIVATE"
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_extra=[response({"public_url": url})],
            mock_public_url=False,
            download_origins=(),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            assert s.downloader._initial_pool is None
            report = await MimeDiagnostic(s.manager, _NoRuleRegistry()).probe(
                s.ctx, REF, allow_mismatch=True, public_url=True
            )
            assert report["detected_format"] == "docx"
            assert len(s.pool.calls) == 1 and s.pool.calls[0][1] == url
            assert "Authorization" not in s.pool.calls[0][2]

    asyncio.run(run())


def test_external_capability_does_not_use_canvas_private_pool(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_extra=[response({"public_url": SIGNED})],
            mock_public_url=False,
            allow_private_origin=True,
            trusted_private_ips=("192.168.4.200",),
            download_origins=(CDN,),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            assert s.downloader._initial_pool is not None
            await s.downloader._initial_pool.aclose()
            private = Pool([])
            s.downloader._initial_pool = private
            report = await MimeDiagnostic(s.manager, _NoRuleRegistry()).probe(
                s.ctx, REF, allow_mismatch=True, public_url=True
            )
            assert report["detected_format"] == "docx"
            assert not private.calls and len(s.pool.calls) == 1
            assert "Authorization" not in s.pool.calls[0][2]

    asyncio.run(run())


def test_same_origin_capability_loses_private_trust_after_external_redirect(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        url = ORIGIN + "/signed?Signature=PRIVATE"
        async with file_stack(
            [
                Reply(status=302, headers=[(b"location", (ORIGIN + "/return").encode())]),
                Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())]),
            ],
            metadata("docx", body),
            api_extra=[response({"public_url": url})],
            mock_public_url=False,
            allow_private_origin=True,
            trusted_private_ips=("192.168.4.200",),
            download_origins=(CDN,),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            assert s.downloader._initial_pool is not None
            await s.downloader._initial_pool.aclose()
            private = Pool([Reply(status=302, headers=[(b"location", (CDN + "/out").encode())])])
            s.downloader._initial_pool = private
            report = await MimeDiagnostic(s.manager, _NoRuleRegistry()).probe(
                s.ctx, REF, allow_mismatch=True, public_url=True
            )
            assert report["detected_format"] == "docx"
            assert len(private.calls) == 1 and len(s.pool.calls) == 2
            assert private.calls[0][1] == url
            assert [call[1] for call in s.pool.calls] == [CDN + "/out", ORIGIN + "/return"]
            assert all("Authorization" not in call[2] for call in [*private.calls, *s.pool.calls])

    asyncio.run(run())
