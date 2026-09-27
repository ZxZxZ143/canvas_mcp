"""Inspector-only loopback fixture. No credentials, Canvas calls or public bind."""

import io
from contextlib import asynccontextmanager

import uvicorn

from canvas_mcp.infrastructure.config.remote import RemoteSettings
from canvas_mcp.mcp_http_server import create_app
from fake_server import FakeConnection


@asynccontextmanager
async def fake_connection():
    yield FakeConnection()


if __name__ == "__main__":
    settings = RemoteSettings(
        auth_mode="development", development=True, dev_token="SYNTHETIC_DEV_AUTH_TOKEN_1234567890"
    )
    app = create_app(
        settings, environ={}, connection_factory=fake_connection, log_stream=io.StringIO()
    )
    uvicorn.run(
        app, host="127.0.0.1", port=8000, proxy_headers=False, access_log=False, log_level="warning"
    )
