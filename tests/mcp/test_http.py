"""Real SDK HTTP flow, request boundaries and stdio parity without Canvas credentials."""

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass, replace

import pytest
from starlette.testclient import TestClient

from canvas_mcp.domain.errors import ConfigurationError
from canvas_mcp.infrastructure.config.remote import RemoteSettings, load_remote_settings
from canvas_mcp.mcp.identity import current_principal
from canvas_mcp.mcp.tools import create_server
from canvas_mcp.mcp_http_server import create_app
from canvas_mcp.mcp import projection as dto
from test_surface import ARGS, FakeConnection, invoke, result
from canvas_mcp.domain.models import DownloadedFile
from test_surface import NOW, REFERENCE, obs
from canvas_mcp.domain.models import ExternalText

DEV_TOKEN = "SYNTHETIC_DEV_AUTH_TOKEN_1234567890"
CANVAS_TOKEN = "SYNTHETIC_CANVAS_TOKEN_1234567890"
HEADERS = {
    "Authorization": f"Bearer {DEV_TOKEN}",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}
SETTINGS = RemoteSettings(auth_mode="development", development=True, dev_token=DEV_TOKEN)
REMOTE = set(ARGS) - {"canvas_download_file"}


@asynccontextmanager
async def fake_factory():
    assert current_principal.get() is not None
    yield PathFreeConnection()


def neutral_fixture(value):
    # The pre-existing fake contains a prompt injection quoting C:\\Users\\id_rsa.
    # Use path-free coursework for the server-path parity check; the original
    # injection fixture remains covered by the stdio security tests.
    if isinstance(value, ExternalText):
        return replace(value, text="Synthetic coursework")
    if is_dataclass(value):
        return replace(
            value,
            **{item.name: neutral_fixture(getattr(value, item.name)) for item in fields(value)},
        )
    if isinstance(value, tuple):
        return tuple(neutral_fixture(item) for item in value)
    return value


class PathFreeConnection(FakeConnection):
    def __getattr__(self, name):
        original = super().__getattr__(name)

        async def call(*args, **kwargs):
            return neutral_fixture(await original(*args, **kwargs))

        return call


def client(settings=SETTINGS, factory=fake_factory, logs=None):
    return TestClient(
        create_app(
            settings,
            environ={"CANVAS_ACCESS_TOKEN": CANVAS_TOKEN},
            connection_factory=factory,
            log_stream=logs or io.StringIO(),
        ),
        base_url="http://127.0.0.1:8000",
    )


def rpc(connection, method, params=None, headers=HEADERS):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return connection.post("/mcp", json=payload, headers=headers)


def test_initialization_health_and_list_do_not_open_canvas():
    @asynccontextmanager
    async def forbidden():
        raise AssertionError("Startup and discovery must not contact Canvas")
        yield

    with client(factory=forbidden) as connection:
        health = connection.get("/health")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        initialized = rpc(
            connection,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "canvas_student"
        assert "mcp-session-id" not in initialized.headers
        notification = connection.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS
        )
        assert notification.status_code == 202
        listed = rpc(connection, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in listed} == REMOTE
        assert all(tool["annotations"]["readOnlyHint"] for tool in listed)
        assert "access-control-allow-origin" not in health.headers


def test_official_python_sdk_client_completes_initialization_and_tool_flow():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run():
        app = create_app(
            SETTINGS, environ={}, connection_factory=fake_factory, log_stream=io.StringIO()
        )
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers={"Authorization": HEADERS["Authorization"]},
            ) as http_client:
                async with streamable_http_client(
                    "http://127.0.0.1:8000/mcp", http_client=http_client
                ) as (reader, writer, session_id):
                    async with ClientSession(reader, writer) as session:
                        initialized = await session.initialize()
                        assert initialized.protocolVersion == "2025-11-25"
                        assert session_id() is None
                        assert {tool.name for tool in (await session.list_tools()).tools} == REMOTE
                        value = await session.call_tool("canvas_get_profile", {})
                        assert not value.isError
                        assert (
                            value.structuredContent["data"]["name"]["text"]
                            == "Synthetic coursework"
                        )

    asyncio.run(run())


