"""Fresh authorization, existing anonymous downloader, worker, unconditional cleanup."""

import asyncio
import hashlib
import os
import time
from html import unescape
from urllib.parse import unquote
from dataclasses import replace

from canvas_mcp.domain.errors import (
    ApplicationError,
    DownloadTimeoutError,
    DownloadRejectedError,
    DownloadRejectionReason,
    FileContentTimeoutError,
    FileContentTooLargeError,
    FileContentUnavailableError,
    FileTooLargeError,
    FileParseError,
    StorageError,
    UnsupportedFileFormatError,
    ValidationError,
)
from canvas_mcp.domain.file_content import (
    ContentSelection,
    ContentUnit,
    FileContent,
    REMOTE_FILE_TIMEOUT_SECONDS,
    REMOTE_INSPECTION_MAX_BYTES,
    SUPPORTED_FORMATS,
    REMOTE_ARTIFACT_MAX_BYTES,
)
from canvas_mcp.domain.remote_artifact import RemoteArtifact
from canvas_mcp.domain.file_validation import file_reference
from canvas_mcp.domain.models import FileReference, RequestContext
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.canvas.provider import CanvasProvider
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.content_runner import parse_file
from canvas_mcp.infrastructure.files.download import CanvasDownloadClient
from canvas_mcp.infrastructure.files.ephemeral import EphemeralStore
from canvas_mcp.infrastructure.files.policy import metadata_policy, MEDIA, reflects_capability
from canvas_mcp.infrastructure.logging.events import EventLogger


