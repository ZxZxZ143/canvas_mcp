"""Developer-only fixed-endpoint observations. Never used by production requests.

Default-DNS modes preflight DNS and check the connected peer before HTTP secrets
are sent. They are NOT equivalent to production pre-connect DNS pinning: a DNS
race can cause an unauthenticated TCP connection before the peer check rejects it.
"""

import asyncio
import http.client
import json
import socket
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, NoReturn
from urllib.parse import urlsplit

import httpcore

from canvas_mcp.infrastructure.canvas.client import PROFILE_PATH
from canvas_mcp.infrastructure.config.origin import (
    canonical_resolved_address,
    configured_origin_address_allowed,
    is_internal_service_address,
    is_public_address,
    normalize_origin,
    normalize_trusted_private_ips,
)
from canvas_mcp.ports.credentials import AccessToken

MAX_EVIDENCE = 8192


class Mode(str, Enum):
    BASELINE = "baseline_python"
    LIBRARY = "library_default"
    HEADERS = "production_headers"
    TRANSPORT = "production_transport"
    CLIENT = "canvas_client"
    UA_PYTHON = "ua_python_urllib"
    UA_POWERSHELL = "ua_powershell_style"
    UA_APPLICATION = "ua_application"


class ProbeBlocked(Exception):
    pass


@dataclass
class Report:
    mode: Mode
    status: int | None = None
    host_correct: bool | None = None
    sni_correct: bool | None = None
    auth_present: bool | None = None
    tls_verified: bool | None = None
    request_correct: bool | None = None
    accept_json: bool | None = None
    accept_encoding: str = "unknown"
    content_type_present: bool | None = None
    transport: str = "system_dns"
    tcp_destination: str = "unknown"
    http_version: str = "unknown"
    user_agent: str = "unknown"
    proxy_used: bool = False
    redirects_followed: bool = False
    content_class: str = "other"
    body_category: str = "unknown"
    failure: str = "none"

    def safe_data(self) -> dict[str, object]:
        """Reject accidental raw/freeform data before console/worker projection."""
        if not isinstance(self.mode, Mode):
            raise ProbeBlocked()
        if self.status is not None and (
            type(self.status) is not int or not 100 <= self.status <= 599
        ):
            raise ProbeBlocked()
        for value in (
            self.host_correct,
            self.sni_correct,
            self.auth_present,
            self.tls_verified,
            self.request_correct,
            self.accept_json,
            self.content_type_present,
        ):
            if value is not None and type(value) is not bool:
                raise ProbeBlocked()
        if self.proxy_used is not False or self.redirects_followed is not False:
            raise ProbeBlocked()
        for label, allowed in (
            (self.transport, ("system_dns", "vetted_ip")),
            (self.tcp_destination, ("unknown", "public_ip", "internal_ip", "rejected")),
            (self.http_version, ("unknown", "HTTP/1.1", "HTTP/1.0", "HTTP/2")),
            (self.accept_encoding, ("unknown", "absent", "identity", "other")),
            (
                self.user_agent,
                ("unknown", "absent", "python_urllib", "powershell_style", "application", "other"),
            ),
            (self.content_class, ("html", "json", "binary", "other")),
            (
                self.body_category,
                ("canvas_json_error", "html_gateway_error", "html_waf_error", "unknown"),
            ),
            (
                self.failure,
                (
                    "none",
                    "dns_rejected",
                    "wire_rejected",
                    "timeout",
                    "request_failed",
                    "client_rejected",
                    "worker_failed",
                ),
            ),
        ):
            if type(label) is not str or label not in allowed:
                raise ProbeBlocked()
        return asdict(self)


def public_records(records: list[Any], trusted_private_ips: tuple[str, ...] = ()) -> bool:
    addresses = [str(item[4][0]) for item in records]
    return bool(addresses) and all(
        configured_origin_address_allowed(address, trusted_private_ips) for address in addresses
    )


async def preflight(
    host: str, timeout: float, report: Report, trusted_private_ips: tuple[str, ...] = ()
) -> None:
    async with asyncio.timeout(timeout):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    if not public_records(records, trusted_private_ips):
        report.failure = "dns_rejected"
        raise ProbeBlocked()