@pytest.mark.parametrize("name", sorted(REMOTE))
def test_http_invocation_matches_stdio_projection_and_has_no_paths(name):
    with client() as connection:
        response = rpc(connection, "tools/call", {"name": name, "arguments": ARGS[name]})
        assert response.status_code == 200
        value = response.json()["result"]
        assert not value["isError"]
        assert value["structuredContent"] == invoke(
            create_server(PathFreeConnection(), None), name, ARGS[name]
        )
        for private in (
            "managed_local_path",
            "C:\\",
            "/tmp/",
            "DOWNLOAD_DIRECTORY",
            CANVAS_TOKEN,
            DEV_TOKEN,
            "public_url",
        ):
            assert private not in response.text


@pytest.mark.parametrize(
    "headers",
    [{}, {**HEADERS, "Authorization": "Bearer wrong"}, {**HEADERS, "Authorization": CANVAS_TOKEN}],
)
def test_development_requires_separate_bearer_auth(headers):
    with client() as connection:
        assert rpc(connection, "tools/list", headers=headers).status_code == 401


def test_default_non_development_refuses_every_mcp_method_but_health():
    with client(settings=RemoteSettings()) as connection:
        for method in ("initialize", "tools/list", "tools/call"):
            assert rpc(connection, method).status_code == 401
        for method in ("get", "delete", "options"):
            assert getattr(connection, method)("/mcp", headers=HEADERS).status_code == 401
        assert connection.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "env",
    [
        {"MCP_HTTP_AUTH_MODE": "none"},
        {"MCP_HTTP_AUTH_MODE": "development", "MCP_HTTP_DEV_TOKEN": DEV_TOKEN},
        {"MCP_HTTP_AUTH_MODE": "development", "MCP_HTTP_DEVELOPMENT": "true"},
        {"MCP_HTTP_HOST": "garbage"},
        {"MCP_HTTP_PORT": "0"},
        {"MCP_HTTP_REQUEST_TIMEOUT": "nan"},
        {"MCP_HTTP_MAX_CONCURRENCY": "0"},
    ],
)
def test_configuration_fails_closed(env):
    with pytest.raises(ConfigurationError):
        load_remote_settings(env)
    assert load_remote_settings({}).host == "127.0.0.1"
    assert load_remote_settings({}).auth_mode == "deny"


def test_remote_rejects_windows_credentials_and_local_download_configuration():
    for extra in (
        {"CANVAS_CREDENTIAL_PROVIDER": "windows"},
        {"DOWNLOAD_DIRECTORY": "C:\\server\\downloads"},
    ):
        with pytest.raises(ConfigurationError):
            create_app(
                SETTINGS,
                environ={
                    "CANVAS_BASE_URL": "https://canvas.example.edu",
                    "CANVAS_ACCESS_TOKEN": CANVAS_TOKEN,
                    **extra,
                },
            )


def test_real_remote_startup_does_not_query_canvas_or_import_windows_store(monkeypatch):
    from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient

    async def forbidden(*args, **kwargs):
        raise AssertionError("Canvas contacted during startup")

    monkeypatch.setattr(CanvasHttpClient, "_get", forbidden)
    app = create_app(
        SETTINGS,
        environ={
            "CANVAS_BASE_URL": "https://canvas.example.edu",
            "CANVAS_ACCESS_TOKEN": CANVAS_TOKEN,
        },
        log_stream=io.StringIO(),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as connection:
        assert connection.get("/health").json() == {"status": "ok"}
        assert len(rpc(connection, "tools/list").json()["result"]["tools"]) == 14


@pytest.mark.parametrize(
    "arguments",
    [{"extra": CANVAS_TOKEN}, {"course_id": CANVAS_TOKEN}, {"course_id": True, "extra": DEV_TOKEN}],
)
def test_bad_tool_arguments_and_unknown_tools_do_not_echo_secrets(arguments):
    logs = io.StringIO()
    with client(logs=logs) as connection:
        bad = rpc(
            connection, "tools/call", {"name": "canvas_list_assignments", "arguments": arguments}
        )
        assert bad.json()["result"]["isError"]
        unknown = rpc(connection, "tools/call", {"name": CANVAS_TOKEN, "arguments": {}})
        assert unknown.json()["result"]["isError"]
        withheld = rpc(
            connection,
            "tools/call",
            {"name": "canvas_download_file", "arguments": ARGS["canvas_download_file"]},
        )
        assert withheld.json()["result"]["isError"]
    for secret in (CANVAS_TOKEN, DEV_TOKEN):
        assert secret not in bad.text + unknown.text + withheld.text + logs.getvalue()


def test_private_exception_strings_are_absent_from_http_and_logs(caplog):
    logs = io.StringIO()

    @asynccontextmanager
    async def factory():
        raise RuntimeError(
            f"{CANVAS_TOKEN} C:\\server\\file /tmp/private https://files.example/x?signature=PRIVATE"
        )
        yield

    with client(factory=factory, logs=logs) as connection:
        response = rpc(connection, "tools/call", {"name": "canvas_get_profile", "arguments": {}})
        assert response.json()["result"]["isError"]
    for private in (CANVAS_TOKEN, "C:\\server", "/tmp/private", "signature=PRIVATE", DEV_TOKEN):
        assert private not in response.text + logs.getvalue() + caplog.text


def test_signed_urls_and_paths_in_malformed_protocol_are_not_echoed(caplog):
    logs = io.StringIO()
    with client(logs=logs) as connection:
        response = connection.post(
            "/mcp",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": [
                            CANVAS_TOKEN,
                            "C:\\server\\private",
                            "https://signed.example/x?token=private",
                        ]
                    },
                }
            ),
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code >= 400 or "error" in response.json()
    for private in (CANVAS_TOKEN, "C:\\server", "signed.example", DEV_TOKEN):
        assert private not in response.text + logs.getvalue() + caplog.text


