"""Standard MCP resource handoff, actual SDK wire, identity and rollback guards."""

import asyncio
import base64
import hashlib
import io
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from canvas_mcp.domain.errors import FileContentUnavailableError, FileParseError
from canvas_mcp.domain.models import ConnectionId, PrincipalId
from canvas_mcp.domain.remote_artifact import RemoteArtifact
from canvas_mcp.mcp.http import HttpBoundary
from canvas_mcp.mcp.identity import McpPrincipal, current_principal
from canvas_mcp.mcp.native_handoff import OriginalHandoffs, handoff_receipts
from canvas_mcp.mcp.native_result import native_content_result
from canvas_mcp.mcp.tools import create_server
from canvas_mcp.mcp_http_server import create_app
from test_file_content import CONTENT, ARGS, Connection
from test_http import HEADERS, SETTINGS, rpc
from test_surface import FILE, result

OWNER = McpPrincipal(PrincipalId("synthetic-owner"), ConnectionId("synthetic-canvas"))
A = "SYNTHETIC_ISSUANCE_BEARER_1234567890"
B = "SYNTHETIC_RESOURCE_BEARER_0987654321"


def original(body=b"Untrusted controlled original."):
    return RemoteArtifact(
        FILE, "canvas-file-50.txt", "text/plain", hashlib.sha256(body).hexdigest(), body
    )


def content(artifact):
    return replace(
        CONTENT, format="txt", size=len(artifact.data), sha256=artifact.sha256, original=artifact
    )


@asynccontextmanager
async def owned():
    owner_token = current_principal.set(OWNER)
    receipt_token = handoff_receipts.set([])
    registry = OriginalHandoffs()
    try:
        yield registry
    finally:
        registry.close()
        current_principal.reset(owner_token)
        handoff_receipts.reset(receipt_token)


class Auth:
    async def authenticate(self, authorization):
        return OWNER if authorization in ("Bearer " + A, "Bearer " + B) else None


def native_client(artifact, logs=None):
    class Analyzed(Connection):
        async def get_file_content(self, reference, selection):
            return result(content(artifact))

    @asynccontextmanager
    async def factory():
        yield Analyzed()

    return TestClient(
        create_app(
            SETTINGS,
            environ={},
            connection_factory=factory,
            authenticator=Auth(),
            log_stream=logs or io.StringIO(),
        ),
        base_url="http://127.0.0.1:8000",
    )


def headers(bearer=A):
    return {**HEADERS, "Authorization": "Bearer " + bearer}


def analyze(connection):
    return rpc(
        connection,
        "tools/call",
        {"name": "canvas_get_file_content", "arguments": ARGS},
        headers(A),
    )


def link(response):
    return next(
        item for item in response.json()["result"]["content"] if item["type"] == "resource_link"
    )


def test_native_reference_is_compact_and_blob_only_in_resource_read():
    logs = io.StringIO()
    artifact = original()
    with native_client(artifact, logs) as connection:
        response = analyze(connection)
        assert response.status_code == 200
        resource = link(response)
        assert resource["mimeType"] == "text/plain" and resource["size"] == len(artifact.data)
        output = response.json()["result"]
        assert "base64" not in response.text and "blob" not in output["structuredContent"]
        assert artifact.data.decode() not in response.text and "_meta" not in output
        assert output["structuredContent"]["data"]["file"]["original_download_available"]
        listed = rpc(connection, "resources/list", headers=headers()).json()["result"]
        assert resource["uri"] not in json.dumps(listed)
        read = rpc(connection, "resources/read", {"uri": resource["uri"]}, headers(B))
        assert read.status_code == 200
        item = read.json()["result"]["contents"][0]
        assert base64.b64decode(item["blob"]) == artifact.data
        assert item["mimeType"] == artifact.content_type and item["uri"] == resource["uri"]
        assert "canvas-original" not in logs.getvalue() and A not in logs.getvalue()
        retry = rpc(connection, "resources/read", {"uri": resource["uri"]}, headers(B))
        assert retry.status_code == 200 and retry.json()["result"]["contents"][0] == item
        assert rpc(connection, "resources/read", {"uri": resource["uri"]}, {}).status_code == 401


@pytest.mark.parametrize("where", ["issuance", "read"])
@pytest.mark.parametrize("encoding", ["plain", "utf16", "percent"])
def test_distinct_request_bearers_guard_original_at_issuance_and_read(where, encoding):
    secret = A if where == "issuance" else B
    body = (
        secret.encode("utf-16-le")
        if encoding == "utf16"
        else "".join("%" + format(byte, "02X") for byte in secret.encode()).encode()
        if encoding == "percent"
        else secret.encode()
    )
    with native_client(original(body)) as connection:
        response = analyze(connection)
        if where == "read":
            assert response.status_code == 200
            uri = link(response)["uri"]
            response = rpc(connection, "resources/read", {"uri": uri}, headers(B))
            assert response.status_code == 500
            again = rpc(connection, "resources/read", {"uri": uri}, headers(A))
            assert "error" in again.json()  # rejected bytes were removed
        assert response.status_code == 500 and response.json() == {"error": "internal_error"}
        assert secret not in response.text


def test_maximum_blob_has_its_own_cap_and_analysis_stays_small():
    artifact = original(b"x" * 4_194_304)
    with native_client(artifact) as connection:
        response = analyze(connection)
        assert len(response.content) < 131_072
        read = rpc(connection, "resources/read", {"uri": link(response)["uri"]}, headers(B))
        assert read.status_code == 200 and len(read.content) <= 5_700_000
        assert (
            hashlib.sha256(
                base64.b64decode(read.json()["result"]["contents"][0]["blob"])
            ).hexdigest()
            == artifact.sha256
        )


