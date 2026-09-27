"""Fresh authorized API -> anonymous capability -> real Linux worker -> clean result."""

import asyncio
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from canvas_mcp.application.file_content import FileContentService
from canvas_mcp.domain.errors import (
    AuthenticationError,
    BudgetExceededError,
    FileParseError,
    NotFoundError,
    UnsupportedFileFormatError,
    FileContentTooLargeError,
    FileContentTimeoutError,
)
from canvas_mcp.domain.file_content import ContentSelection
from canvas_mcp.domain.models import FileReference
from canvas_mcp.infrastructure.files import content_manager as manager_module
from canvas_mcp.infrastructure.files.content_manager import RemoteFileContentManager
from canvas_mcp.infrastructure.files.ephemeral import EphemeralStore
from canvas_mcp.infrastructure.files.policy import MEDIA
from canvas_mcp.mcp import projection as dto
from canvas_mcp.mcp.artifact_result import artifact_result
from conftest import ORIGIN, PROFILE, TOKEN, response
from file_content_fixtures import pdf_bytes

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux remote storage/worker flow")
COURSE = {
    "id": 8,
    "name": "Synthetic",
    "course_code": "SYN",
    "enrollments": [{"type": "student", "enrollment_state": "active", "user_id": 7}],
}
REFERENCE = FileReference("10", "8")
CAPABILITY = ORIGIN + "/anonymous?Signature=PRIVATE_SIGNED_QUERY"


class Pool:
    def __init__(self, body, fmt="pdf", declared_length=None):
        self.body, self.fmt, self.calls = body, fmt, []
        self.declared_length = declared_length

    @asynccontextmanager
    async def stream(self, method, target, headers, extensions):
        self.calls.append((method, target, headers))
        assert target == CAPABILITY
        assert "Authorization" not in headers and "Cookie" not in headers
        body, fmt = self.body, self.fmt

        class Response:
            status = 200
            headers = [(b"content-type", MEDIA.get(fmt, "application/octet-stream").encode())]
            if self.declared_length is not None:
                headers += [(b"content-length", str(self.declared_length).encode())]

            async def aiter_stream(self):
                yield body

        yield Response()

    async def aclose(self):
        pass


@pytest.fixture
def content_stack(stack, tmp_path, monkeypatch):
    monkeypatch.setattr(
        manager_module, "EphemeralStore", lambda maximum: EphemeralStore(maximum, _root=tmp_path)
    )

    @asynccontextmanager
    async def make(
        body=None,
        fmt="pdf",
        *,
        association_failure=False,
        metadata_size=None,
        declared_length=None,
        calls=1,
        **settings,
    ):
        body = pdf_bytes() if body is None else body
        file = {
            "id": 10,
            "display_name": "controlled." + fmt,
            "filename": "controlled." + fmt,
            "content-type": MEDIA.get(fmt, "application/octet-stream"),
            "size": len(body) if metadata_size is None else metadata_size,
        }
        replies = [response(PROFILE), response(COURSE)]
        if association_failure:
            replies += [response({}, status=404)]
        else:
            replies += [response(file), response({"public_url": CAPABILITY})]
            for _ in range(calls - 1):
                replies += [response(COURSE), response(file), response({"public_url": CAPABILITY})]
        async with stack(replies, **settings) as state:
            manager = RemoteFileContentManager(
                state.provider, state.client, state.settings, state.logger
            )
            await manager._client._pool.aclose()
            pool = Pool(body, fmt, declared_length)
            manager._client._pool = pool
            try:
                yield state, manager, FileContentService(manager), pool
            finally:
                await manager.aclose()

    return make