def test_late_host_logger_cannot_capture_private_sdk_validation_echoes():
    logs, host_logs = io.StringIO(), io.StringIO()
    connection = client(logs=logs)
    handler = logging.StreamHandler(host_logs)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with connection:
            for headers, content in (
                (
                    HEADERS,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": [CANVAS_TOKEN]},
                    },
                ),
                (
                    {**HEADERS, "Host": CANVAS_TOKEN},
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                ),
                (
                    {**HEADERS, "Origin": CANVAS_TOKEN},
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                ),
            ):
                response = connection.post("/mcp", json=content, headers=headers)
                assert CANVAS_TOKEN not in response.text
        assert CANVAS_TOKEN not in host_logs.getvalue() + logs.getvalue()
    finally:
        root.removeHandler(handler)


def test_development_response_guard_rejects_accidental_secret_echo():
    class Echo(FakeConnection):
        async def get_profile(self):
            from canvas_mcp.domain.models import Profile

            return result(Profile("7", ExternalText(CANVAS_TOKEN), obs("UTC")))

    @asynccontextmanager
    async def factory():
        yield Echo()

    with client(factory=factory) as connection:
        response = rpc(connection, "tools/call", {"name": "canvas_get_profile", "arguments": {}})
        assert response.status_code == 500
        assert CANVAS_TOKEN not in response.text


def test_remote_artifact_projection_is_allowlisted_and_stdio_keeps_path():
    value = DownloadedFile(
        "a" * 32,
        REFERENCE,
        "C:\\server\\download.pdf",
        ExternalText("x.pdf"),
        obs(None),
        "x.pdf",
        "application/pdf",
        obs(None),
        8,
        "0" * 64,
        0,
        NOW,
        "untrusted_document",
    )
    envelope = dto.envelope(result(value), dto.downloaded_file)
    assert dto.for_transport(envelope, "stdio")["data"]["managed_local_path"] == value.local_path
    remote = dto.for_transport(envelope, "http")
    assert set(remote["data"]) == {"artifact_id", "size", "sha256", "content_type", "trust"}
    assert "C:\\" not in json.dumps(remote)


def test_host_origin_and_forwarded_headers_do_not_bypass_rebinding_policy():
    with client() as connection:
        for extra in (
            {"Host": "evil.example", "X-Forwarded-Host": "127.0.0.1:8000"},
            {"Origin": "https://evil.example", "X-Forwarded-Proto": "https"},
            {"Origin": "null"},
        ):
            assert (
                rpc(connection, "tools/list", headers={**HEADERS, **extra}).status_code == 403
                or rpc(connection, "tools/list", headers={**HEADERS, **extra}).status_code == 421
            )
        assert (
            rpc(
                connection,
                "tools/list",
                headers={
                    **HEADERS,
                    "X-Forwarded-For": "1.2.3.4",
                    "X-Forwarded-Host": "evil.example",
                },
            ).status_code
            == 200
        )


def test_body_and_header_limits():
    with client() as connection:
        assert (
            connection.post(
                "/mcp", content=b"x" * (SETTINGS.max_body_bytes + 1), headers=HEADERS
            ).status_code
            == 413
        )
        assert (
            rpc(
                connection,
                "tools/list",
                headers={**HEADERS, "X-Private": "x" * SETTINGS.max_header_bytes},
            ).status_code
            == 431
        )
        assert (
            connection.post("/mcp", content=iter([b"x" * 32_768] * 3), headers=HEADERS).status_code
            == 413
        )


