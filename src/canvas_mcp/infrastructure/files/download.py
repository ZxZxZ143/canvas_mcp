"""Deliberate GET redirects, pinned DNS, streaming and one download attempt."""

import asyncio
import math
import time
from collections.abc import Iterable
from enum import Enum
from typing import TypeVar
from urllib.parse import urlsplit

import httpcore

from canvas_mcp.domain.errors import (
    ApplicationError,
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    DownloadTimeoutError,
    FileTooLargeError,
    RequestBudgetExceededError,
    UnsafeRedirectError,
    UpstreamUnavailableError,
    UpstreamReason,
)
from canvas_mcp.domain.models import FileMetadata, RequestContext
from canvas_mcp.domain.mime import RedirectOriginClass
from canvas_mcp.domain.file_validation import file_reference
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.config.origin import normalize_origin, normalize_trusted_private_ips
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.policy import (
    BytePolicy,
    Classification,
    metadata_policy,
    redirect_target,
    validate_media,
)
from canvas_mcp.infrastructure.files.capability import DownloadCapability, parse_public_url
from canvas_mcp.infrastructure.files.storage import ManagedStore, PendingFile
from canvas_mcp.ports.download_sink import DownloadSink, DownloadTarget
from canvas_mcp.infrastructure.files.mime_observation import MimeObservation
from canvas_mcp.infrastructure.logging.events import (
    Event,
    EventLogger,
    SENSITIVE_HTTP,
    protect_http_logging,
)

P = TypeVar("P", bound=DownloadTarget)


class DownloadBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, origins: frozenset[str]) -> None:
        self._backends = {
            urlsplit(origin).hostname: PublicOriginBackend(origin) for origin in origins
        }

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        backend = self._backends.get(host)
        if backend is None:
            raise UnsafeRedirectError()
        return await backend.connect_tcp(host, port, timeout, local_address, socket_options)


class RedirectTrust(Enum):
    TRUSTED_CANVAS_CHAIN = "trusted_canvas_chain"
    CANVAS_CAPABILITY_CHAIN = "canvas_capability_chain"
    EXTERNAL_CHAIN = "external_chain"


class DownloadRouteVariant(Enum):
    CURRENT = "A"
    COURSE_FORCED = "B"
    GLOBAL_FORCED = "C"


def controlled_download_target(
    origin: str, metadata: FileMetadata, variant: DownloadRouteVariant
) -> str:
    """Build one of three fixed routes from validated IDs, never a metadata URL."""
    if type(variant) is not DownloadRouteVariant:
        raise ConfigurationError()
    reference = metadata.source
    file_reference(reference)
    if variant is DownloadRouteVariant.CURRENT:
        prefix = (
            ""
            if reference.source_kind == "submission_attachment"
            else "/courses/" + reference.course_id
        )
        suffix = ""
    elif variant is DownloadRouteVariant.COURSE_FORCED:
        if reference.source_kind == "submission_attachment":
            raise ConfigurationError()
        prefix = "/courses/" + reference.course_id
        suffix = "?download_frd=1"
    else:
        prefix = ""
        suffix = "?download_frd=1"
    return origin + prefix + "/files/" + reference.file_id + "/download" + suffix


