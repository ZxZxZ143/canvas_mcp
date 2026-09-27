"""Real MCP result privacy, integrity, bounds and unchanged stdio download surface."""

import asyncio
import base64
import hashlib
import json
from dataclasses import replace

import pytest
from mcp.types import CallToolResult

from canvas_mcp.domain.errors import FileParseError
from canvas_mcp.domain.remote_artifact import RemoteArtifact
from canvas_mcp.mcp.artifact_result import artifact_result, MAX_ARTIFACT_WIRE_BYTES
from canvas_mcp.mcp.artifact_ui import ARTIFACT_URI
from canvas_mcp.mcp.tools import create_server
from test_surface import FakeConnection, FILE, result
from test_http import client, rpc
from contextlib import asynccontextmanager

BODY = b"Original untrusted document bytes."
ARTIFACT = RemoteArtifact(
    FILE, "canvas-file-50.txt", "text/plain", hashlib.sha256(BODY).hexdigest(), BODY
)


class Connection(FakeConnection):
    async def download_original(self, reference):
        self.calls.append(reference)
        return result(ARTIFACT)


def test_prepare_is_compact_and_ui_only_fetch_preserves_hidden_original():
    server = create_server(Connection(), None, transport="http")
    args = {"course_id": 8, "file_id": 50}
    prepared = asyncio.run(server.call_tool("canvas_download_file", args))
    assert "base64" not in prepared.model_dump_json()
    assert len(prepared.model_dump_json().encode()) < 4096
    output = asyncio.run(server.call_tool("canvas_fetch_original_for_card", args))
    assert isinstance(output, CallToolResult)
    assert base64.b64decode(output.meta["canvasArtifact"]["base64"]) == BODY
    visible = json.dumps(output.structuredContent) + repr(output.content)
    assert "base64" not in visible and BODY.decode() not in visible
    assert not any(value in visible for value in ("local_path", "public_url", "https://", "/tmp/"))
    assert output.structuredContent["data"]["sha256"] == hashlib.sha256(BODY).hexdigest()
    tools = asyncio.run(server.list_tools())
    download = next(tool for tool in tools if tool.name == "canvas_download_file")
    assert download.meta["ui"]["resourceUri"] == ARTIFACT_URI
    assert download.outputSchema["type"] == "object"
    assert download.inputSchema["additionalProperties"] is False
    assert download.annotations.destructiveHint is False
    fetch = next(tool for tool in tools if tool.name == "canvas_fetch_original_for_card")
    assert fetch.meta["ui"] == {"visibility": ["app"]}
    assert fetch.meta["openai/visibility"] == "private"
    assert fetch.meta["openai/widgetAccessible"] is True
    assert "openai/outputTemplate" not in fetch.meta
    assert {key: value for key, value in fetch.inputSchema.items() if key != "title"} == {
        key: value for key, value in download.inputSchema.items() if key != "title"
    }
    local = create_server(Connection(), None)
    assert "canvas_fetch_original_for_card" not in {
        tool.name for tool in asyncio.run(local.list_tools())
    }


def test_invalid_hash_empty_or_oversized_payload_never_creates_artifact():
    for changed in (
        replace(ARTIFACT, sha256="0" * 64),
        replace(ARTIFACT, data=b""),
        replace(ARTIFACT, data=b"a" * (4194304 + 1)),
    ):
        with pytest.raises(FileParseError):
            artifact_result(result(changed))


def test_app_only_fetch_still_requires_auth_and_rejects_non_reference_inputs():
    opened = []

    @asynccontextmanager
    async def factory():
        opened.append(True)
        yield Connection()

    with client(factory=factory) as connection:
        name = "canvas_fetch_original_for_card"
        denied = rpc(connection, "tools/call", {"name": name, "arguments": {}}, headers={})
        assert denied.status_code == 401 and not opened
        for bad in (
            {"course_id": 8, "file_id": 50, "url": "https://example.invalid/private"},
            {"course_id": 8, "file_id": 50, "path": "/etc/passwd"},
            {"course_id": True, "file_id": 50},
        ):
            rejected = rpc(connection, "tools/call", {"name": name, "arguments": bad})
            assert rejected.json()["result"]["isError"] and not opened


def test_maximum_original_has_bounded_exact_wire_and_still_hidden():
    body = b"x" * 4194304
    artifact = replace(ARTIFACT, data=body, sha256=hashlib.sha256(body).hexdigest())
    output = artifact_result(result(artifact))
    assert len(output.model_dump_json().encode()) + 1024 <= MAX_ARTIFACT_WIRE_BYTES
    assert len(output.content[0].text) < 256


def test_static_resource_is_inert_and_has_empty_external_domains():
    server = create_server(Connection(), None, transport="http")
    resources = list(asyncio.run(server.read_resource(ARTIFACT_URI)))
    card = resources[0]
    assert card.mime_type == "text/html;profile=mcp-app"
    assert card.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    assert "innerHTML" not in card.content and "fetch(" not in card.content
    assert "crypto.subtle.digest" in card.content and "textContent" in card.content


