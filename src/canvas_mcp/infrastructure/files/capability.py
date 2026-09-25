"""Ephemeral, untrusted Canvas file capability; never an application result."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from canvas_mcp.domain.errors import (
    ConfigurationError,
    MalformedUpstreamError,
    PublicUrlOriginUnapprovedError,
)
from canvas_mcp.infrastructure.config.origin import normalize_origin
from canvas_mcp.infrastructure.files.policy import decoded_url_safe


@dataclass(frozen=True, repr=False)
class DownloadCapability:
    target: str = field(repr=False)


def _validated_target_and_origin(payload: object, secret: str) -> tuple[str, str]:
    """Validate the capability without disclosing its path or signed query."""
    invalid = False
    target = payload.get("public_url") if type(payload) is dict else None
    if type(target) is not str or not 1 <= len(target) <= 8192:
        raise MalformedUpstreamError()
    try:
        if (
            re.search(r"[\s\\\x00-\x1f\x7f]", target)
            or re.search(r"%(?![0-9a-fA-F]{2})", target)
            or "#" in target
            or not decoded_url_safe(target, secret)
        ):
            raise ValueError()
        parsed = urlsplit(target)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.path and not parsed.path.startswith("/"))
        ):
            raise ValueError()
        origin = normalize_origin(parsed.scheme + "://" + parsed.netloc)
        canonical = origin + (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    except (ValueError, UnicodeError, ConfigurationError):
        invalid = True
    if invalid:
        raise MalformedUpstreamError()
    return canonical, origin


def public_url_origin(payload: object, secret: str) -> str:
    """Return only the validated origin for an explicit local operator check."""
    _, origin = _validated_target_and_origin(payload, secret)
    return origin


def parse_public_url(
    payload: object, secret: str, approved_origins: tuple[str, ...]
) -> DownloadCapability:
    """Validate syntax and exact origin before an anonymous network request."""
    canonical, origin = _validated_target_and_origin(payload, secret)
    if origin not in frozenset(normalize_origin(item) for item in approved_origins):
        raise PublicUrlOriginUnapprovedError()
    return DownloadCapability(canonical)