def content_class(headers: list[tuple[bytes, bytes]]) -> str:
    values = [value for key, value in headers if key.lower() == b"content-type"]
    if len(values) != 1 or len(values[0]) > 1024:
        return "other"
    media = values[0].split(b";", 1)[0].strip().lower()
    if media in (b"text/html", b"application/xhtml+xml"):
        return "html"
    if media in (b"application/json", b"text/json"):
        return "json"
    return "binary" if media == b"application/octet-stream" else "other"


def identity_encoded(headers: list[tuple[bytes, bytes]]) -> bool:
    values = [value.strip().lower() for key, value in headers if key.lower() == b"content-encoding"]
    return not values or values == [b"identity"]


def classify_403(media: str, evidence: bytes) -> str:
    """Fixed shape/signature hints, NEVER provenance proof or extracted messages."""
    if len(evidence) > MAX_EVIDENCE:
        return "unknown"
    if media == "json":
        try:
            parsed = json.loads(evidence)
            if isinstance(parsed, dict) and (
                isinstance(parsed.get("error"), str)
                or (
                    isinstance(parsed.get("errors"), list)
                    and any(
                        isinstance(item, dict) and isinstance(item.get("message"), str)
                        for item in parsed["errors"]
                    )
                )
            ):
                return "canvas_json_error"
        except (ValueError, UnicodeError, RecursionError):
            pass
    if media == "html":
        lowered = evidence.lower()
        if not any(marker in lowered for marker in (b"<html", b"<!doctype html")):
            return "unknown"
        if any(
            marker in lowered
            for marker in (b"cloudflare", b"web application firewall", b"request blocked")
        ):
            return "html_waf_error"
        if any(marker in lowered for marker in (b"nginx", b"bad gateway", b"proxy error")):
            return "html_gateway_error"
    return "unknown"


def ua_class(value: bytes) -> str:
    if not value:
        return "absent"
    if value.startswith(b"Python-urllib/"):
        return "python_urllib"
    if value == b"PowerShell-compatible CanvasTransportProbe/0.0.0":
        return "powershell_style"
    if value == b"canvas-mcp/0.0.0":
        return "application"
    return "other"


