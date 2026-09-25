"""Private service opt-in must stay on the configured Canvas origin and hop zero."""

import asyncio
import io
import json
import socket
import ssl

import httpcore
import pytest

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError, NetworkPolicyError
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.canvas.academic_mapping import references
from canvas_mcp.infrastructure.canvas import transport_diagnostic as diag
from canvas_mcp import transport_probe as probe
from canvas_mcp.infrastructure.config.environment import load_settings
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource
from canvas_mcp.infrastructure.config.origin import is_internal_service_address
from canvas_mcp.infrastructure.files.download import CanvasDownloadClient, DownloadBackend
from canvas_mcp.infrastructure.logging.events import EventLogger
from canvas_mcp.domain.models import AccessScope, ConnectionId, PrincipalId
from conftest import ORIGIN, PROFILE, TOKEN, RecordingBackend, response
from test_transport_probe import Backend, install_backends, install_dns
from canvas_mcp.ports.credentials import AccessToken


@pytest.mark.parametrize("value", ["", "TRUE", "False", "1", "yes", " true ", "on"])
def test_private_flag_rejects_ambiguous_values(value):
    with pytest.raises(ConfigurationError):
        load_settings({"CANVAS_BASE_URL": ORIGIN, "CANVAS_ALLOW_PRIVATE_ORIGIN": value})


def test_private_origin_requires_exact_ips_and_rejects_private_ip_literal():
    assert load_settings({"CANVAS_BASE_URL": ORIGIN}).allow_private_origin is False
    with pytest.raises(ConfigurationError):
        load_settings({"CANVAS_BASE_URL": ORIGIN, "CANVAS_ALLOW_PRIVATE_ORIGIN": "true"})
    assert (
        load_settings(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200"}
        ).allow_private_origin
        is True
    )
    with pytest.raises(ConfigurationError):
        load_settings(
            {
                "CANVAS_BASE_URL": "https://192.168.4.200",
                "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
            }
        )


@pytest.mark.parametrize(
    "address,expected",
    [
        ("192.168.4.200", True),
        ("10.2.3.4", True),
        ("172.16.0.1", True),
        ("fd00::5", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("169.254.169.254", False),
        ("fe80::1", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("::ffff:192.168.4.200", False),
    ],
)
def test_only_internal_service_address_categories(address, expected):
    assert is_internal_service_address(address) is expected


@pytest.mark.parametrize("opted_in", [False, True])
def test_api_private_dns_requires_opt_in_and_preserves_tls_identity(monkeypatch, stack, opted_in):
    async def run():
        async with stack([response(PROFILE)]) as s:
            backend = PublicOriginBackend(
                ORIGIN, trusted_private_ips=("192.168.4.200",) if opted_in else ()
            )
            dial = RecordingBackend([response(PROFILE)])
            backend._backend = dial

            async def dns(host, port, **kwargs):
                assert host == "canvas.example.edu" and port == 443
                return [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("192.168.4.200", 443),
                    )
                ]

            monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
            await s.client._pool.aclose()
            s.client._pool = httpcore.AsyncConnectionPool(network_backend=backend)
            if opted_in:
                result = await s.app.get_profile(s.ctx)
                assert result.data.id == "7"
                assert dial.targets == [("192.168.4.200", 443)]
                assert dial.sni == [("canvas.example.edu", True, ssl.CERT_REQUIRED)]
                wire = b"".join(dial.writes)
                assert b"Host: canvas.example.edu\r\n" in wire
                assert b"Host: 192.168.4.200" not in wire
            else:
                with pytest.raises(NetworkPolicyError) as caught:
                    await s.client.get_profile(s.ctx)
                assert caught.value.diagnostic_code == "network_policy_error"
                assert not dial.targets

    asyncio.run(run())


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "224.0.0.1",
        "0.0.0.0",
    ],
)
def test_opt_in_still_rejects_non_service_addresses(monkeypatch, address):
    async def run():
        backend = PublicOriginBackend(ORIGIN, trusted_private_ips=("192.168.4.200",))
        dial = RecordingBackend([])
        backend._backend = dial

        async def dns(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        with pytest.raises(NetworkPolicyError):
            await backend.connect_tcp("canvas.example.edu", 443)
        assert not dial.targets

    asyncio.run(run())


def test_other_private_host_and_redirect_backend_reject_private(monkeypatch):
    async def run():
        canvas = PublicOriginBackend(ORIGIN, trusted_private_ips=("192.168.4.200",))
        redirects = DownloadBackend(frozenset((ORIGIN, "https://other.example")))

        async def dns(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.20", 443))
            ]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        with pytest.raises(NetworkPolicyError):
            await canvas.connect_tcp("other.example", 443)
        with pytest.raises(NetworkPolicyError):
            await redirects.connect_tcp("other.example", 443)
        with pytest.raises(NetworkPolicyError):
            await redirects.connect_tcp("canvas.example.edu", 443)

    asyncio.run(run())


def test_canvas_derived_private_link_stays_inert_with_opt_in():
    settings = load_settings(
        {
            "CANVAS_BASE_URL": ORIGIN,
            "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
        }
    )
    items = references(
        '<a href="https://192.168.5.10/private">internal</a>',
        settings.canvas_origin,
        "8",
    ).value
    assert items is not None and len(items) == 1
    assert items[0].kind == "external" and items[0].state == "not_fetched"
    assert items[0].target_id is None


def test_http_403_remains_authorization_error_with_opt_in(stack):
    async def run():
        async with stack(
            [response({}, 403)],
            allow_private_origin=True,
            trusted_private_ips=("192.168.4.200",),
        ) as s:
            with pytest.raises(AuthorizationError):
                await s.client.get_profile(s.ctx)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode", [diag.Mode.LIBRARY, diag.Mode.HEADERS, diag.Mode.TRANSPORT, diag.Mode.CLIENT]
)
def test_transport_stages_reach_http_with_private_opt_in(monkeypatch, mode):
    backend = Backend(response(PROFILE), peer="192.168.4.200")
    install_backends(monkeypatch, backend)

    async def run():
        await install_dns(monkeypatch, "192.168.4.200")
        async with open_canvas_connection(
            {
                "CANVAS_BASE_URL": ORIGIN,
                "CANVAS_ACCESS_TOKEN": TOKEN,
                "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
            },
            log_stream=io.StringIO(),
        ) as connection:
            result = await probe.core_stage(connection, AccessToken(TOKEN), mode, 2)
        assert result.status == 200 and result.failure == "none"
        assert result.tcp_destination == "internal_ip"
        assert result.host_correct and result.sni_correct and result.tls_verified
        assert backend.sni == ["canvas.example.edu"]

    asyncio.run(run())