class RemoteFileContentManager:
    def __init__(
        self,
        provider: CanvasProvider,
        api: CanvasHttpClient,
        settings: DeploymentSettings,
        logger: EventLogger,
    ) -> None:
        self._provider = provider
        self._settings = replace(
            settings,
            max_download_bytes=min(settings.max_download_bytes, REMOTE_INSPECTION_MAX_BYTES),
            download_timeout_seconds=min(
                settings.download_timeout_seconds, REMOTE_FILE_TIMEOUT_SECONDS
            ),
        )
        self._client = CanvasDownloadClient(self._settings, api, logger)
        self._gate = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def download_original(
        self, ctx: RequestContext, reference: FileReference
    ) -> RemoteArtifact:
        file_reference(reference)
        if self._closed:
            raise FileContentUnavailableError()
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        store = None
        ctx.budget.monotonic_deadline = min(
            ctx.budget.monotonic_deadline, time.monotonic() + REMOTE_FILE_TIMEOUT_SECONDS
        )
        try:
            async with asyncio.timeout(max(0, ctx.budget.monotonic_deadline - time.monotonic())):
                async with self._gate:
                    metadata, capability = await self._provider.resolve_file_capability(
                        ctx, reference
                    )
                    name = metadata.filename.value
                    fmt = name.text.rsplit(".", 1)[-1].lower() if name else ""
                    if fmt not in SUPPORTED_FORMATS:
                        raise UnsupportedFileFormatError()
                    metadata_policy(metadata, REMOTE_ARTIFACT_MAX_BYTES)
                    store = EphemeralStore(REMOTE_ARTIFACT_MAX_BYTES)
                    pending = store.begin()
                    await self._provider._call(
                        ctx, lambda: self._client.stream(ctx, metadata, capability, store, pending)
                    )
                    store.validate(pending, fmt)
                    fd = store.reader(pending)
                    pieces = []
                    count = 0
                    while chunk := os.read(fd, 65_536):
                        count += len(chunk)
                        if count > REMOTE_ARTIFACT_MAX_BYTES:
                            raise FileContentTooLargeError()
                        pieces.append(chunk)
                    data = b"".join(pieces)
                    if (
                        not data
                        or count != pending.count
                        or hashlib.sha256(data).hexdigest() != pending.digest.hexdigest()
                    ):
                        raise FileParseError()
                    if reflects_capability(data, capability.target):
                        raise FileParseError()
                    self._provider._authorize(ctx)
                    return RemoteArtifact(
                        metadata,
                        f"canvas-file-{metadata.id}.{fmt}",
                        MEDIA[fmt],
                        pending.digest.hexdigest(),
                        data,
                    )
        except (TimeoutError, DownloadTimeoutError):
            raise FileContentTimeoutError() from None
        except FileTooLargeError:
            raise FileContentTooLargeError() from None
        except ApplicationError as error:
            raise error.sanitized() from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise StorageError() from None
        finally:
            try:
                if store is not None:
                    store.close()
            finally:
                self._tasks.discard(task)

    async def get_file_content(
        self, ctx: RequestContext, reference: FileReference, selection: ContentSelection
    ) -> FileContent:
        file_reference(reference)
        selection.validate()
        if self._closed:
            raise FileContentUnavailableError()
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        ctx.budget.monotonic_deadline = min(
            ctx.budget.monotonic_deadline, time.monotonic() + REMOTE_FILE_TIMEOUT_SECONDS
        )
        store: EphemeralStore | None = None
        try:
            async with asyncio.timeout(max(0, ctx.budget.monotonic_deadline - time.monotonic())):
                async with self._gate:
                    metadata, capability = await self._provider.resolve_file_capability(
                        ctx, reference
                    )
                    name = metadata.filename.value
                    fmt = name.text.rsplit(".", 1)[-1].lower() if name else ""
                    if fmt not in SUPPORTED_FORMATS:
                        raise UnsupportedFileFormatError()
                    if fmt not in ("pdf", "pptx") and selection != ContentSelection():
                        raise ValidationError()
                    metadata_policy(metadata, self._settings.max_download_bytes)
                    store = EphemeralStore(self._settings.max_download_bytes)
                    pending = store.begin()
                    await self._provider._call(
                        ctx, lambda: self._client.stream(ctx, metadata, capability, store, pending)
                    )
                    store.validate(pending, fmt)
                    parsed = await parse_file(
                        store.reader(pending),
                        pending.count,
                        pending.digest.hexdigest(),
                        fmt,
                        selection,
                    )
                    # Another concurrent call may have invalidated the Canvas credential.
                    self._provider._authorize(ctx)
                    # A gateway/document may reflect this per-call secret URL.
                    # Compare only this capability, never censor ordinary links.
                    reflected = unquote(unescape(capability.target))
                    query = reflected.partition("?")[2]
                    for unit in parsed["units"]:
                        visible = unquote(unescape(unit["text"].replace("\\/", "/")))
                        if reflected in visible or query and query in visible:
                            raise FileParseError()
                    return FileContent(
                        metadata,
                        fmt,
                        pending.count,
                        pending.digest.hexdigest(),
                        tuple(ContentUnit(**unit) for unit in parsed["units"]),
                        parsed["truncated"],
                        parsed["content_available"],
                        parsed["reason"],
                        parsed["total_units"],
                        parsed["start_page"],
                        parsed["end_page"],
                        tuple(parsed["omissions"]),
                        parsed["extraction_mode"],
                        tuple(parsed["ocr_pages"]),
                        parsed["page_count_processed"],
                        tuple(parsed["native_pages"]),
                        tuple(parsed["limitations"]),
                    )
        except (TimeoutError, DownloadTimeoutError):
            raise FileContentTimeoutError() from None
        except FileTooLargeError:
            raise FileContentTooLargeError() from None
        except DownloadRejectedError as error:
            if error.reason is DownloadRejectionReason.UNSUPPORTED_EXTENSION:
                raise UnsupportedFileFormatError() from None
            raise error.sanitized() from None
        except ApplicationError as error:
            raise error.sanitized() from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise StorageError() from None
        finally:
            try:
                if store is not None:
                    store.close()
            finally:
                self._tasks.discard(task)

    async def aclose(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()
