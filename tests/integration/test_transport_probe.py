import asyncio
import http.client
import io
import json
import logging
import socket
import ssl
import sys
from types import SimpleNamespace

import httpcore
import pytest

from canvas_mcp import composition, transport_probe as probe
from canvas_mcp.infrastructure.canvas import transport_diagnostic as diag
from canvas_mcp.infrastructure.logging.events import SENSITIVE_HTTP
from canvas_mcp.ports.credentials import AccessToken
from conftest import ORIGIN, PROFILE, TOKEN, response

IP = "8.8.8.8"
HOST = "canvas.example.edu"


def records(ip=IP):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]


class Stream(httpcore.AsyncMockStream):
    def __init__(self, backend):
        super().__init__([backend.reply])
        self.backend = backend
        self.ssl_object = None

    async def write(self, buffer, timeout=None):
        self.backend.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.backend.sni.append(server_hostname)
        self.ssl_object = SimpleNamespace(
            context=ssl_context,
            server_hostname=server_hostname,
            selected_alpn_protocol=lambda: None,
        )
        return self

    def get_extra_info(self, name):
        if name == "server_addr":
            return (self.backend.peer, 443)
        if name == "ssl_object":
            return self.ssl_object
        return None


class Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, reply, peer=IP):
        self.reply, self.peer = reply, peer
        self.targets, self.writes, self.sni = [], [], []

    async def connect_tcp(self, host, port, **kwargs):
        self.targets.append((host, port))
        return Stream(self)


def install_backends(monkeypatch, backend):
    # PublicOriginBackend and HTTPCore's default AutoBackend both delegate here.
    from httpcore._backends import anyio

    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: backend)
    monkeypatch.setattr(anyio, "AnyIOBackend", lambda: backend)


async def install_dns(monkeypatch, ip=IP):
    async def dns(*args, **kwargs):
        return records(ip)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)


@pytest.mark.parametrize("mode", list(diag.Mode)[1:])
@pytest.mark.parametrize("status", [200, 403, 302])
def test_real_library_wire_and_client_parity(monkeypatch, capsys, mode, status):
    backend = Backend(
        response(PROFILE if status == 200 else {"errors": [{"message": TOKEN}]}, status=status)
    )
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch)
        async with composition.open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN + "/", "CANVAS_ACCESS_TOKEN": TOKEN},
            log_stream=io.StringIO(),
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), mode, 2)
        assert result.status == status
        assert result.host_correct and result.sni_correct and result.auth_present
        assert result.tls_verified and result.request_correct and result.accept_json
        assert result.http_version == "HTTP/1.1"
        assert result.proxy_used is False and result.redirects_followed is False
        assert result.body_category == ("canvas_json_error" if status == 403 else "unknown")
        assert result.accept_encoding == (
            "identity" if mode in (diag.Mode.HEADERS, diag.Mode.CLIENT) else "absent"
        )
        wire = b"".join(backend.writes)
        assert wire.startswith(b"GET /api/v1/users/self HTTP/1.1\r\n")
        assert b"Host: canvas.example.edu\r\n" in wire and b"Host: 8.8.8.8" not in wire
        assert wire.count(b"Authorization: Bearer " + TOKEN.encode() + b"\r\n") == 1
        assert backend.sni == [HOST]
        assert backend.targets == [
            (IP if mode in (diag.Mode.TRANSPORT, diag.Mode.CLIENT) else HOST, 443)
        ]
        assert TOKEN not in json.dumps(result.safe_data())
        assert ORIGIN not in json.dumps(result.safe_data())

    asyncio.run(run())
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("mode", [diag.Mode.LIBRARY, diag.Mode.TRANSPORT, diag.Mode.CLIENT])
def test_private_dns_or_rebound_peer_never_receives_auth(monkeypatch, mode):
    backend = Backend(response(PROFILE), peer="127.0.0.1")
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch)
        async with composition.open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}, log_stream=io.StringIO()
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), mode, 2)
        assert result.status is None and result.tcp_destination == "rejected"
        assert result.failure == "wire_rejected"
        assert not backend.writes and not backend.sni

    asyncio.run(run())


def test_bad_host_with_correct_sni_is_detected_before_send(monkeypatch):
    backend = Backend(response(PROFILE))
    install_backends(monkeypatch, backend)

    async def run():
        report = diag.Report(diag.Mode.LIBRARY)
        observed = diag.ObservedPool(
            httpcore.AsyncConnectionPool(), diag.Observation(ORIGIN, AccessToken(TOKEN), report)
        )
        try:
            with pytest.raises(diag.ProbeBlocked):
                async with observed.stream(
                    "GET",
                    ORIGIN + probe.PROFILE_PATH,
                    headers={
                        "Host": IP,
                        "Authorization": "Bearer " + TOKEN,
                        "Accept": "application/json",
                    },
                    extensions={},
                ):
                    pytest.fail("wrong authority reached response")
        finally:
            await observed.aclose()
        assert report.sni_correct is True and report.host_correct is False
        assert report.status is None and report.failure == "wire_rejected"
        assert not backend.writes

    asyncio.run(run())