def test_private_config_event_is_fixed_and_emitted_once():
    async def run():
        log = io.StringIO()
        async with open_canvas_connection(
            {
                "CANVAS_BASE_URL": ORIGIN,
                "CANVAS_ACCESS_TOKEN": TOKEN,
                "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
                "LOG_LEVEL": "ERROR",
            },
            log_stream=log,
        ):
            pass
        assert [json.loads(line) for line in log.getvalue().splitlines()] == [
            {"event": "configured_private_origin_enabled"}
        ]

    asyncio.run(run())


def test_download_hop_zero_has_separate_private_pool():
    async def run():
        settings = load_settings(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200"}
        )
        scope = AccessScope(PrincipalId("local"), ConnectionId("test"))
        credentials = EnvironmentCredentialSource.from_environment(
            scope, {"CANVAS_ACCESS_TOKEN": TOKEN}
        )
        logger = EventLogger(stream=io.StringIO())
        api = CanvasHttpClient(settings, credentials, logger)
        download = CanvasDownloadClient(settings, api, logger)
        assert download._initial_pool is not None
        assert download._initial_pool is not download._pool
        assert download._initial_pool._network_backend._trusted_private_ips == ("192.168.4.200",)
        assert (
            download._pool._network_backend._backends["canvas.example.edu"]._trusted_private_ips
            == ()
        )
        await download.aclose()
        await api.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "224.0.0.1",
        "0.0.0.0",
        "::",
        "::ffff:192.168.4.200",
        "8.8.8.8",
        "192.168.4.0/24",
        "canvas.narxoz.kz",
        "fd00::1%eth0",
        "192.168.4.200,",
    ],
)
def test_trusted_private_ips_reject_unsafe_or_nonliteral_entries(value):
    with pytest.raises(ConfigurationError):
        load_settings({"CANVAS_BASE_URL": ORIGIN, "CANVAS_TRUSTED_PRIVATE_IPS": value})


def test_legacy_private_boolean_only_works_with_exact_ips():
    settings = load_settings(
        {
            "CANVAS_BASE_URL": ORIGIN,
            "CANVAS_ALLOW_PRIVATE_ORIGIN": "true",
            "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
        }
    )
    assert settings.trusted_private_ips == ("192.168.4.200",)


@pytest.mark.parametrize(
    "addresses,allowed,selected",
    [
        (["192.168.4.200"], True, "192.168.4.200"),
        (["192.168.4.201"], False, None),
        (["8.8.8.8"], True, "8.8.8.8"),
        (["1.1.1.1"], True, "1.1.1.1"),
        (["8.8.8.8", "192.168.4.201"], False, None),
        (["8.8.8.8", "192.168.4.200"], True, "8.8.8.8"),
    ],
)
def test_split_horizon_dns_answers_are_all_vetted_before_dial(
    monkeypatch, addresses, allowed, selected
):
    async def run():
        backend = PublicOriginBackend(ORIGIN, trusted_private_ips=("192.168.4.200",))
        dial = RecordingBackend([])
        backend._backend = dial

        async def dns(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
                for ip in addresses
            ]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        if allowed:
            await backend.connect_tcp("canvas.example.edu", 443)
            assert dial.targets == [(selected, 443)]
        else:
            with pytest.raises(NetworkPolicyError):
                await backend.connect_tcp("canvas.example.edu", 443)
            assert not dial.targets

    asyncio.run(run())


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1"])
def test_public_dns_needs_no_private_trust_or_static_public_pin(monkeypatch, address):
    async def run():
        backend = PublicOriginBackend(ORIGIN)
        dial = RecordingBackend([])
        backend._backend = dial

        async def dns(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        await backend.connect_tcp("canvas.example.edu", 443)
        assert dial.targets == [(address, 443)]

    asyncio.run(run())