def test_actual_authorized_download_extraction_and_no_locators(content_stack, tmp_path):
    async def run():
        body = pdf_bytes()
        async with content_stack(body) as (s, manager, service, pool):
            content = await service.get_file_content(s.ctx, REFERENCE, ContentSelection())
            output = dto.file_content_envelope(content)
            wire = b"".join(s.backend.writes)
            assert b"GET /api/v1/files/10/public_url HTTP/1.1" in wire
            assert b"Authorization: Bearer " + TOKEN.encode() in wire
            assert len(pool.calls) == 1
            assert "Controlled PDF requirement" in output["data"]["content"]["units"][0]["text"]
            assert output["data"]["file"]["sha256"] == hashlib.sha256(body).hexdigest()
            for forbidden in (
                TOKEN,
                CAPABILITY,
                "PRIVATE_SIGNED_QUERY",
                "local_path",
                str(tmp_path),
            ):
                assert forbidden not in json.dumps(output) + s.logs.getvalue()
            # The logger's allowlisted operation name files.public_url is safe;
            # the infrastructure-private capability value/field never serializes.
            assert "public_url" not in json.dumps(output)
            assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_original_download_has_exact_bytes_hash_cleanup_and_anonymous_transport(
    content_stack, tmp_path
):
    import base64

    async def run():
        body = pdf_bytes()
        async with content_stack(body) as (s, manager, service, pool):
            original = await service.download_original(s.ctx, REFERENCE)
            output = artifact_result(original)
            assert base64.b64decode(output.meta["canvasArtifact"]["base64"]) == body
            assert original.data.sha256 == hashlib.sha256(body).hexdigest()
            assert len(pool.calls) == 1
            visible = json.dumps(output.structuredContent)
            assert not any(
                forbidden in visible
                for forbidden in (
                    TOKEN,
                    CAPABILITY,
                    "PRIVATE_SIGNED_QUERY",
                    str(tmp_path),
                    "local_path",
                )
            )
            assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize(
    "case", ["association", "oversized", "reflection", "invalidated", "cancel"]
)
def test_original_failure_and_cancellation_leave_no_staging(
    content_stack, tmp_path, monkeypatch, case
):
    async def run():
        body = (
            b"Original " + CAPABILITY.encode() if case == "reflection" else b"Original safe text."
        )
        async with content_stack(
            body,
            "txt",
            association_failure=case == "association",
            metadata_size=4194305 if case == "oversized" else None,
        ) as (s, manager, service, pool):
            if case in ("cancel", "invalidated"):
                stream = manager._client.stream

                async def changed(*args, **kwargs):
                    await stream(*args, **kwargs)
                    if case == "cancel":
                        raise asyncio.CancelledError()
                    s.provider._invalidate()

                monkeypatch.setattr(manager._client, "stream", changed)
            expected = {
                "cancel": asyncio.CancelledError,
                "association": NotFoundError,
                "oversized": FileContentTooLargeError,
                "reflection": FileParseError,
                "invalidated": AuthenticationError,
            }[case]
            with pytest.raises(expected):
                await service.download_original(s.ctx, REFERENCE)
            assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize(
    "case", ["malformed", "unsupported", "association", "cancel", "response_limit", "invalidated"]
)
def test_failed_or_cancelled_calls_leave_no_artifacts(content_stack, tmp_path, monkeypatch, case):
    async def run():
        body = (
            b"%PDF-1.7 malformed"
            if case == "malformed"
            else b"inert"
            if case == "unsupported"
            else pdf_bytes()
        )
        fmt = "zip" if case == "unsupported" else "pdf"
        async with content_stack(body, fmt, association_failure=case == "association") as (
            s,
            manager,
            service,
            pool,
        ):
            if case == "cancel":
                entered = asyncio.Event()

                async def waiting(*args):
                    entered.set()
                    await asyncio.sleep(60)

                monkeypatch.setattr(manager_module, "parse_file", waiting)
                task = asyncio.create_task(
                    service.get_file_content(s.ctx, REFERENCE, ContentSelection())
                )
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                if case == "response_limit":
                    s.ctx.budget.limits = replace(s.ctx.budget.limits, max_response_bytes=1)
                elif case == "invalidated":
                    from canvas_mcp.infrastructure.files.content_runner import parse_file

                    async def invalidating(*args):
                        parsed = await parse_file(*args)
                        s.provider._invalidate()
                        return parsed

                    monkeypatch.setattr(manager_module, "parse_file", invalidating)
                expected = {
                    "malformed": FileParseError,
                    "unsupported": UnsupportedFileFormatError,
                    "association": NotFoundError,
                    "response_limit": BudgetExceededError,
                    "invalidated": AuthenticationError,
                }[case]
                with pytest.raises(expected):
                    await service.get_file_content(s.ctx, REFERENCE, ContentSelection())
            assert not list(tmp_path.iterdir())
            if case in ("unsupported", "association"):
                assert not pool.calls

    asyncio.run(run())


