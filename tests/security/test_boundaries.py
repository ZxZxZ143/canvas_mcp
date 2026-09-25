import ast
import asyncio
import socket
import ssl
from pathlib import Path

import httpcore
import pytest

from canvas_mcp.domain.errors import MalformedUpstreamError, NetworkPolicyError
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.canvas.pagination import validate_target
from canvas_mcp.infrastructure.config.origin import is_public_address
from conftest import ORIGIN, PROFILE, RecordingBackend, response


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "::ffff:8.8.8.8",
        "100.64.0.1",
        "192.0.2.1",
        "2001:db8::1",
        "fd00::1",
        "2002:7f00:1::",
        "not-an-address",
    ],
)
def test_nonpublic_destinations_rejected(address):
    assert not is_public_address(address)


def test_dns_is_pinned_and_tls_uses_original_hostname(monkeypatch, stack):
    async def run():
        async with stack([]) as s:
            backend = PublicOriginBackend(ORIGIN)
            dial = RecordingBackend([response(PROFILE)])
            backend._backend = dial
            resolutions = []

            async def dns(host, port, **kwargs):
                resolutions.append(host)
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
                ]

            monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
            pool = httpcore.AsyncConnectionPool(network_backend=backend)
            await s.client._pool.aclose()
            s.client._pool = pool
            result = await s.app.get_profile(s.ctx)
            assert result.data.id == "7"
            assert resolutions == ["canvas.example.edu"]
            assert dial.targets == [("8.8.8.8", 443)]
            assert dial.sni == [("canvas.example.edu", True, ssl.CERT_REQUIRED)]
            wire = b"".join(dial.writes)
            assert b"Host: canvas.example.edu\r\n" in wire
            assert b"Host: 8.8.8.8" not in wire
            assert wire.startswith(b"GET /api/v1/users/self HTTP/1.1\r\n")

    asyncio.run(run())


def test_dns_private_or_mixed_answers_fail_before_dial(monkeypatch):
    async def run():
        backend = PublicOriginBackend(ORIGIN)
        dial = RecordingBackend([])
        backend._backend = dial

        async def dns(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
                for ip in ("8.8.8.8", "127.0.0.1")
            ]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        with pytest.raises(NetworkPolicyError):
            await backend.connect_tcp("canvas.example.edu", 443)
        with pytest.raises(NetworkPolicyError):
            await backend.connect_tcp("evil.example", 443)
        assert not dial.targets

    asyncio.run(run())


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/api/v1/courses?page=2&per_page=25",
        "https://canvas.example.edu/api/v1/users/self?page=2&per_page=25",
        "https://canvas.example.edu/api/v1/courses?page=2&per_page=25&access_token=secret",
        "https://canvas.example.edu/api/v1/courses?page=2&per_page=100",
        "https://canvas.example.edu/api/v1/courses?page=2&page=3&per_page=25",
        "https://canvas.example.edu/api/v1/courses?page=2&per_page=25#fragment",
        "https://user@canvas.example.edu/api/v1/courses?page=2&per_page=25",
        "https://canvas.example.edu/api/v1/courses?page=%xx&per_page=25",
        "https://canvas.example.edu/api/v1/courses?page=%0d&per_page=25",
    ],
)
def test_pagination_policy(target):
    with pytest.raises(MalformedUpstreamError):
        validate_target(target, ORIGIN, "/api/v1/courses", (("per_page", "25"),))


def test_dependency_direction_and_mcp_adapter_boundary():
    root = Path(__file__).parents[2] / "src" / "canvas_mcp"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        imports += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        area = path.relative_to(root).parts[0]
        if area == "domain":
            assert not any(
                name.startswith(
                    (
                        "canvas_mcp.application",
                        "canvas_mcp.infrastructure",
                        "canvas_mcp.mcp",
                        "httpcore",
                        "httpx",
                        "os",
                    )
                )
                for name in imports
            )
        if area == "application":
            assert not any(
                name.startswith(
                    (
                        "canvas_mcp.infrastructure",
                        "canvas_mcp.mcp",
                        "httpcore",
                        "httpx",
                        "os",
                        "pathlib",
                    )
                )
                for name in imports
            )
        if area == "mcp" or path.name == "mcp_server.py":
            assert not any(
                name.startswith(
                    (
                        "canvas_mcp.infrastructure.canvas",
                        "canvas_mcp.infrastructure.files",
                        "httpcore",
                        "httpx",
                        "requests",
                        "urllib",
                    )
                )
                for name in imports
            )
