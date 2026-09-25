"""Exact public HTTPS origin validation; no DNS or environment access here."""

import ipaddress
import re
from urllib.parse import urlsplit

from canvas_mcp.domain.errors import ConfigurationError


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not (
            isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)
        )
    )


def is_internal_service_address(address: str) -> bool:
    """Only RFC1918 IPv4 and IPv6 ULA; exclude all other non-global ranges."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(
            ip in block
            for block in (
                ipaddress.IPv4Network("10.0.0.0/8"),
                ipaddress.IPv4Network("172.16.0.0/12"),
                ipaddress.IPv4Network("192.168.0.0/16"),
            )
        )
    return ip in ipaddress.IPv6Network("fc00::/7")


def normalize_trusted_private_ips(values: tuple[str, ...]) -> tuple[str, ...]:
    """Accept only exact RFC1918/ULA service literals, never ranges or special IPs."""
    if type(values) is not tuple or len(values) > 16:
        raise ConfigurationError()
    canonical: list[str] = []
    for value in values:
        if type(value) is not str or not value or len(value) > 64 or "%" in value:
            raise ConfigurationError()
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ConfigurationError() from None
        normalized = str(address)
        if not is_internal_service_address(normalized) or normalized in canonical:
            raise ConfigurationError()
        canonical.append(normalized)
    return tuple(canonical)


def canonical_resolved_address(value: str) -> str | None:
    if type(value) is not str or "%" in value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def configured_origin_address_allowed(address: str, trusted_private_ips: tuple[str, ...]) -> bool:
    canonical = canonical_resolved_address(address)
    return canonical is not None and (
        is_public_address(canonical)
        or is_internal_service_address(canonical)
        and canonical in trusted_private_ips
    )


def normalize_origin(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\%?#]", value):
        raise ConfigurationError()
    invalid = False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        invalid = True
    if invalid:
        raise ConfigurationError()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or port not in (None, 443)
        or parsed.netloc.endswith(":")
    ):
        raise ConfigurationError()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        if (
            len(host) > 253
            or "." not in host
            or host.endswith((".", ".local", ".internal", ".localhost"))
            or re.fullmatch(r"[0-9.]+", host)
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            )
        ):
            raise ConfigurationError()
    else:
        if not is_public_address(str(address)):
            raise ConfigurationError()
        host = f"[{address}]" if address.version == 6 else str(address)
    return f"https://{host}"