def test_capability_reflection_is_rejected_and_cleaned(content_stack, tmp_path):
    async def run():
        async with content_stack(("Inert reflection: " + CAPABILITY).encode(), "txt") as (
            s,
            manager,
            service,
            pool,
        ):
            with pytest.raises(FileParseError) as caught:
                await service.get_file_content(s.ctx, REFERENCE, ContentSelection())
            assert CAPABILITY not in str(caught.value) + s.logs.getvalue()
            assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize("case", ["metadata", "header", "stream", "deadline"])
def test_remote_limits_leave_no_artifacts(content_stack, tmp_path, monkeypatch, case):
    async def run():
        maximum = 8 * 1024 * 1024
        body = b"a" * (maximum + 1) if case == "stream" else b"inert text"
        async with content_stack(
            body,
            "txt",
            metadata_size=maximum + 1 if case == "metadata" else 1,
            declared_length=maximum + 1 if case == "header" else None,
        ) as (s, manager, service, pool):
            if case == "deadline":
                entered = asyncio.Event()

                async def waiting(*args):
                    entered.set()
                    await asyncio.sleep(60)

                monkeypatch.setattr(manager_module, "parse_file", waiting)
                monkeypatch.setattr(manager_module, "REMOTE_FILE_TIMEOUT_SECONDS", 0.2)
            expected = FileContentTimeoutError if case == "deadline" else FileContentTooLargeError
            with pytest.raises(expected):
                await service.get_file_content(s.ctx, REFERENCE, ContentSelection())
            assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_concurrent_calls_are_serialized_with_distinct_artifacts_and_queue_cancel(
    content_stack, tmp_path, monkeypatch
):
    from canvas_mcp.infrastructure.files.content_runner import parse_file

    names = []
    active = peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def recording(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        directories = list(tmp_path.iterdir())
        assert len(directories) == 1
        names.append(directories[0].name)
        entered.set()
        await release.wait()
        try:
            return await parse_file(*args)
        finally:
            active -= 1

    monkeypatch.setattr(manager_module, "parse_file", recording)

    async def run():
        async with content_stack(calls=2) as (s, manager, service, pool):
            second_ctx = replace(s.ctx, budget=replace(s.ctx.budget), courses={})
            queued_ctx = replace(s.ctx, budget=replace(s.ctx.budget), courses={})
            first = asyncio.create_task(
                service.get_file_content(s.ctx, REFERENCE, ContentSelection())
            )
            await entered.wait()
            second = asyncio.create_task(
                service.get_file_content(second_ctx, REFERENCE, ContentSelection())
            )
            queued = asyncio.create_task(
                service.get_file_content(queued_ctx, REFERENCE, ContentSelection())
            )
            await asyncio.sleep(0.02)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert len(list(tmp_path.iterdir())) == 1
            release.set()
            results = await asyncio.gather(first, second)
            assert all(result.data.content_available for result in results)
            assert peak == 1 and len(set(names)) == 2 and len(pool.calls) == 2
            assert not list(tmp_path.iterdir())

    asyncio.run(run())