def test_request_timeout_bounds_tool_and_cleans_identity():
    @asynccontextmanager
    async def slow():
        await asyncio.sleep(1)
        yield FakeConnection()

    with client(
        settings=replace(SETTINGS, request_timeout_seconds=0.1), factory=slow
    ) as connection:
        assert (
            rpc(
                connection, "tools/call", {"name": "canvas_get_profile", "arguments": {}}
            ).status_code
            == 504
        )
        assert connection.get("/health").json() == {"status": "ok"}
    assert current_principal.get() is None


def test_http_concurrency_limit_and_identity_are_request_scoped():
    from canvas_mcp.mcp.http import HttpBoundary, DevelopmentAuthenticator
    from canvas_mcp.mcp.identity import McpPrincipal
    from canvas_mcp.domain.models import PrincipalId, ConnectionId

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        principal = McpPrincipal(PrincipalId("development"), ConnectionId("canvas"))

        async def application(scope, receive, send):
            assert current_principal.get() == principal
            entered.set()
            await release.wait()
            assert current_principal.get() == principal
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        boundary = HttpBoundary(
            application,
            replace(SETTINGS, max_concurrency=1),
            DevelopmentAuthenticator(DEV_TOKEN, principal),
            frozenset(),
            io.StringIO(),
        )
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", HEADERS["Authorization"].encode())],
        }

        async def receive():
            return {"type": "http.request", "body": b"{}"}

        first, second = [], []

        async def send_first(message):
            first.append(message)

        async def send_second(message):
            second.append(message)

        task = asyncio.create_task(boundary(scope, receive, send_first))
        await entered.wait()
        await boundary(scope, receive, send_second)
        assert second[0]["status"] == 503
        assert current_principal.get() is None
        release.set()
        await task
        assert first[0]["status"] == 200
        assert boundary._active == 0
        assert current_principal.get() is None

    asyncio.run(run())


def test_scoped_connection_registry_preserves_cursor_state_and_rejects_other_accounts(monkeypatch):
    from canvas_mcp import remote_composition
    from canvas_mcp.domain.errors import AuthorizationError
    from canvas_mcp.domain.models import ConnectionId, PrincipalId
    from canvas_mcp.infrastructure.config.schema import DeploymentSettings
    from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource
    from canvas_mcp.mcp.identity import McpPrincipal
    from canvas_mcp.remote_composition import DevelopmentCredentialProvider, ScopedConnections

    async def run():
        principal = McpPrincipal(PrincipalId("development"), ConnectionId("canvas"))
        source = EnvironmentCredentialSource.from_environment(
            principal.scope, {"CANVAS_ACCESS_TOKEN": CANVAS_TOKEN}
        )
        provider = DevelopmentCredentialProvider(principal.scope, source)
        opened, closed = [], []

        @asynccontextmanager
        async def composition(settings, scope, credentials, **kwargs):
            assert kwargs["local_downloads"] is False
            assert scope == principal.scope
            assert await credentials.get_access_token(scope) is not None
            opened.append(scope)
            connection = FakeConnection()
            connection.cursor_state = {}
            try:
                yield connection
            finally:
                closed.append(scope)

        monkeypatch.setattr(remote_composition, "open_scoped_connection", composition)
        registry = ScopedConnections(
            DeploymentSettings("https://canvas.example.edu"), provider, principal.scope
        )
        with pytest.raises(AuthorizationError):
            async with registry.connection():
                pass
        token = current_principal.set(principal)
        try:
            async with registry.connection() as first:
                first.cursor_state["cursor"] = "continuation"
            async with registry.connection() as second:
                assert second is first and second.cursor_state["cursor"] == "continuation"
            other = McpPrincipal(PrincipalId("other"), ConnectionId("other"))
            current_principal.set(other)
            with pytest.raises(AuthorizationError):
                async with registry.connection():
                    pass
            with pytest.raises(AuthorizationError):
                provider.for_scope(other.scope)
            with pytest.raises(AuthorizationError):
                await source.get_access_token(other.scope)
        finally:
            current_principal.reset(token)
            await registry.aclose()
        assert len(opened) == len(closed) == 1

    asyncio.run(run())
