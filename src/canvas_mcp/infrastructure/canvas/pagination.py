"""Strict Link parsing and route/query confinement shared by paginated endpoints."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from canvas_mcp.domain.errors import MalformedUpstreamError


def fingerprint(target: str) -> str:
    return hashlib.sha256(target.encode("ascii")).hexdigest()


def next_target(
    headers: list[tuple[bytes, bytes]],
    origin: str,
    path: str,
    parameters: tuple[tuple[str, str], ...],
    current: str,
) -> str | None:
    raw = b",".join(value for key, value in headers if key.lower() == b"link")
    if not raw:
        return None
    if len(raw) > 8192:
        raise MalformedUpstreamError()
    try:
        header = raw.decode("ascii")
    except UnicodeError:
        raise MalformedUpstreamError() from None
    # Split only at link boundaries, not commas inside opaque query tokens.
    entries = re.split(r",\s*(?=<)", header)
    result: str | None = None
    for entry in entries:
        match = re.fullmatch(r'\s*<([^<>\s]+)>\s*;\s*rel="([a-z ]+)"\s*', entry)
        if not match:
            raise MalformedUpstreamError()
        target, relations = match.groups()
        if "next" not in relations.split():
            continue
        if result is not None:
            raise MalformedUpstreamError()
        result = validate_target(target, origin, path, parameters)
        if result == current:
            raise MalformedUpstreamError()
    return result


def validate_target(
    target: str,
    origin: str,
    path: str,
    parameters: tuple[tuple[str, str], ...],
) -> str:
    if len(target) > 2048 or re.search(r"[\s\\]", target):
        raise MalformedUpstreamError()
    try:
        parts = urlsplit(target)
        if (
            f"{parts.scheme}://{parts.netloc}" != origin
            or parts.path != path
            or parts.fragment
            or parts.username is not None
            or re.search(r"%(?![0-9a-fA-F]{2})", parts.query)
        ):
            raise MalformedUpstreamError()
        query = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
            encoding="utf-8",
            errors="strict",
        )
        fixed = tuple(sorted((k, v) for k, v in query if k != "page"))
        pages = [v for k, v in query if k == "page"]
        if (
            fixed != tuple(sorted(parameters))
            or len(pages) != 1
            or not pages[0]
            or len(pages[0]) > 512
            or re.search(r"[\x00-\x20\x7f]", pages[0])
        ):
            raise MalformedUpstreamError()
        return origin + path + "?" + urlencode(sorted(query))
    except (ValueError, UnicodeError):
        raise MalformedUpstreamError() from None