def test_handoff_identity_capacity_rollback_expiry_and_shutdown(monkeypatch):
    async def run():
        async with owned() as registry:
            first = registry.publish(original())
            second = registry.publish(original())
            assert first and second and registry.publish(original()) is None
            changed = current_principal.set(replace(OWNER, connection_id=ConnectionId("other")))
            with pytest.raises(FileContentUnavailableError):
                registry.read(first)
            current_principal.reset(changed)
            for invalid in (
                first + "?x=1",
                first + "/../x",
                first.replace("canvas-original", "https"),
            ):
                with pytest.raises(FileContentUnavailableError):
                    registry.read(invalid)
            registry.finish(second, "published", False)
            assert registry.publish(original())
            registry.finish(first, "read", True)
            assert registry.peek(first).data
            registry.close()
            assert not registry._entries and registry._timer is None
            with pytest.raises(FileContentUnavailableError):
                registry.read(first)
        monkeypatch.setattr("canvas_mcp.mcp.native_handoff.PENDING_SECONDS", 0.01)
        async with owned() as registry:
            uri = registry.publish(original())
            await asyncio.sleep(0.03)
            assert not registry._entries and registry._timer is None
            with pytest.raises(FileContentUnavailableError):
                registry.read(uri)

    asyncio.run(run())


def test_missing_request_identity_or_ledger_fails_closed():
    async def run():
        registry = OriginalHandoffs()
        with pytest.raises(FileContentUnavailableError):
            registry.publish(original())
        token = current_principal.set(OWNER)
        try:
            with pytest.raises(FileContentUnavailableError):
                registry.publish(original())
        finally:
            current_principal.reset(token)
            registry.close()

    asyncio.run(run())


def test_aggregate_capacity_never_evicts_an_unexpired_promised_original():
    async def run():
        async with owned() as registry:
            artifact = original(b"a" * 3_000_000)
            uri = registry.publish(artifact)
            assert uri and registry.publish(artifact) is None
            assert registry.peek(uri).data == artifact.data
            assert len(registry._entries) == 1

    asyncio.run(run())


def test_transfer_gate_covers_slow_response_send_before_next_analysis():
    async def run():
        first_sending = asyncio.Event()
        release = asyncio.Event()
        started = []

        async def app(scope, receive, send):
            started.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"result":{}}'})

        boundary = HttpBoundary(
            app, SETTINGS, Auth(), frozenset(("canvas_get_file_content",)), io.StringIO()
        )
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", ("Bearer " + A).encode())],
        }

        async def receive():
            return {
                "type": "http.request",
                "body": json.dumps(
                    {"method": "tools/call", "params": {"name": "canvas_get_file_content"}}
                ).encode(),
            }

        async def slow_send(message):
            first_sending.set()
            await release.wait()

        async def fast_send(message):
            pass

        first = asyncio.create_task(boundary(scope, receive, slow_send))
        await first_sending.wait()
        second = asyncio.create_task(boundary(scope, receive, fast_send))
        await asyncio.sleep(0.02)
        assert len(started) == 1 and boundary._transfer_gate.locked()
        release.set()
        await asyncio.gather(first, second)
        assert len(started) == 2 and not boundary._transfer_gate.locked()

    asyncio.run(run())


def test_native_result_capacity_reason_and_integrity_validation():
    async def run():
        async with owned() as registry:
            registry.publish(original())
            registry.publish(original())
            output = native_content_result(result(content(original())), registry)
            assert all(item.type == "text" for item in output.content)
            metadata = output.structuredContent["data"]["file"]
            assert metadata["original_download_available"] is False
            assert metadata["original_download_reason"] == "original_handoff_capacity"
            with pytest.raises(FileParseError):
                native_content_result(result(replace(content(original()), size=999)), registry)

    asyncio.run(run())


def test_non_analysis_tools_have_no_native_or_widget_template():
    server = create_server(Connection(), None, transport="http")
    tools = asyncio.run(server.list_tools())
    for name in ("canvas_get_file_content", "canvas_list_files", "canvas_get_file_metadata"):
        tool = next(tool for tool in tools if tool.name == name)
        assert not tool.meta or not any(key in tool.meta for key in ("ui", "openai/outputTemplate"))
    assert "canvas_test_native_file_reference" not in {tool.name for tool in tools}


@pytest.mark.parametrize("failure", ["cancel", "send"])
def test_response_failure_rolls_back_original_and_releases_transfer_gate(failure):
    async def run():
        registry = OriginalHandoffs()
        issued = []

        async def app(scope, receive, send):
            issued.append(registry.publish(original()))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"result":{}}'})

        boundary = HttpBoundary(
            app, SETTINGS, Auth(), frozenset(("canvas_get_file_content",)), io.StringIO()
        )
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", ("Bearer " + A).encode())],
        }

        async def receive():
            return {
                "type": "http.request",
                "body": json.dumps(
                    {"method": "tools/call", "params": {"name": "canvas_get_file_content"}}
                ).encode(),
            }

        failed = False

        async def send(message):
            nonlocal failed
            if not failed:
                failed = True
                if failure == "cancel":
                    raise asyncio.CancelledError()
                raise RuntimeError()

        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await boundary(scope, receive, send)
        else:
            await boundary(scope, receive, send)
        assert issued and not registry._entries and not boundary._transfer_gate.locked()
        assert handoff_receipts.get() is None and current_principal.get() is None
        registry.close()

    asyncio.run(run())
