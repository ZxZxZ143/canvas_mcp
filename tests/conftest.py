import io
import json
import socket
import time
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpcore
import pytest

from canvas_mcp.application.connection import ConnectionService
from canvas_mcp.application.academic import AcademicService
from canvas_mcp.domain.models import (
    AccessScope,
    BudgetLimits,
    ConnectionId,
    PrincipalId,
    RequestBudget,
    RequestContext,
)
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.canvas.provider import CanvasProvider
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.logging.events import EventLogger

TOKEN = "SUPER_SECRET_CANVAS_TOKEN_12345"


@pytest.fixture(params=["sqlite", "postgresql"])
def state_repo(request, tmp_path):
    import asyncio
    import os
    from canvas_mcp.infrastructure.state.settings import StateSettings
    from canvas_mcp.infrastructure.state.sql import SQLiteStateRepository, PostgreSQLStateRepository

    if request.param == "sqlite":
        settings = StateSettings(
            f"sqlite:///{tmp_path / 'state.db'}", "sqlite", tmp_path / "state.db"
        )
        repo = SQLiteStateRepository(settings)
    else:
        url = os.environ.get("CANVAS_STATE_TEST_URL")
        if not url:
            pytest.skip("CANVAS_STATE_TEST_URL required for real PostgreSQL contract tests")
        settings = StateSettings.load({"STATE_DATABASE_URL": url})
        repo = PostgreSQLStateRepository(settings)
    asyncio.run(repo.migrate())
    return repo


ORIGIN = "https://canvas.example.edu"
PROFILE = {"id": 7, "name": "Student Name", "time_zone": "UTC", "login_id": "private@example.edu"}
COURSE = {"id": 8, "name": "Algorithms", "course_code": "CS101", "term": {"name": "Fall"}}


def response(payload=None, status=200, headers=(), body=None):
    data = json.dumps(payload).encode() if body is None else body
    fields = [(b"Content-Type", b"application/json"), (b"Content-Length", str(len(data)).encode())]
    fields.extend(headers)
    return (
        f"HTTP/1.1 {status} Response\r\n".encode()
        + b"\r\n".join(k + b": " + v for k, v in fields)
        + b"\r\n\r\n"
        + data
    )


def next_link(page=2, limit=25, origin=ORIGIN, active=True):
    from urllib.parse import urlencode

    params = [
        ("enrollment_type", "student"),
        ("include[]", "term"),
        ("per_page", str(limit)),
        ("page", str(page)),
    ]
    if active:
        params.append(("enrollment_state", "active"))
    return f'<{origin}/api/v1/courses?{urlencode(params)}>; rel="next"'.encode()


class RecordingStream(httpcore.AsyncMockStream):
    def __init__(self, replies, writes, sni):
        super().__init__(replies)
        self.writes = writes
        self.sni = sni

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.sni.append((server_hostname, ssl_context.check_hostname, ssl_context.verify_mode))
        return self


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, replies):
        self.replies = replies
        self.writes = []
        self.sni = []
        self.targets = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.targets.append((host, port))
        return RecordingStream(self.replies, self.writes, self.sni)


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests must not use real DNS/network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def stack():
    @asynccontextmanager
    async def make(replies, **overrides):
        settings = replace(DeploymentSettings(ORIGIN, max_get_attempts=1), **overrides)
        scope = AccessScope(PrincipalId("local"), ConnectionId(str(uuid4())))
        ctx = RequestContext(
            scope,
            str(uuid4()),
            datetime.now(timezone.utc),
            RequestBudget(BudgetLimits(100, 20, 131072), time.monotonic() + 60),
        )
        creds = EnvironmentCredentialSource.from_environment(scope, {"CANVAS_ACCESS_TOKEN": TOKEN})
        logs = io.StringIO()
        logger = EventLogger("DEBUG", logs)
        backend = RecordingBackend(list(replies))
        pool = httpcore.AsyncConnectionPool(network_backend=backend)
        client = CanvasHttpClient(settings, creds, logger, _pool=pool)
        provider = CanvasProvider(client, scope, settings, logger)
        try:
            yield SimpleNamespace(
                settings=settings,
                scope=scope,
                ctx=ctx,
                creds=creds,
                logs=logs,
                logger=logger,
                backend=backend,
                client=client,
                provider=provider,
                app=ConnectionService(provider),
                academic=AcademicService(provider),
            )
        finally:
            await provider.aclose()

    return make


@pytest.fixture
def academic_payloads():
    payloads = json.loads(
        (Path(__file__).parent / "fixtures" / "academic.json").read_text(encoding="utf-8")
    )
    payloads["assignment"]["submission"] = payloads["submission"]
    payloads["assignment"]["attachments"] = [payloads["attachment"]]
    payloads["sequence"] = {
        "modules": [payloads["module"]],
        "items": [{"prev": None, "current": payloads["module_item"], "next": None}],
    }
    return payloads
