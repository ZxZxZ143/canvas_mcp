"""Offline dev verification of the real four CLI paths using only synthetic data.

Only HTTP transport and the post-success stop are replaced. No network, runtime
directory, credential file, MIME/redirect diagnostic or live request is permitted.
Also runnable as an installed-package check with python [-I] scripts/auth_parity.py.
"""

import asyncio
import io
import os
import socket
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpcore

import canvas_mcp
from canvas_mcp import academic_smoke, composition, file_smoke, mime_probe, smoke
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource
from canvas_mcp.ports.credentials import AccessToken

FAKE_TOKEN = "FAKE_CANVAS_TOKEN_123456"
ORIGIN = "https://canvas.narxoz.kz"  # Never contacted; both spelling variants tested.
CLIS = (smoke, academic_smoke, file_smoke, mime_probe)


class ProfileComplete(Exception):
    """Stop after a real, successfully normalized mocked profile, before discovery."""


class RecordingStream(httpcore.AsyncMockStream):
    def __init__(self, reply, writes):
        super().__init__([reply])
        self.writes = writes

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self


class MockBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, status):
        body = b'{"id":7,"name":"Synthetic student","time_zone":"UTC"}'
        self.reply = (
            f"HTTP/1.1 {status} Mock\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        self.writes = []
        self.targets = []

    async def connect_tcp(self, host, port, **kwargs):
        self.targets.append((host, port))
        return RecordingStream(self.reply, self.writes)


def forbid(*args, **kwargs):
    raise AssertionError("offline check forbids network or diagnostic storage")


async def exercise(module, *, origin=ORIGIN, profile="personal", status=200, token=FAKE_TOKEN):
    """Return booleans only, even on mismatches; never return request/header bytes."""
    backend = MockBackend(status)
    seen = []
    output = io.StringIO()
    profiles = []
    env = {"CANVAS_BASE_URL": origin, "CANVAS_APPLICATION_PROFILE": profile}
    if token is not None:
        env["CANVAS_ACCESS_TOKEN"] = token

    def client(settings, credentials, logger):
        # Preserve the REAL composition, environment adapter, scope and API client.
        instance = CanvasHttpClient(
            settings,
            credentials,
            logger,
            _pool=httpcore.AsyncConnectionPool(network_backend=backend),
        )
        seen.append((settings, credentials, instance))
        return instance

    original_profile = composition.CanvasConnection.get_profile

    async def stop_after_profile(connection):
        result = await original_profile(connection)
        profiles.append(result.data.id == "7")
        raise ProfileComplete()

    # Set environment AFTER importing all CLIs to detect import-time snapshots.
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(composition, "CanvasHttpClient", client),
        patch.object(composition.CanvasConnection, "get_profile", stop_after_profile),
        patch.object(mime_probe, "MimeRuntime", forbid),
        patch.object(socket, "getaddrinfo", forbid),
        patch.object(socket.socket, "connect", forbid),
        redirect_stdout(output),
        redirect_stderr(output),
    ):
        code = await (
            module.run(allow_mismatch=True, redirect_topology=True)
            if module is mime_probe
            else module.run()
        )

    captured = output.getvalue()
    no_leak = (
        FAKE_TOKEN not in captured
        and "Authorization" not in captured
        and "Bearer " not in captured
        and ORIGIN not in captured
    )
    if token in (None, ""):
        return {
            "no_contact": not backend.targets and not backend.writes and not seen,
            "safe_failure": code == 1 and "configuration_error" in captured and no_leak,
        }
    wire = b"".join(backend.writes)
    expected_auth = b"Authorization: Bearer " + FAKE_TOKEN.encode() + b"\r\n"
    provider_received = False
    client_received = False
    if len(seen) == 1:
        settings, credentials, instance = seen[0]
        received = await credentials.get_access_token(credentials._scope)
        provider_received = (
            isinstance(credentials, EnvironmentCredentialSource)
            and isinstance(received, AccessToken)
            and received.value == FAKE_TOKEN
        )
        client_received = instance._credentials is credentials
    else:
        settings = None
    expected_reason = {
        200: "internal_error",
        401: "authentication_error",
        403: "authorization_error",
    }
    return {
        "method_path": wire.startswith(b"GET /api/v1/users/self HTTP/1.1\r\n"),
        "origin": (
            settings is not None
            and settings.canvas_origin == ORIGIN
            and backend.targets == [("canvas.narxoz.kz", 443)]
            and wire.count(b"Host: canvas.narxoz.kz\r\n") == 1
        ),
        "authorization": wire.count(expected_auth) == 1 and wire.count(b"Authorization:") == 1,
        "provider_received": provider_received,
        "client_received": client_received,
        "profile_outcome": profiles == ([True] if status == 200 else []),
        "expected_stop": code == 1 and expected_reason[status] in captured,
        "no_leak": no_leak,
    }


def package_source() -> str:
    root = Path(canvas_mcp.__file__).resolve().parent
    if root == Path(__file__).resolve().parents[1] / "src" / "canvas_mcp":
        return "project_source"
    if "site-packages" in root.parts:
        return "installed_wheel"
    return "other"


async def verify() -> bool:
    for module in CLIS:
        for origin in (ORIGIN, ORIGIN + "/"):
            for profile in ("personal", "different-profile"):
                for status in (200, 401, 403):
                    if not all(
                        (
                            await exercise(module, origin=origin, profile=profile, status=status)
                        ).values()
                    ):
                        return False
        for token in (None, ""):
            if not all((await exercise(module, token=token)).values()):
                return False
    return True


def main() -> int:
    def audit(event, args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            forbid()

    async def guarded_verify():
        # Windows asyncio creates a local wakeup socket pair when the loop starts.
        # Install the audit guard AFTER that, before any composition/test work.
        sys.addaudithook(audit)
        return await verify()

    try:
        passed = asyncio.run(guarded_verify())
    except Exception:
        passed = False
    print("package_source = " + package_source())
    print("authentication_parity = " + str(passed).lower())
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