@pytest.mark.parametrize("status", [200, 403, 302])
def test_stdlib_baseline_actual_host_sni_and_fixed_target(monkeypatch, status):
    writes = []

    class FakeSocket:
        def __init__(self, context, hostname):
            self.context, self.server_hostname = context, hostname

        def getpeername(self):
            return (IP, 443)

        def sendall(self, data):
            writes.append(data)

        def makefile(self, *args):
            return io.BytesIO(response({"error": TOKEN}, status=status))

        def close(self):
            pass

    def connect(connection):
        connection.sock = FakeSocket(connection._context, connection.host)

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: records())
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
    monkeypatch.setattr(ssl, "SSLSocket", FakeSocket)
    result = diag.baseline(ORIGIN, AccessToken(TOKEN), 2)
    assert result.status == status
    assert result.host_correct and result.sni_correct and result.auth_present
    assert result.tls_verified and result.request_correct
    assert result.accept_encoding == "identity" and result.user_agent == "absent"
    assert b"Host: canvas.example.edu\r\n" in b"".join(writes)
    assert TOKEN not in json.dumps(result.safe_data())


@pytest.mark.parametrize(
    "media,body,expected",
    [
        ("json", b'{"errors":[{"message":"private"}]}', "canvas_json_error"),
        ("json", b'{"error":"private"}', "canvas_json_error"),
        ("json", b'{"message":"private"}', "unknown"),
        ("json", b"[" * 3000, "unknown"),
        ("html", b"<html>cloudflare PRIVATE</html>", "html_waf_error"),
        ("html", b"<!doctype html><p>nginx PRIVATE</p>", "html_gateway_error"),
        ("html", b"<html>permission denied PRIVATE</html>", "unknown"),
        ("json", b" " * (diag.MAX_EVIDENCE + 1), "unknown"),
    ],
)
def test_response_categories_are_bounded_fixed_hints(media, body, expected):
    assert diag.classify_403(media, body) == expected


@pytest.mark.parametrize(
    "arguments", [[], ["--details"], ["--user-agent-matrix"], ["--_baseline-worker"]]
)
def test_no_live_optin_no_config_or_network(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["transport_probe", *arguments])
    monkeypatch.setattr(probe, "collect", lambda *a, **k: pytest.fail("I/O before opt-in"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_real_collect_order_no_profile_preflight_or_file_activity(monkeypatch, capsys):
    backend = Backend(response(PROFILE))
    install_backends(monkeypatch, backend)
    monkeypatch.setenv("CANVAS_BASE_URL", ORIGIN)
    monkeypatch.setenv("CANVAS_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy-secret.invalid:3128")
    calls = []
    original = probe.core_stage

    async def baseline(env, timeout):
        calls.append(diag.Mode.BASELINE)
        assert env["CANVAS_ACCESS_TOKEN"] == TOKEN
        return diag.Report(diag.Mode.BASELINE, status=200)

    async def core(connection, token, mode, timeout):
        calls.append(mode)
        return await original(connection, token, mode, timeout)

    monkeypatch.setattr(probe, "baseline_worker", baseline)
    monkeypatch.setattr(probe, "core_stage", core)
    monkeypatch.setattr(composition.ManagedStore, "_start", lambda *a: pytest.fail("file activity"))

    async def run():
        await install_dns(monkeypatch)
        return await probe.run(live=True, details=True, user_agents=True)

    assert asyncio.run(run()) == 0
    out = capsys.readouterr()
    assert not out.err and TOKEN not in out.out and "private-proxy-secret" not in out.out
    assert calls == [*probe.MODES, *probe.USER_AGENTS]
    rows = [json.loads(line) for line in out.out.splitlines()[1:]]
    assert len(rows) == 8 and all(row["status"] == 200 for row in rows)
    assert all(row["proxy_used"] is False for row in rows)
    assert len(backend.targets) == 7


def test_library_debug_logging_suppressed_during_trace(monkeypatch):
    backend = Backend(response({"error": TOKEN}, status=403))
    install_backends(monkeypatch, backend)
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    logger = logging.getLogger("httpcore.http11")
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    async def run():
        await install_dns(monkeypatch)
        async with composition.open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}, log_stream=io.StringIO()
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), diag.Mode.LIBRARY, 2)
        assert result.status == 403
        assert not SENSITIVE_HTTP.get()

    try:
        asyncio.run(run())
        assert log.getvalue() == ""
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