class CanvasDownloadClient:
    def __init__(
        self,
        settings: DeploymentSettings,
        api: CanvasHttpClient,
        logger: EventLogger,
        *,
        _pool: httpcore.AsyncConnectionPool | None = None,
        _gate: asyncio.Semaphore | None = None,
    ) -> None:
        trusted_private_ips = normalize_trusted_private_ips(settings.trusted_private_ips)
        if (
            type(settings.allow_private_origin) is not bool
            or settings.allow_private_origin
            and not trusted_private_ips
            or not math.isfinite(settings.download_timeout_seconds)
            or not 0 < settings.download_timeout_seconds <= 300
            or not 1 <= settings.max_download_bytes <= 262_144_000
            or not 0 <= settings.max_redirects <= 3
        ):
            raise ConfigurationError()
        self._settings, self._api, self._log = settings, api, logger
        self._origin = normalize_origin(settings.canvas_origin)
        self._origins = frozenset(
            (self._origin, *(normalize_origin(o) for o in settings.download_origins))
        )
        self._capability_origins = (
            self._origin,
            *(normalize_origin(o) for o in settings.download_origins),
        )
        self._gate = _gate or api._gate
        self._pool = _pool or httpcore.AsyncConnectionPool(
            network_backend=DownloadBackend(self._origins),
            max_connections=settings.max_concurrency,
            max_keepalive_connections=0,
            retries=0,
        )
        # This pool uses only exact configured private IPs, for authenticated
        # Canvas routes and anonymous API-issued same-origin capabilities.
        # After an external hop, the public-only pool is permanent.
        self._initial_pool = (
            httpcore.AsyncConnectionPool(
                network_backend=PublicOriginBackend(
                    self._origin, trusted_private_ips=trusted_private_ips
                ),
                max_connections=settings.max_concurrency,
                max_keepalive_connections=0,
                retries=0,
            )
            if trusted_private_ips
            else None
        )
        protect_http_logging()

    async def aclose(self) -> None:
        error = False
        guard = SENSITIVE_HTTP.set(True)
        try:
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                await self._pool.aclose()
                if self._initial_pool is not None:
                    await self._initial_pool.aclose()
        except Exception:
            error = True
        finally:
            SENSITIVE_HTTP.reset(guard)
        if error:
            raise UpstreamUnavailableError()

    async def stream(
        self,
        ctx: RequestContext,
        metadata: FileMetadata,
        capability: DownloadCapability,
        store: DownloadSink[P],
        pending: P,
    ) -> tuple[str, int, Classification]:
        if type(capability) is not DownloadCapability:
            raise ConfigurationError()
        return await self._guarded_stream(ctx, metadata, store, pending, capability=capability)

    async def stream_diagnostic(
        self,
        ctx: RequestContext,
        metadata: FileMetadata,
        store: ManagedStore,
        pending: PendingFile | None,
        observation: MimeObservation,
        *,
        route_variant: DownloadRouteVariant = DownloadRouteVariant.CURRENT,
        capability: DownloadCapability | None = None,
    ) -> tuple[str, int, Classification]:
        if (
            type(observation) is not MimeObservation
            or type(route_variant) is not DownloadRouteVariant
            or (capability is not None and type(capability) is not DownloadCapability)
            or (capability is not None and route_variant is not DownloadRouteVariant.CURRENT)
        ):
            raise ConfigurationError()
        return await self._guarded_stream(
            ctx, metadata, store, pending, observation, route_variant, capability
        )

    async def _guarded_stream(
        self,
        ctx: RequestContext,
        metadata: FileMetadata,
        store: DownloadSink[P],
        pending: P | None,
        observation: MimeObservation | None = None,
        route_variant: DownloadRouteVariant = DownloadRouteVariant.CURRENT,
        capability: DownloadCapability | None = None,
    ) -> tuple[str, int, Classification]:
        error: ApplicationError
        guard = SENSITIVE_HTTP.set(True)
        try:
            return await self._stream(
                ctx, metadata, store, pending, observation, route_variant, capability
            )
        except ApplicationError as exc:
            error = exc.sanitized()
        except (TimeoutError, httpcore.TimeoutException):
            error = DownloadTimeoutError()
        except (httpcore.NetworkError, OSError):
            error = UpstreamUnavailableError(reason=UpstreamReason.CONNECTION)
        except (httpcore.ProtocolError, ValueError, UnicodeError):
            error = DownloadRejectedError(reason=DownloadRejectionReason.PROTOCOL_ERROR)
        except Exception:
            error = ApplicationError()
        finally:
            SENSITIVE_HTTP.reset(guard)
        raise error

    async def _stream(
        self,
        ctx: RequestContext,
        metadata: FileMetadata,
        store: DownloadSink[P],
        pending: P | None,
        observation: MimeObservation | None = None,
        route_variant: DownloadRouteVariant = DownloadRouteVariant.CURRENT,
        capability: DownloadCapability | None = None,
    ) -> tuple[str, int, Classification]:
        if pending is None and (observation is None or not observation.headers_only):
            raise ConfigurationError()
        expected, classification = metadata_policy(metadata, self._settings.max_download_bytes)
        token = await self._api._access_token(ctx, self._settings.request_timeout_seconds)
        if token.value in str(self._settings.download_directory):
            raise ConfigurationError()
        if capability is None:
            target = controlled_download_target(self._origin, metadata, route_variant)
        else:
            target = parse_public_url(
                {"public_url": capability.target}, token.value, self._capability_origins
            ).target
        visited = {target}
        policy = BytePolicy(classification)
        secret = token.value.encode("ascii")
        tail = b""
        if capability is None:
            trust = RedirectTrust.TRUSTED_CANVAS_CHAIN
        else:
            capability_parts = urlsplit(target)
            capability_origin = normalize_origin(
                capability_parts.scheme + "://" + capability_parts.netloc
            )
            trust = (
                RedirectTrust.CANVAS_CAPABILITY_CHAIN
                if capability_origin == self._origin
                else RedirectTrust.EXTERNAL_CHAIN
            )
        for redirects in range(self._settings.max_redirects + 1):
            remaining = ctx.budget.monotonic_deadline - time.monotonic()
            if remaining <= 0 or ctx.budget.http_attempts_used >= min(
                ctx.budget.limits.max_http_attempts, self._settings.max_aggregate_requests
            ):
                raise RequestBudgetExceededError()
            ctx.budget.http_attempts_used += 1
            timeout = min(self._settings.request_timeout_seconds, remaining)
            parsed = urlsplit(target)
            current_origin = normalize_origin(parsed.scheme + "://" + parsed.netloc)
            if current_origin not in self._origins or (
                trust
                in (
                    RedirectTrust.TRUSTED_CANVAS_CHAIN,
                    RedirectTrust.CANVAS_CAPABILITY_CHAIN,
                )
                and current_origin != self._origin
            ):
                raise UnsafeRedirectError()
            authorized = trust is RedirectTrust.TRUSTED_CANVAS_CHAIN
            headers: dict[bytes | str, bytes | str] = {
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            }
            if authorized:
                headers["Authorization"] = "Bearer " + token.value
            # HTTPCore has no cookie jar, env proxies, netrc, automatic redirect,
            # decompression or response buffering. Never restore auth after external.
            async with self._gate:
                use_initial = trust in (
                    RedirectTrust.TRUSTED_CANVAS_CHAIN,
                    RedirectTrust.CANVAS_CAPABILITY_CHAIN,
                )
                pool = self._initial_pool if use_initial and self._initial_pool else self._pool
                async with pool.stream(
                    "GET",
                    target,
                    headers=headers,
                    extensions={
                        "timeout": {key: timeout for key in ("connect", "read", "write", "pool")},
                    },
                ) as response:
                    fields = list(response.headers)
                    if observation is not None:
                        observation.record_hop(
                            redirects,
                            RedirectOriginClass.CANVAS
                            if current_origin == self._origin
                            else RedirectOriginClass.EXTERNAL,
                            response.status,
                            "Authorization" in headers,
                            fields,
                        )
                    if response.status in (301, 302, 303, 307, 308):
                        if redirects >= self._settings.max_redirects:
                            raise UnsafeRedirectError()
                        following = redirect_target(
                            target,
                            CanvasHttpClient._header(fields, b"location"),
                            self._origins,
                            token.value,
                        )
                        if following in visited:
                            raise UnsafeRedirectError()
                        parsed_next = urlsplit(following)
                        next_origin = normalize_origin(
                            parsed_next.scheme + "://" + parsed_next.netloc
                        )
                        if next_origin != self._origin:
                            trust = RedirectTrust.EXTERNAL_CHAIN
                        visited.add(following)
                        target = following
                        self._log.emit(
                            Event.REDIRECT,
                            ctx.request_id,
                            "files.download",
                            redirect_count=redirects + 1,
                        )
                        continue
                    # Anonymous failures say nothing about the Canvas credential.
                    # A 401 on any authenticated same-origin hop can revoke it.
                    if not authorized and response.status == 401:
                        raise DownloadRejectedError(
                            reason=DownloadRejectionReason.ANONYMOUS_UNAUTHORIZED
                        )
                    error = CanvasHttpClient._status_error(response.status)
                    if error is not None:
                        raise error
                    if CanvasHttpClient._header(fields, b"content-encoding").lower() not in (
                        b"",
                        b"identity",
                    ):
                        raise DownloadRejectedError(
                            reason=DownloadRejectionReason.UNSUPPORTED_ENCODING
                        )
                    declared = CanvasHttpClient._header(fields, b"content-length")
                    length = None
                    if declared:
                        if len(declared) > 12 or not declared.isdigit():
                            raise DownloadRejectedError(
                                reason=DownloadRejectionReason.INVALID_LENGTH
                            )
                        length = int(declared)
                        if length > self._settings.max_download_bytes:
                            raise FileTooLargeError()
                    raw_media = CanvasHttpClient._header(fields, b"content-type").decode("ascii")
                    media = (
                        validate_media(raw_media, expected, source="http")
                        if observation is None
                        else observation.check_http(raw_media, expected, token.value)
                    )
                    if observation is not None and observation.headers_only:
                        # Exit the response context without iterating the body.
                        # Only the local diagnostic can enter this path; it does
                        # not allocate a part file, validate bytes or publish.
                        return media, redirects, classification
                    assert pending is not None
                    async for chunk in response.aiter_stream():
                        # HTTPCore bounds socket reads; subdivide test/custom transports too.
                        for offset in range(0, len(chunk), 65536):
                            data = chunk[offset : offset + 65536]
                            combined = tail + data
                            if secret in combined:
                                raise DownloadRejectedError(
                                    reason=DownloadRejectionReason.CREDENTIAL_REFLECTION
                                )
                            tail = combined[-(len(secret) - 1) :] if len(secret) > 1 else b""
                            policy.update(data)
                            store.write(pending, data)
                            await asyncio.sleep(0)  # cancellation/deadline even with ready buffers
                    if length is not None and pending.count != length:
                        raise DownloadRejectedError(reason=DownloadRejectionReason.LENGTH_MISMATCH)
                    policy.finish()
                    return media, redirects, classification
        raise UnsafeRedirectError()
