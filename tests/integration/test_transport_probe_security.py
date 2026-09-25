import asyncio
import io
import json
import ssl
from types import SimpleNamespace

import pytest

from canvas_mcp import composition, transport_probe as probe
from canvas_mcp.infrastructure.canvas import transport_diagnostic as diag
from canvas_mcp.ports.credentials import AccessToken
from conftest import ORIGIN, PROFILE, TOKEN, response
from test_transport_probe import Backend, HOST, IP, install_backends, install_dns


@pytest.mark.parametrize("process_env", [False, True])
def test_tls_keylogging_rejected_before_any_composition(monkeypatch, process_env):
    monkeypatch.setattr(probe, "open_canvas_connection", lambda *a, **k: pytest.fail("I/O"))
    if process_env:
        monkeypatch.setenv("SSLKEYLOGFILE", "unopened-sensitive-path")
    with pytest.raises(diag.ProbeBlocked):
        asyncio.run(
            probe.collect({} if process_env else {"SSLKEYLOGFILE": "unopened-sensitive-path"})
        )


class OpeningStream:
    def __init__(self, *, peer=IP, hostname=HOST, tls=True):
        self.peer, self.hostname, self.has_tls = peer, hostname, tls
        self.closed = False
        self.context = ssl.create_default_context()

    async def aclose(self):
        self.closed = True

    def get_extra_info(self, name):
        if name == "server_addr":
            return (self.peer, 443)
        if name == "ssl_object" and self.has_tls:
            return SimpleNamespace(context=self.context, server_hostname=self.hostname)
        return None


@pytest.mark.parametrize(
    "case", ["private_peer", "wrong_sni", "insecure_tls", "missing_tls", "wrong_tls_name"]
)
def test_rejected_opening_streams_close_before_any_http(case):
    async def run():
        report = diag.Report(diag.Mode.LIBRARY)
        observer = diag.Observation(ORIGIN, AccessToken(TOKEN), report)
        stream = OpeningStream(peer="127.0.0.1" if case == "private_peer" else IP)
        with pytest.raises(diag.ProbeBlocked):
            await observer.trace("connection.connect_tcp.complete", {"return_value": stream})
            if case == "insecure_tls":
                stream.context.check_hostname = False
                stream.context.verify_mode = ssl.CERT_NONE
            await observer.trace(
                "connection.start_tls.started",
                {
                    "ssl_context": stream.context,
                    "server_hostname": "evil.example" if case == "wrong_sni" else HOST,
                },
            )
            if case == "missing_tls":
                stream.has_tls = False
            if case == "wrong_tls_name":
                stream.hostname = "evil.example"
            await observer.trace("connection.start_tls.complete", {"return_value": stream})
        assert stream.closed and report.status is None
        assert report.failure == "wire_rejected"

    asyncio.run(run())


def test_tls_verified_means_completed_not_merely_configured():
    async def run():
        report = diag.Report(diag.Mode.LIBRARY)
        observer = diag.Observation(ORIGIN, AccessToken(TOKEN), report)
        stream = OpeningStream()
        await observer.trace("connection.connect_tcp.complete", {"return_value": stream})
        await observer.trace(
            "connection.start_tls.started", {"ssl_context": stream.context, "server_hostname": HOST}
        )
        assert report.tls_verified is None
        await observer.trace("connection.start_tls.failed", {"exception": RuntimeError(TOKEN)})
        assert report.tls_verified is False and stream.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "private"),
        ("status", True),
        ("status", 600),
        ("host_correct", TOKEN),
        ("transport", TOKEN),
        ("failure", TOKEN),
        ("body_category", TOKEN),
        ("user_agent", TOKEN),
        ("proxy_used", True),
        ("redirects_followed", True),
    ],
)
def test_report_projection_cannot_emit_freeform_fields(field, value):
    report = diag.Report(diag.Mode.CLIENT)
    setattr(report, field, value)
    with pytest.raises(diag.ProbeBlocked):
        report.safe_data()


