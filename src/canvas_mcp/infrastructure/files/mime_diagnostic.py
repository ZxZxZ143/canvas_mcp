"""Local operator quarantine workflow, deliberately outside FileDownloads/MCP."""

import asyncio
import time
from dataclasses import asdict

from canvas_mcp.domain.errors import (
    ApplicationError,
    ArtifactUnavailableError,
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    DownloadTimeoutError,
    StorageError,
)
from canvas_mcp.domain.file_validation import file_reference
from canvas_mcp.domain.mime import DiagnosticStatus, ExpectedFormat, MimeScope, QuarantinedFile
from canvas_mcp.domain.models import FileReference, RequestContext
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES, identify
from canvas_mcp.infrastructure.files.download import DownloadRouteVariant
from canvas_mcp.infrastructure.files.capability import DownloadCapability
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.mime_observation import MimeObservation, diagnostic_mime
from canvas_mcp.infrastructure.files.policy import metadata_policy
from canvas_mcp.infrastructure.files.storage import PendingFile
from canvas_mcp.infrastructure.logging.events import Event
from canvas_mcp.ports.mime import MimeCompatibilityRegistry


class MimeDiagnostic:
    def __init__(
        self,
        manager: FileDownloadManager,
        registry: MimeCompatibilityRegistry | None,
        *,
        application_profile: str = "personal",
    ) -> None:
        if not application_profile or len(application_profile) > 128:
            raise ConfigurationError()
        self._manager, self._registry, self._profile = manager, registry, application_profile
        self.report: dict[str, object] | None = None
        self.capability_received = False

    async def probe(
        self,
        ctx: RequestContext,
        reference: FileReference,
        *,
        allow_mismatch: bool,
        remember: bool = False,
        headers_only: bool = False,
        route_variant: DownloadRouteVariant = DownloadRouteVariant.CURRENT,
        public_url: bool = False,
    ) -> dict[str, object]:
        self.report = None
        self.capability_received = False
        if (
            allow_mismatch is not True
            or type(remember) is not bool
            or type(headers_only) is not bool
            or (headers_only and remember)
            or (not headers_only and self._registry is None)
            or type(route_variant) is not DownloadRouteVariant
            or type(public_url) is not bool
            or (public_url and route_variant is not DownloadRouteVariant.CURRENT)
            or (
                route_variant is DownloadRouteVariant.COURSE_FORCED
                and reference.source_kind == "submission_attachment"
            )
        ):
            raise ConfigurationError()
        file_reference(reference)
        manager = self._manager
        if manager._closed:
            raise ArtifactUnavailableError()
        if manager._settings.max_download_bytes > MAX_PROBE_BYTES:
            raise ConfigurationError()
        task = asyncio.current_task()
        assert task is not None
        manager._tasks.add(task)
        pending: PendingFile | None = None
        observation: MimeObservation | None = None
        error: ApplicationError | None = None
        cancelled = False
        expected: ExpectedFormat | None = None
        canvas_mime: str | None = None
        evidence = None
        quarantine = None
        capability: DownloadCapability | None = None
        deadline = min(
            ctx.budget.monotonic_deadline,
            time.monotonic() + manager._settings.download_timeout_seconds,
        )
        try:
            async with asyncio.timeout_at(deadline):
                if public_url:
                    metadata, capability = await manager._provider.resolve_file_capability(
                        ctx, reference
                    )
                    self.capability_received = True
                else:
                    metadata = await manager._provider.get_file_metadata(ctx, reference)
                metadata_policy(metadata, MAX_PROBE_BYTES)
                assert metadata.filename.value is not None
                expected = ExpectedFormat(metadata.filename.value.text.rsplit(".", 1)[-1].lower())
                subject = await manager._provider._ready(ctx)
                scope = MimeScope(manager._settings.canvas_origin, self._profile, subject)
                token = await manager._client._api._access_token(
                    ctx, manager._settings.request_timeout_seconds
                )
                if token.value in str(manager._settings.download_directory):
                    raise ConfigurationError()
                if metadata.content_type.value is not None:
                    canvas_mime = diagnostic_mime(metadata.content_type.value.text, token.value)
                observation = MimeObservation(
                    scope, expected, self._registry, allow_mismatch=True, headers_only=headers_only
                )
                if not headers_only:
                    pending = manager._store.begin(ctx.scope)
                _, redirects, _ = await manager._provider._call(
                    ctx,
                    lambda: manager._client.stream_diagnostic(
                        ctx,
                        metadata,
                        manager._store,
                        pending,
                        observation,
                        route_variant=route_variant,
                        capability=capability,
                    ),
                )
                manager._provider._authorize(ctx)
                if not headers_only:
                    assert observation.http_mime is not None and pending is not None
                    quarantine = QuarantinedFile(
                        pending.identity,
                        pending.count,
                        pending.digest.hexdigest(),
                        expected,
                        canvas_mime,
                        observation.http_mime,
                        redirects,
                    )
                    # Only reached after *every* non-MIME streaming check succeeds.
                    content = manager._store.inspect_pending(pending, MAX_PROBE_BYTES)
                    evidence = identify(content, expected)
                    del content
                if time.monotonic() >= deadline:
                    raise DownloadTimeoutError()
        except asyncio.CancelledError:
            cancelled = True
        except TimeoutError:
            error = DownloadTimeoutError()
        except ApplicationError as exc:
            error = exc.sanitized()
        except Exception:
            error = StorageError()
        finally:
            try:
                if pending is not None:
                    manager._store.abort(pending)
            except Exception:
                error = StorageError()
            manager._tasks.discard(task)
        hops = [asdict(hop) for hop in observation.hops] if observation else []
        if headers_only:
            self.report = {
                "mode": "redirect_topology",
                "redirect_hops": hops,
                "result": "REJECTED" if error or cancelled else "HEADERS_ONLY",
                "reason": error.diagnostic_code if error else "cancelled" if cancelled else None,
                "state": "not_downloaded",
            }
            if error is not None:
                raise error
            if cancelled:
                raise asyncio.CancelledError()
            return self.report
        # Erasure must succeed before reporting validation or learning anything.
        if observation is not None and observation.mismatch:
            manager._log.emit(Event.MIME_MISMATCH, ctx.request_id, "files.mime_diagnostic")
        if error is not None or cancelled:
            if observation is not None and observation.ticket is not None:
                manager._log.emit(Event.MIME_DISABLED, ctx.request_id, "files.mime_diagnostic")
            unsafe = (
                isinstance(error, DownloadRejectedError)
                and error.reason is DownloadRejectionReason.UNSAFE_PREFIX
            )
            self.report = {
                "extension_class": expected.value if expected else None,
                "canvas_mime": canvas_mime,
                "http_mime": observation.http_mime if observation else None,
                "detected_format": "unsafe" if unsafe else "not_inspected",
                "validation_method": "stream_security_rejection",
                "result": DiagnosticStatus.UNSAFE.value
                if unsafe
                else DiagnosticStatus.INVALID.value,
                "size": None,
                "sha256": None,
                "redirect_count": None,
                "mime_mismatch": bool(observation and observation.mismatch),
                "registry_action": "disabled"
                if observation and observation.ticket
                else "not_recorded",
                "reason": error.diagnostic_code if error else "cancelled",
                "trust": "untrusted",
                "state": "erased" if not isinstance(error, StorageError) else "cleanup_unconfirmed",
                "redirect_hops": hops,
            }
            if error is not None:
                raise error
            raise asyncio.CancelledError()
        assert quarantine is not None and evidence is not None and observation is not None
        # No awaits: authorization cannot be invalidated between final check,
        # erasure, deadline check and this local registry transaction.
        manager._provider._authorize(ctx)
        if time.monotonic() >= deadline:
            raise DownloadTimeoutError()
        action = "not_applicable"
        if observation.mismatch:
            assert self._registry is not None
            action = self._registry.record_validated(
                observation.scope,
                quarantine.http_mime,
                evidence,
                remember=remember,
                ticket=observation.ticket,
            )
        self.report = {
            "extension_class": quarantine.expected_format.value,
            "canvas_mime": quarantine.canvas_mime,
            "http_mime": quarantine.http_mime,
            "detected_format": evidence.detected_format,
            "validation_method": evidence.validation_method,
            "result": evidence.result.value,
            "size": quarantine.size,
            "sha256": quarantine.sha256,
            "redirect_count": quarantine.redirect_count,
            "mime_mismatch": observation.mismatch,
            "registry_action": action,
            "reason": None,
            "trust": "untrusted",
            "state": "erased",
            "redirect_hops": hops,
        }
        manager._log.emit(
            Event.MIME_VALIDATED if evidence.learnable else Event.FAILED,
            ctx.request_id,
            "files.mime_diagnostic",
            reason=None if evidence.learnable else "download_rejected",
        )
        event = {"learned": Event.MIME_LEARNED, "reconfirmed": Event.MIME_RECONFIRMED}.get(action)
        if event is not None:
            manager._log.emit(event, ctx.request_id, "files.mime_diagnostic")
        elif observation.ticket is not None and not evidence.learnable:
            manager._log.emit(Event.MIME_DISABLED, ctx.request_id, "files.mime_diagnostic")
        return self.report
