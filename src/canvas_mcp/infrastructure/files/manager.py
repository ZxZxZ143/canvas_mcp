"""Authorization + network + managed artifact lifecycle; no public URL/path input."""

import asyncio
import time

from canvas_mcp.domain.errors import (
    ApplicationError,
    ArtifactUnavailableError,
    ConfigurationError,
    DownloadTimeoutError,
    StorageError,
)
from canvas_mcp.domain.file_validation import file_reference
from canvas_mcp.domain.models import ArtifactId, DownloadedFile, FileReference, RequestContext
from canvas_mcp.infrastructure.canvas.provider import CanvasProvider
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.download import CanvasDownloadClient
from canvas_mcp.infrastructure.files.policy import metadata_policy
from canvas_mcp.infrastructure.files.storage import ManagedStore, PendingFile
from canvas_mcp.infrastructure.logging.events import Event, EventLogger


class FileDownloadManager:
    def __init__(
        self,
        provider: CanvasProvider,
        client: CanvasDownloadClient,
        store: ManagedStore,
        settings: DeploymentSettings,
        logger: EventLogger,
    ) -> None:
        self._provider, self._client, self._store = provider, client, store
        self._settings, self._log = settings, logger
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def download_file(self, ctx: RequestContext, reference: FileReference) -> DownloadedFile:
        file_reference(reference)
        if self._closed:
            raise ArtifactUnavailableError()
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        pending: PendingFile | None = None
        error: ApplicationError | None = None
        cancelled = False
        result: DownloadedFile | None = None
        self._log.emit(Event.STARTED, ctx.request_id, "files.download")
        try:
            remaining = min(
                self._settings.download_timeout_seconds,
                ctx.budget.monotonic_deadline - time.monotonic(),
            )
            deadline = time.monotonic() + remaining
            async with asyncio.timeout(max(0, remaining)):
                metadata, capability = await self._provider.resolve_file_capability(ctx, reference)
                metadata_policy(metadata, self._settings.max_download_bytes)
                # Reject secret-bearing operator configuration before creating
                # directories; metadata URLs are never consulted.
                token = await self._client._api._access_token(
                    ctx, self._settings.request_timeout_seconds
                )
                if token.value in str(self._settings.download_directory):
                    raise ConfigurationError()
                pending = self._store.begin(ctx.scope)
                media, redirects, classification = await self._provider._call(
                    ctx,
                    lambda: self._client.stream(ctx, metadata, capability, self._store, pending),
                )
                self._store.validate_pending_archive(pending, metadata)
                # Synchronous finish cannot race with connection invalidation on this event loop.
                result = self._store.finish(pending, metadata, media, redirects, classification)
                if time.monotonic() >= deadline:
                    self._store.cleanup(ctx.scope, result.artifact_id)
                    result = None
                    raise DownloadTimeoutError()
                self._log.emit(
                    Event.COMPLETED,
                    ctx.request_id,
                    "files.download",
                    bytes_written=result.size,
                    redirect_count=redirects,
                )
        except asyncio.CancelledError:
            cancelled = True
        except TimeoutError:
            error = DownloadTimeoutError()
        except ApplicationError as exc:
            error = exc.sanitized()
        except Exception:
            error = StorageError()
        finally:
            if result is None and pending is not None:
                try:
                    self._store.abort(pending)
                except Exception:
                    error = StorageError()
            self._tasks.discard(task)
        if error is not None:
            self._log.emit(
                Event.FAILED,
                ctx.request_id,
                "files.download",
                reason=error.code,
                # Completed temporary writes, not published/usable bytes. Abort
                # preserves this count; the old omitted field misleadingly read 0.
                bytes_written=pending.count if pending is not None else 0,
            )
            raise error
        if cancelled:
            raise asyncio.CancelledError()
        assert result is not None
        return result

    async def resolve_download(
        self, ctx: RequestContext, artifact_id: ArtifactId
    ) -> DownloadedFile:
        error: ApplicationError
        try:
            descriptor = self._store.get(ctx.scope, artifact_id, verify=False)
            metadata = await self._provider.get_file_metadata(ctx, descriptor.source)
            metadata_policy(metadata, self._settings.max_download_bytes)
            self._provider._authorize(ctx)
            return self._store.get(ctx.scope, artifact_id)
        except ApplicationError as exc:
            error = exc.sanitized()
        except Exception:
            error = StorageError()
        raise error

    async def cleanup_download(self, ctx: RequestContext, artifact_id: ArtifactId) -> None:
        error: ApplicationError | None = None
        try:
            self._store.cleanup(ctx.scope, artifact_id)
            self._log.emit(Event.COMPLETED, ctx.request_id, "files.cleanup")
        except ApplicationError as exc:
            error = exc.sanitized()
        except Exception:
            error = StorageError()
        if error is not None:
            raise error

    async def aclose(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            self._store.close()
        finally:
            await self._client.aclose()