@pytest.mark.parametrize(
    "bad", ["duplicate_host", "cookie", "proxy_auth", "bad_auth", "wrong_path", "wrong_scheme"]
)
def test_request_guard_rejects_other_authority_or_sensitive_extensions(bad):
    report = diag.Report(diag.Mode.LIBRARY)
    observer = diag.Observation(ORIGIN, AccessToken(TOKEN), report)
    headers = [(b"Host", HOST.encode()), (b"Authorization", b"Bearer " + TOKEN.encode())]
    if bad == "duplicate_host":
        headers.append((b"Host", IP.encode()))
    if bad == "cookie":
        headers.append((b"Cookie", TOKEN.encode()))
    if bad == "proxy_auth":
        headers.append((b"Proxy-Authorization", TOKEN.encode()))
    if bad == "bad_auth":
        headers[1] = (b"Authorization", b"Bearer wrong")
    with pytest.raises(diag.ProbeBlocked):
        observer.request(
            b"GET",
            b"http" if bad == "wrong_scheme" else b"https",
            HOST.encode(),
            443,
            b"/api/v1/users/self/profile" if bad == "wrong_path" else probe.PROFILE_PATH.encode(),
            headers,
        )
    assert report.failure == "wire_rejected" and report.status is None


def test_preflight_private_dns_no_connection(monkeypatch):
    backend = Backend(response(PROFILE))
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch, "127.0.0.1")
        async with composition.open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}, log_stream=io.StringIO()
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), diag.Mode.LIBRARY, 2)
        assert result.status is None and result.failure == "dns_rejected"
        assert not backend.targets and not backend.writes

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["oversize", "compressed", "read_failure"])
def test_raw_403_survives_unclassifiable_or_failed_body(monkeypatch, kind):
    if kind == "read_failure":
        reply = b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{"
    else:
        body = b'{"error":"' + TOKEN.encode() + b'"}'
        if kind == "oversize":
            body += b" " * (diag.MAX_EVIDENCE + 1)
        reply = response(
            status=403,
            body=body,
            headers=[(b"Content-Encoding", b"gzip")] if kind == "compressed" else [],
        )
    backend = Backend(reply)
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch)
        async with composition.open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}, log_stream=io.StringIO()
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), diag.Mode.CLIENT, 2)
        assert result.status == 403 and result.body_category == "unknown"
        assert TOKEN not in json.dumps(result.safe_data())

    asyncio.run(run())


def test_stage_c_headers_match_actual_stage_e_request(monkeypatch):
    backend = Backend(response(PROFILE))
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch)
        snapshots = []
        for mode in (diag.Mode.HEADERS, diag.Mode.CLIENT):
            backend.writes.clear()
            async with composition.open_canvas_connection(
                {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}, log_stream=io.StringIO()
            ) as connection:
                result = await probe.core_stage(connection, AccessToken(TOKEN), mode, 2)
            assert result.status == 200
            snapshots.append(b"".join(backend.writes))
        identical = snapshots[0] == snapshots[1]
        assert identical  # Compare inside test; never emit header bytes.

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    ["success", "invalid_json", "unsafe_field", "too_many", "timeout", "headers_then_timeout"],
)
def test_baseline_child_is_bounded_validated_and_reaped(monkeypatch, case):
    normal = json.dumps(diag.Report(diag.Mode.BASELINE, status=403).safe_data()).encode() + b"\n"
    lines = [normal, normal, b""]
    if case == "invalid_json":
        lines = [TOKEN.encode()]
    if case == "unsafe_field":
        lines = [json.dumps({"mode": "baseline_python", "status": 200, "failure": TOKEN}).encode()]
    if case == "too_many":
        lines = [normal] * 3
    if case == "timeout":
        lines = []
    if case == "headers_then_timeout":
        lines = [normal]

    class Child:
        returncode = None
        killed = False
        reaped = False

        @property
        def stdout(self):
            return self

        async def readline(self):
            if lines:
                return lines.pop(0)
            await asyncio.Event().wait()

        async def wait(self):
            self.reaped = True
            self.returncode = -9 if self.killed else 0

        def kill(self):
            self.killed = True

    child = Child()

    async def create(*args, **kwargs):
        assert TOKEN not in " ".join(args)
        assert kwargs["env"]["CANVAS_ACCESS_TOKEN"] == TOKEN
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = asyncio.run(probe.baseline_worker({"CANVAS_ACCESS_TOKEN": TOKEN}, 0.02))
    assert child.reaped and child.killed == (case != "success")
    assert result.failure == (
        "none" if case == "success" else "timeout" if "timeout" in case else "worker_failed"
    )
    if case == "headers_then_timeout":
        assert result.status == 403
    assert TOKEN not in json.dumps(result.safe_data())
