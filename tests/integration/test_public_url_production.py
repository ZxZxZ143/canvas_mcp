"""Normal FileService downloads use fresh Canvas capabilities, never browser routes."""

import asyncio
import hashlib
import io
import zipfile
from dataclasses import asdict
from pathlib import Path

import pytest

from canvas_mcp.domain.errors import (
    DownloadPermissionUnavailableError,
    DownloadRejectedError,
    MalformedUpstreamError,
    PublicUrlOriginUnapprovedError,
)
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource
from canvas_mcp.domain.models import FileReference
from canvas_mcp.infrastructure.files.policy import MEDIA
from conftest import ORIGIN, response
from test_files import CDN, REF, Pool, Reply, file_stack as file_stack
from test_mime_diagnostic import archive, metadata


SECRET = "SUPER_SECRET_CANVAS_PUBLIC_URL_TOKEN_57291"
SIGNED = CDN + "/object?Signature=PRIVATE_SIGNED_QUERY&Expires=12345"


def test_production_download_uses_authenticated_api_anonymous_capability_and_cleanup(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_extra=[response({"public_url": SIGNED})],
            mock_public_url=False,
            download_origins=(CDN,),
        ) as stack:
            stack.client._credentials = EnvironmentCredentialSource.from_environment(
                stack.scope, {"CANVAS_ACCESS_TOKEN": SECRET}
            )
            downloaded = (await stack.files.download_file(stack.ctx, REF)).data
            wire = b"".join(stack.backend.writes)
            assert b"GET /api/v1/files/10/public_url HTTP/1.1" in wire
            assert b"Authorization: Bearer " + SECRET.encode() in wire
            assert b"/files/10/download" not in wire
            assert len(stack.pool.calls) == 1 and stack.pool.calls[0][1] == SIGNED
            assert "Authorization" not in stack.pool.calls[0][2]
            assert "Cookie" not in stack.pool.calls[0][2]
            assert "Referer" not in stack.pool.calls[0][2]
            assert downloaded.trust == "untrusted"
            assert downloaded.sha256 == hashlib.sha256(body).hexdigest()
            assert Path(downloaded.local_path).read_bytes() == body
            exposed = repr(asdict(downloaded)) + stack.logs.getvalue()
            assert SECRET not in exposed and SIGNED not in exposed
            assert "PRIVATE_SIGNED_QUERY" not in exposed
            await stack.files.cleanup_download(stack.ctx, downloaded.artifact_id)
            assert not Path(downloaded.local_path).exists()
            assert not stack.store._records and not stack.store._pending

    asyncio.run(run())


@pytest.mark.parametrize("case", ["denied", "unapproved", "malformed"])
def test_production_capability_failure_never_falls_back_to_browser_route(file_stack, case):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        payload = (
            response({}, status=403)
            if case == "denied"
            else response({"public_url": "https://unknown.example/file?Signature=PRIVATE"})
            if case == "unapproved"
            else response({"public_url": "http://trusted-cdn.example/file"})
        )
        expected = {
            "denied": DownloadPermissionUnavailableError,
            "unapproved": PublicUrlOriginUnapprovedError,
            "malformed": MalformedUpstreamError,
        }[case]
        async with file_stack(
            [],
            metadata("docx", body),
            api_extra=[payload],
            mock_public_url=False,
            download_origins=(CDN,),
        ) as stack:
            with pytest.raises(expected) as caught:
                await stack.files.download_file(stack.ctx, REF)
            wire = b"".join(stack.backend.writes)
            assert b"/api/v1/files/10/public_url" in wire
            assert b"/files/10/download" not in wire
            assert not stack.pool.calls
            assert not stack.store._records and not stack.store._pending
            exposed = repr(caught.value) + stack.logs.getvalue()
            assert "PRIVATE" not in exposed and "unknown.example" not in exposed

    asyncio.run(run())


@pytest.mark.parametrize(
    "expected,body",
    [
        ("docx", archive("[Content_Types].xml", "ppt/presentation.xml")),
        ("docx", archive("[Content_Types].xml", "word/document.xml", "word/vbaProject.bin")),
    ],
)
def test_production_rejects_wrong_or_active_ooxml_before_publication(file_stack, expected, body):
    async def run():
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA[expected].encode())])],
            metadata(expected, body),
            api_extra=[response({"public_url": SIGNED})],
            mock_public_url=False,
            download_origins=(CDN,),
        ) as stack:
            with pytest.raises(DownloadRejectedError):
                await stack.files.download_file(stack.ctx, REF)
            assert not stack.store._records and not stack.store._pending

    asyncio.run(run())


