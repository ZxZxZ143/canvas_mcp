"""DNS vetting at the actual HTTPCore connect boundary; original TLS SNI is retained."""

import asyncio
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpcore

from canvas_mcp.domain.errors import NetworkPolicyError
from canvas_mcp.infrastructure.config.origin import (
    canonical_resolved_address,
    configured_origin_address_allowed,
    normalize_origin,
    normalize_trusted_private_ips,
)


class PublicOriginBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, origin: str, *, trusted_private_ips: tuple[str, ...] = ()) -> None:
        self._host = urlsplit(normalize_origin(origin)).hostname
        self._trusted_private_ips = normalize_trusted_private_ips(trusted_private_ips)
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._host or port != 443 or local_address is not None:
            raise NetworkPolicyError()
        last_was_timeout = False
        async with asyncio.timeout(timeout):
            records = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            addresses = tuple(
                dict.fromkeys(canonical_resolved_address(str(item[4][0])) for item in records)
            )
            if not addresses or any(
                ip is None or not configured_origin_address_allowed(ip, self._trusted_private_ips)
                for ip in addresses
            ):
                raise NetworkPolicyError()
            for address in addresses:
                assert address is not None
                try:
                    # The delegate dials a vetted IP literal, never re-resolves host.
                    # HTTPCore later starts TLS using the original URL hostname.
                    return await self._backend.connect_tcp(
                        address,
                        port,
                        timeout=timeout,
                        socket_options=socket_options,
                    )
                except httpcore.ConnectTimeout:
                    last_was_timeout = True
                except httpcore.ConnectError:
                    last_was_timeout = False
        # Preserve the final failure category without retaining any delegate
        # exception text or chain (which can contain hostnames or addresses).
        if last_was_timeout:
            raise httpcore.ConnectTimeout("connection_timeout")
        raise httpcore.ConnectError("connection_failed")