def test_saved_card_reference_survives_upgrade_without_expanding_resources():
    server = create_server(Connection(), None, transport="http")
    old_uri = "ui://canvas/original-file-v1.html"
    current = list(asyncio.run(server.read_resource(ARTIFACT_URI)))[0]
    saved = list(asyncio.run(server.read_resource(old_uri)))[0]
    assert saved.content == current.content
    assert saved.mime_type == current.mime_type == "text/html;profile=mcp-app"
    assert saved.meta == current.meta
    assert saved.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    previous = list(asyncio.run(server.read_resource("ui://canvas/original-file-v2.html")))[0]
    assert previous.content == current.content and previous.meta == current.meta
    tools = asyncio.run(server.list_tools())
    download = next(tool for tool in tools if tool.name == "canvas_download_file")
    assert download.meta["ui"]["resourceUri"] == ARTIFACT_URI != old_uri
    with pytest.raises(Exception, match="Unknown resource"):
        asyncio.run(server.read_resource("ui://canvas/unknown-file.html"))
    local = create_server(Connection(), None)
    assert asyncio.run(local.list_resources()) == []
    for uri in (old_uri, "ui://canvas/original-file-v2.html", ARTIFACT_URI):
        with pytest.raises(Exception, match="Unknown resource"):
            asyncio.run(local.read_resource(uri))


@pytest.mark.parametrize("size", [250_000, 519_944, 4194304])
def test_actual_http_endpoint_accepts_bounded_hidden_original(size):
    body = b"x" * size
    artifact = replace(ARTIFACT, data=body, sha256=hashlib.sha256(body).hexdigest())

    class Sized(Connection):
        async def download_original(self, reference):
            return result(artifact)

    @asynccontextmanager
    async def factory():
        yield Sized()

    with client(factory=factory) as connection:
        prepared = rpc(
            connection,
            "tools/call",
            {"name": "canvas_download_file", "arguments": {"course_id": 8, "file_id": 50}},
        )
        assert prepared.status_code == 200 and not prepared.json()["result"].get("isError")
        assert "base64" not in prepared.text and len(prepared.content) < 4096
        response = rpc(
            connection,
            "tools/call",
            {
                "name": "canvas_fetch_original_for_card",
                "arguments": {"course_id": 8, "file_id": 50},
            },
        )
        assert response.status_code == 200
        output = response.json()["result"]
        assert not output.get("isError")
        assert base64.b64decode(output["_meta"]["canvasArtifact"]["base64"]) == body
        assert output["structuredContent"]["data"]["sha256"] == artifact.sha256
        assert prepared.json()["result"]["structuredContent"] == output["structuredContent"]


def test_ordinary_http_tool_keeps_previous_response_cap():
    from canvas_mcp.domain.models import ExternalText
    from test_surface import PROFILE

    class Large(Connection):
        async def get_profile(self):
            return result(replace(PROFILE, display_name=ExternalText("x" * 300_000)))

    @asynccontextmanager
    async def factory():
        yield Large()

    with client(factory=factory) as connection:
        response = rpc(connection, "tools/call", {"name": "canvas_get_profile", "arguments": {}})
        assert len(response.content) < 262144
        assert response.status_code >= 400 or response.json()["result"].get("isError")


@pytest.mark.parametrize("encoding", ["raw", "percent", "utf16"])
@pytest.mark.parametrize("secret_kind", ["canvas", "oauth"])
def test_actual_oauth_endpoint_rejects_credentials_hidden_in_original(encoding, secret_kind):
    import io

    from starlette.testclient import TestClient
    from canvas_mcp.infrastructure.config.remote import RemoteSettings
    from canvas_mcp.mcp.http import DevelopmentAuthenticator
    from canvas_mcp.mcp.identity import McpPrincipal
    from canvas_mcp.mcp_http_server import create_app
    from test_http import CANVAS_TOKEN
    from test_oauth import BASE, ENV, SUBJECT, headers

    bearer = "SYNTHETIC_PRESENTED_OAUTH_BEARER_1234567890"
    secret = CANVAS_TOKEN if secret_kind == "canvas" else bearer
    body = (
        secret.encode("utf-16-le")
        if encoding == "utf16"
        else "".join("%" + format(byte, "02X") for byte in secret.encode()).encode()
        if encoding == "percent"
        else secret.encode()
    )
    artifact = replace(ARTIFACT, data=body, sha256=hashlib.sha256(body).hexdigest())

    class Reflected(Connection):
        async def download_original(self, reference):
            return result(artifact)

    @asynccontextmanager
    async def factory():
        yield Reflected()

    logs = io.StringIO()
    principal = McpPrincipal(SUBJECT, "personal_canvas")
    with TestClient(
        create_app(
            RemoteSettings(auth_mode="oauth"),
            environ=ENV,
            connection_factory=factory,
            log_stream=logs,
            authenticator=DevelopmentAuthenticator(bearer, principal),
        ),
        base_url=BASE,
    ) as connection:
        response = rpc(
            connection,
            "tools/call",
            {
                "name": "canvas_fetch_original_for_card",
                "arguments": {"course_id": 8, "file_id": 50},
            },
            headers(bearer),
        )
        assert response.status_code == 500
        assert response.json() == {"error": "internal_error"}
        assert secret not in response.text + logs.getvalue()