def test_production_ooxml_structure_check_reads_bounded_metadata_above_one_mib(file_stack):
    async def run():
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as handle:
            handle.writestr("[Content_Types].xml", b"inert")
            handle.writestr("word/document.xml", b"inert")
            handle.writestr("word/media/image1.png", b"inert" * 220_000)
        body = output.getvalue()
        assert len(body) > 1_048_576
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_extra=[response({"public_url": SIGNED})],
            mock_public_url=False,
            download_origins=(CDN,),
        ) as stack:
            item = (await stack.files.download_file(stack.ctx, REF)).data
            assert item.size == len(body) and item.trust == "untrusted"
            await stack.files.cleanup_download(stack.ctx, item.artifact_id)

    asyncio.run(run())


def test_production_same_origin_private_capability_uses_exact_pool_anonymously(file_stack):
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
        ) as stack:
            assert stack.downloader._initial_pool is not None
            await stack.downloader._initial_pool.aclose()
            private = Pool([Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])])
            stack.downloader._initial_pool = private
            item = (await stack.files.download_file(stack.ctx, REF)).data
            assert not stack.pool.calls and len(private.calls) == 1
            assert private.calls[0][1] == url
            assert "Authorization" not in private.calls[0][2]
            assert item.trust == "untrusted"
            await stack.files.cleanup_download(stack.ctx, item.artifact_id)

    asyncio.run(run())


def test_production_same_origin_public_capability_needs_no_private_pin(file_stack):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        url = ORIGIN + "/signed?Signature=PRIVATE"
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_extra=[response({"public_url": url})],
            mock_public_url=False,
            download_origins=(),
        ) as stack:
            assert stack.downloader._initial_pool is None
            item = (await stack.files.download_file(stack.ctx, REF)).data
            assert len(stack.pool.calls) == 1 and stack.pool.calls[0][1] == url
            assert "Authorization" not in stack.pool.calls[0][2]
            await stack.files.cleanup_download(stack.ctx, item.artifact_id)

    asyncio.run(run())


@pytest.mark.parametrize(
    "source", ["assignment_attachment", "module_file", "submission_attachment"]
)
def test_production_resolves_each_source_before_capability(file_stack, academic_payloads, source):
    async def run():
        body = archive("[Content_Types].xml", "word/document.xml")
        d = academic_payloads
        attachment = {**d["attachment"], "id": 10, "filename": "report.docx"}
        assignment = {**d["assignment"], "attachments": [attachment]}
        module_item = {**d["module_item"], "type": "File", "content_id": 10}
        submission = {
            **d["submission"],
            "id": 47,
            "workflow_state": "submitted",
            "attachments": [attachment],
        }
        reference = FileReference(
            "10",
            "8",
            source,
            "21" if source == "module_file" else "10",
            "20" if source == "module_file" else None,
        )
        pre = [
            response(
                {
                    "assignment_attachment": assignment,
                    "module_file": module_item,
                    "submission_attachment": submission,
                }[source]
            )
        ]
        extra = (
            [response(submission), response({"public_url": SIGNED})]
            if source == "submission_attachment"
            else [response({"public_url": SIGNED})]
        )
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())])],
            metadata("docx", body),
            api_before_metadata=pre,
            api_extra=extra,
            mock_public_url=False,
            download_origins=(CDN,),
        ) as stack:
            item = (await stack.files.download_file(stack.ctx, reference)).data
            assert item.source == reference and item.trust == "untrusted"
            wire = b"".join(stack.backend.writes)
            assert b"/api/v1/files/10/public_url" in wire
            if source == "submission_attachment":
                assert b"/api/v1/files/10/public_url?submission_id=47" in wire
                assert wire.count(b"/submissions/self") == 2
            else:
                assert b"submission_id=" not in wire
            assert b"/files/10/download" not in wire
            assert "Authorization" not in stack.pool.calls[0][2]
            await stack.files.cleanup_download(stack.ctx, item.artifact_id)

    asyncio.run(run())
