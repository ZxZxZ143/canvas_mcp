"""HTTP-only authentication, resource bounds and safe observability. No Canvas logic."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TextIO
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from canvas_mcp.infrastructure.config.remote import RemoteSettings
from canvas_mcp.mcp.identity import (
    McpAuthenticator,
    McpPrincipal,
    OAuthRejection,
    current_principal,
    current_failures,
)
from canvas_mcp.infrastructure.config.oauth import OAuthSettings
from canvas_mcp.domain.file_content import REMOTE_ARTIFACT_MAX_BYTES
from canvas_mcp.domain.reflection import reflects_secrets


def _validate_original(output: object, secrets: tuple[str, ...]) -> None:
    """Inspect bounded inert bytes before transport; credentials never reach parsers."""
    if not isinstance(output, dict) or not isinstance(output.get("result"), dict):
        return
    result = output["result"]
    meta = result.get("_meta", {})
    if not isinstance(meta, dict):
        raise ValueError()
    artifact = meta.get("canvasArtifact")
    if artifact is None:
        return
    if not isinstance(artifact, dict):
        raise ValueError()
    encoded, size, digest = artifact.get("base64"), artifact.get("size"), artifact.get("sha256")
    if (
        not isinstance(encoded, str)
        or len(encoded) > ((REMOTE_ARTIFACT_MAX_BYTES + 2) // 3) * 4
        or type(size) is not int
        or not 0 < size <= REMOTE_ARTIFACT_MAX_BYTES
        or not isinstance(digest, str)
    ):
        raise ValueError()
    original = base64.b64decode(encoded, validate=True)
    if len(original) != size or hashlib.sha256(original).hexdigest() != digest:
        raise ValueError()
    if reflects_secrets(original, secrets):
        raise ValueError()


@dataclass(frozen=True, repr=False)
class DevelopmentAuthenticator:
    token: str = field(repr=False)
    principal: McpPrincipal

    async def authenticate(self, authorization: str | None) -> McpPrincipal | None:
        expected = f"Bearer {self.token}"
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected.encode()
        ):
            return None
        return self.principal


class DenyAuthenticator:
    async def authenticate(self, authorization: str | None) -> McpPrincipal | None:
        return None


class _SilentRequestLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK validation/traces can echo private JSON input. Our HTTP event below
        # is the sole request log. The filter acts in SDK child tasks as well.
        return current_principal.get() is None


def protect_sdk_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(item, _SilentRequestLogs) for item in root.filters):
        root.addFilter(_SilentRequestLogs())
    for name in (
        "mcp",
        "mcp.server",
        "mcp.server.lowlevel.server",
        "mcp.server.streamable_http",
        "mcp.server.streamable_http_manager",
        "mcp.server.fastmcp",
        "mcp.server.fastmcp.tools.tool_manager",
        "mcp.shared.session",
        "mcp.server.transport_security",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _SilentRequestLogs) for item in logger.filters):
            logger.addFilter(_SilentRequestLogs())
    # Filter at the emitting loggers as well: hosting code may add root handlers
    # after app construction. Ancestor logger filters do not cover propagation.
    for name, entry in list(logging.Logger.manager.loggerDict.items()):
        if name.startswith("mcp.") and isinstance(entry, logging.Logger):
            if not any(isinstance(item, _SilentRequestLogs) for item in entry.filters):
                entry.addFilter(_SilentRequestLogs())
    # Ancestor logger filters do not filter propagated child records.
    for handler in root.handlers:
        if not any(isinstance(item, _SilentRequestLogs) for item in handler.filters):
            handler.addFilter(_SilentRequestLogs())


class HttpBoundary:
    """Pure ASGI middleware: authenticate before SDK processing, bound the whole call.

    Stateless JSON responses are buffered until complete, so timeout/SDK failures
    can be replaced safely before headers leave the server. No BaseHTTPMiddleware
    task boundary, server-held protocol sessions or broad CORS middleware.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: RemoteSettings,
        authenticator: McpAuthenticator,
        tool_names: frozenset[str],
        log_stream: TextIO,
        secrets: tuple[str, ...] = (),
        oauth: OAuthSettings | None = None,
    ) -> None:
        self.app, self.settings, self.authenticator = app, settings, authenticator
        self.tool_names, self.log_stream = tool_names, log_stream
        self.secrets = tuple(value for value in secrets if value)
        self._active = 0
        self.oauth = oauth
        # One authorized principal; no attacker-controlled identity/IP dictionary.
        self._tokens = 30.0
        self._refilled = time.monotonic()
        self._tool_tokens = 15.0
        self._tool_refilled = time.monotonic()

    def _limit(self, *, tool: bool = False) -> bool:
        now = time.monotonic()
        if tool:
            self._tool_tokens = min(15.0, self._tool_tokens + now - self._tool_refilled)
            self._tool_refilled = now
            if self._tool_tokens < 1:
                return False
            self._tool_tokens -= 1
        else:
            self._tokens = min(30.0, self._tokens + 2 * (now - self._refilled))
            self._refilled = now
            if self._tokens < 1:
                return False
            self._tokens -= 1
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id, started = str(uuid4()), time.monotonic()
        status, code, operation, tool = 500, "internal_error", "http", None

        def event(name: str) -> None:
            self.log_stream.write(
                json.dumps({"event": name, "request_id": request_id, "tool": tool}) + "\n"
            )

        async def reject(value: int, reason: str, challenge: str | None = None) -> None:
            nonlocal status, code
            status, code = value, reason
            response = JSONResponse(
                {"error": reason},
                status_code=value,
                headers={
                    "Cache-Control": "no-store",
                    "X-Request-Id": request_id,
                    **({"WWW-Authenticate": challenge} if challenge else {}),
                    **({"Retry-After": "1"} if value == 429 else {}),
                },
            )
            await response(scope, receive, send)

        principal_token = None
        failures: list[str] = []
        failure_token = current_failures.set(failures)
        authorization: list[bytes] = []
        try:
            headers = scope.get("headers", [])
            if (
                sum(len(k) + len(v) + 4 for k, v in headers) > self.settings.max_header_bytes
                or len(headers) > 100
            ):
                await reject(431, "headers_too_large")
                return
            if self._active >= self.settings.max_concurrency:
                await reject(503, "capacity_exceeded")
                return
            self._active += 1
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    public_paths: tuple[str, ...] = ("/health",)
                    if self.oauth:
                        public_paths += (
                            "/.well-known/oauth-protected-resource",
                            "/.well-known/oauth-protected-resource/mcp",
                        )
                        if scope["path"] != "/health":
                            host = [v.decode("latin1") for k, v in headers if k.lower() == b"host"]
                            origin = [
                                v.decode("latin1") for k, v in headers if k.lower() == b"origin"
                            ]
                            authority = self.oauth.authority
                            if host not in ([authority], [authority + ":443"]) or (
                                origin and origin != [self.oauth.public_base_url]
                            ):
                                await reject(421, "invalid_authority")
                                return
                            if not self._limit():
                                await reject(429, "request_rate_limited")
                                return
                    if scope["path"] not in public_paths:
                        if self.oauth and scope["path"] != "/mcp":
                            await reject(404, "not_found")
                            return
                        authorization = [v for k, v in headers if k.lower() == b"authorization"]
                        try:
                            principal = await self.authenticator.authenticate(
                                authorization[0].decode("latin1")
                                if len(authorization) == 1
                                else None
                            )
                        except OAuthRejection as rejection:
                            event(
                                "oauth_authorization_denied"
                                if rejection.status == 403
                                else "oauth_validation_failure"
                            )
                            await reject(rejection.status, rejection.code, rejection.challenge)
                            return
                        if principal is None:
                            await reject(401, "authentication_required")
                            return
                        principal_token = current_principal.set(principal)
                        if self.oauth:
                            event("oauth_validation_success")
                    lengths = [v for k, v in headers if k.lower() == b"content-length"]
                    if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                        await reject(400, "invalid_request")
                        return
                    if lengths and int(lengths[0]) > self.settings.max_body_bytes:
                        await reject(413, "body_too_large")
                        return
                    body = bytearray()
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            status, code = 499, "client_disconnected"
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > self.settings.max_body_bytes:
                            await reject(413, "body_too_large")
                            return
                        if not message.get("more_body", False):
                            break
                    try:
                        payload = json.loads(body)
                        if isinstance(payload, dict):
                            method = payload.get("method")
                            if method in (
                                "initialize",
                                "notifications/initialized",
                                "tools/list",
                                "tools/call",
                                "ping",
                            ):
                                operation = method
                            params = payload.get("params")
                            if (
                                operation == "tools/call"
                                and isinstance(params, dict)
                                and params.get("name") in self.tool_names
                            ):
                                tool = params["name"]
                    except (ValueError, TypeError):
                        pass
                    if self.oauth and operation == "initialize":
                        event("mcp_initialize")
                    if self.oauth and operation == "tools/call":
                        if not self._limit(tool=True):
                            await reject(429, "tool_rate_limited")
                            return
                        event("tool_call_started")
                    delivered = False
                    disconnected = asyncio.Event()

                    async def replay() -> Message:
                        nonlocal delivered
                        if not delivered:
                            delivered = True
                            return {"type": "http.request", "body": bytes(body), "more_body": False}
                        # The SDK may monitor disconnect in a background task.
                        # Keep it pending while the request response is generated.
                        await disconnected.wait()
                        return {"type": "http.disconnect"}

                    messages: list[Message] = []
                    response_size = 0

                    async def capture(message: Message) -> None:
                        nonlocal response_size
                        response_size += len(message.get("body", b""))
                        # Original bytes are widget-only metadata, with their own
                        # exact wire cap. Keep the existing cap for every other call.
                        maximum = (
                            5_700_000
                            if operation == "tools/call" and tool == "canvas_download_file"
                            else 262_144
                        )
                        if response_size > maximum:
                            raise ValueError()
                        messages.append(message)

                    await self.app(scope, replay, capture)
                    raw = b"".join(message.get("body", b"") for message in messages)
                    if any(secret.encode() in raw for secret in self.secrets):
                        await reject(500, "internal_error")
                        return
                    # Never reflect the presented OAuth credential, even through coursework.
                    if self.oauth and authorization and authorization[0].split(b" ", 1)[-1] in raw:
                        await reject(500, "internal_error")
                        return
                    status = next(
                        (
                            message["status"]
                            for message in messages
                            if message["type"] == "http.response.start"
                        ),
                        500,
                    )
                    if status >= 400:
                        await reject(status, "invalid_mcp_request")
                        return
                    # Protocol errors may otherwise include Pydantic input echoes.
                    try:
                        output = json.loads(raw)
                        if isinstance(output, dict) and "error" in output:
                            error = output["error"]
                            if isinstance(error, dict):
                                output["error"] = {
                                    "code": error.get("code", -32603),
                                    "message": "Invalid MCP request.",
                                }
                                raw = json.dumps(output).encode()
                                messages = [
                                    {
                                        "type": "http.response.start",
                                        "status": status,
                                        "headers": [(b"content-type", b"application/json")],
                                    },
                                    {"type": "http.response.body", "body": raw},
                                ]
                    except (ValueError, TypeError):
                        pass
                    if operation == "tools/call" and tool == "canvas_download_file":
                        presented = (
                            authorization[0].split(b" ", 1)[-1].decode("latin1")
                            if self.oauth and authorization
                            else ""
                        )
                        _validate_original(json.loads(raw), (*self.secrets, presented))
                    for message in messages:
                        if message["type"] == "http.response.start":
                            message["headers"] = [
                                *message.get("headers", []),
                                (b"cache-control", b"no-store"),
                                (b"x-request-id", request_id.encode()),
                            ]
                        await send(message)
                    code = "ok"
                    if self.oauth and operation == "tools/call":
                        event("tool_call_completed")
                        if any(
                            failure
                            in (
                                "authentication_error",
                                "upstream_unavailable",
                                "rate_limited",
                                "malformed_upstream",
                            )
                            for failure in failures
                        ):
                            event("canvas_upstream_error")
            finally:
                self._active -= 1
        except TimeoutError:
            await reject(504, "request_timeout")
        except Exception:
            # Deliberately suppress exception strings, traceback and private input.
            await reject(500, "internal_error")
        finally:
            if principal_token is not None:
                current_principal.reset(principal_token)
            current_failures.reset(failure_token)
            self.log_stream.write(
                json.dumps(
                    {
                        "event": "mcp_http_request",
                        "request_id": request_id,
                        "operation": operation,
                        "tool": tool,
                        "status": status,
                        "code": code,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
                + "\n"
            )
            self.log_stream.flush()