class Observation:
    def __init__(
        self,
        origin: str,
        token: AccessToken,
        report: Report,
        trusted_private_ips: tuple[str, ...] = (),
    ) -> None:
        self.origin = normalize_origin(origin)
        self.host = urlsplit(self.origin).hostname or ""
        self.authority = urlsplit(self.origin).netloc.encode("ascii")
        self.trusted_private_ips = normalize_trusted_private_ips(trusted_private_ips)
        self._token = token
        self.report = report
        self._opening_stream: httpcore.AsyncNetworkStream | None = None

    def block(self) -> NoReturn:
        self.report.failure = "wire_rejected"
        raise ProbeBlocked()

    def peer(self, value: object) -> None:
        if not isinstance(value, tuple) or len(value) < 2 or not isinstance(value[0], str):
            self.report.tcp_destination = "rejected"
            self.block()
        valid = True
        canonical = canonical_resolved_address(value[0])
        public = valid and canonical is not None and is_public_address(canonical)
        internal = (
            valid
            and canonical is not None
            and is_internal_service_address(canonical)
            and canonical in self.trusted_private_ips
        )
        valid = valid and value[1] == 443 and (public or internal)
        self.report.tcp_destination = (
            "public_ip" if public else "internal_ip" if internal else "rejected"
        )
        if not valid:
            self.block()

    def tls(
        self, context: ssl.SSLContext, hostname: str | None, *, completed: bool = False
    ) -> None:
        self.report.sni_correct = hostname == self.host
        valid = context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.report.tls_verified = (True if completed else None) if valid else False
        if not self.report.sni_correct or not valid:
            self.block()

    async def close_opening(self) -> None:
        if self._opening_stream is not None:
            try:
                async with asyncio.timeout(1):
                    await self._opening_stream.aclose()
            finally:
                self._opening_stream = None

    def request(
        self,
        method: bytes,
        scheme: bytes,
        host: bytes,
        port: int,
        target: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        def values(name: bytes) -> list[bytes]:
            return [value for key, value in headers if key.lower() == name]

        self.report.host_correct = values(b"host") == [self.authority]
        self.report.auth_present = len(values(b"authorization")) == 1
        self.report.accept_json = values(b"accept") == [b"application/json"]
        encodings = values(b"accept-encoding")
        self.report.accept_encoding = (
            "absent" if not encodings else "identity" if encodings == [b"identity"] else "other"
        )
        self.report.content_type_present = bool(values(b"content-type"))
        agents = values(b"user-agent")
        self.report.user_agent = (
            ua_class(agents[0]) if len(agents) == 1 else ("absent" if not agents else "other")
        )
        self.report.request_correct = (
            method == b"GET"
            and scheme == b"https"
            and host == self.host.encode("ascii")
            and port == 443
            and target == PROFILE_PATH.encode("ascii")
        )
        if (
            not self.report.host_correct
            or not self.report.request_correct
            or values(b"authorization") != [b"Bearer " + self._token.value.encode("ascii")]
            or values(b"cookie")
            or values(b"proxy-authorization")
        ):
            self.block()

    async def trace(self, name: str, info: dict[str, Any]) -> None:
        # Never store or format trace payloads: they contain raw requests/headers.
        if name == "connection.connect_tcp.started":
            if info.get("host") != self.host or info.get("port") != 443:
                self.block()
        elif name == "connection.connect_tcp.complete":
            self._opening_stream = info["return_value"]
            try:
                self.peer(self._opening_stream.get_extra_info("server_addr"))
            except BaseException:
                await self.close_opening()
                raise
        elif name == "connection.start_tls.started":
            try:
                self.tls(info["ssl_context"], info.get("server_hostname"))
            except BaseException:
                await self.close_opening()
                raise
        elif name == "connection.start_tls.complete":
            stream = info["return_value"]
            self._opening_stream = stream
            try:
                self.peer(stream.get_extra_info("server_addr"))
                ssl_object = stream.get_extra_info("ssl_object")
                if ssl_object is None:
                    self.block()
                self.tls(ssl_object.context, ssl_object.server_hostname, completed=True)
            except BaseException:
                await self.close_opening()
                raise
            self._opening_stream = None
        elif name == "connection.start_tls.failed":
            self.report.tls_verified = False
            await self.close_opening()
        elif name == "http11.send_request_headers.started":
            request = info["request"]
            self.report.http_version = "HTTP/1.1"
            self.request(
                request.method,
                request.url.scheme,
                request.url.host,
                request.url.origin.port,
                request.url.target,
                request.headers,
            )
            if (
                self.report.sni_correct is not True
                or self.report.tls_verified is not True
                or self.report.tcp_destination not in ("public_ip", "internal_ip")
            ):
                self.block()
        elif name == "http2.send_request_headers.started":
            # This diagnostic does not enable HTTP/2; don't silently misreport it.
            self.report.http_version = "HTTP/2"
            self.block()

    def response(self, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        if type(status) is not int or not 100 <= status <= 599:
            self.block()
        self.report.status = status  # Preserve even if reading/classification fails.
        self.report.content_class = content_class(headers)


class ObservedPool:
    """Wrap the actual production/default pool, without altering its backend."""

    def __init__(self, pool: httpcore.AsyncConnectionPool, observation: Observation) -> None:
        self.pool, self.observation = pool, observation

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        extensions: dict[str, Any],
    ) -> AsyncIterator[httpcore.Response]:
        if method != "GET" or url != self.observation.origin + PROFILE_PATH:
            self.observation.block()
        if any(key.lower() in ("cookie", "proxy-authorization") for key in headers):
            self.observation.block()
        # No SNI override, opaque extension or second trace callback is accepted.
        if set(extensions) - {"timeout"}:
            self.observation.block()
        async with self.pool.stream(
            method,
            url,
            headers=list(headers.items()),
            extensions={**extensions, "trace": self.observation.trace},
        ) as response:
            fields = list(response.headers)
            self.observation.response(response.status, fields)
            if response.status == 403 and identity_encoded(fields):
                evidence = bytearray()
                try:
                    async for chunk in response.aiter_stream():
                        evidence.extend(chunk[: MAX_EVIDENCE + 1 - len(evidence)])
                        if len(evidence) > MAX_EVIDENCE:
                            break
                    self.observation.report.body_category = classify_403(
                        self.observation.report.content_class, bytes(evidence)
                    )
                except Exception:
                    pass  # Status survives; the category remains unknown.
                finally:
                    evidence.clear()
            yield response

    async def aclose(self) -> None:
        await self.pool.aclose()


class BaselineConnection(http.client.HTTPSConnection):
    """Standard library HTTP/TLS/DNS; guarded observations, not IP pinning."""

    _buffer: list[bytes]  # CPython's generated request lines, inspected before send.

    def __init__(self, observation: Observation, timeout: float) -> None:
        context = ssl.create_default_context()
        context.set_alpn_protocols(["http/1.1"])
        super().__init__(observation.host, 443, timeout=timeout, context=context)
        self.observation = observation
        self.set_debuglevel(0)

    def connect(self) -> None:
        # Normal OS resolver/socket selection. No proxies or tunnel is configured.
        super().connect()
        assert self.sock is not None
        self.observation.peer(self.sock.getpeername())
        if not isinstance(self.sock, ssl.SSLSocket):
            self.observation.block()
        self.observation.tls(self.sock.context, self.sock.server_hostname, completed=True)

    def endheaders(self, message_body: Any = None, *, encode_chunked: bool = False) -> None:
        # Inspect the actual generated HTTP/1.1 header buffer before it is sent.
        # In http.client, connect() can be deferred until endheaders()->send().
        fields = []
        if self._buffer[0] != b"GET " + PROFILE_PATH.encode("ascii") + b" HTTP/1.1":
            self.observation.report.request_correct = False
            self.observation.block()
        for line in self._buffer[1:]:
            key, value = line.split(b":", 1)
            fields.append((key, value.strip()))
        self.observation.report.http_version = "HTTP/1.1"
        self.observation.request(
            b"GET",
            b"https",
            self.observation.host.encode("ascii"),
            443,
            PROFILE_PATH.encode("ascii"),
            fields,
        )
        if (
            self.observation.report.sni_correct is not True
            or self.observation.report.tls_verified is not True
            or self.observation.report.tcp_destination not in ("public_ip", "internal_ip")
        ):
            self.observation.block()
        super().endheaders(message_body, encode_chunked=encode_chunked)


def baseline(
    origin: str,
    token: AccessToken,
    timeout: float,
    *,
    headers_ready: Callable[[Report], None] | None = None,
    trusted_private_ips: tuple[str, ...] = (),
) -> Report:
    report = Report(Mode.BASELINE)
    observation = Observation(origin, token, report, trusted_private_ips)
    connection = BaselineConnection(observation, timeout)
    try:
        if not public_records(
            socket.getaddrinfo(
                observation.host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            ),
            observation.trusted_private_ips,
        ):
            report.failure = "dns_rejected"
            raise ProbeBlocked()
        connection.connect()  # Verify actual peer and TLS BEFORE any HTTP header.
        connection.request(
            "GET",
            PROFILE_PATH,
            headers={
                "Authorization": "Bearer " + token.value,
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        fields = [
            (key.encode("ascii", "ignore"), value.encode("latin1", "replace"))
            for key, value in response.getheaders()
        ]
        observation.response(response.status, fields)
        if headers_ready is not None:
            headers_ready(report)  # Safe projection, before optional body analysis.
        if response.status == 403 and identity_encoded(fields):
            report.body_category = classify_403(
                report.content_class, response.read(MAX_EVIDENCE + 1)
            )
    except (TimeoutError, socket.timeout):
        report.failure = "timeout"
    except Exception:
        if report.failure == "none":
            report.failure = "request_failed"
    finally:
        connection.close()
    return report
