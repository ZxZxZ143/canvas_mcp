"""Typed, fail-closed HTTP settings. Environment examples are never loaded implicitly."""

import ipaddress
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from canvas_mcp.domain.errors import ConfigurationError


@dataclass(frozen=True)
class RemoteSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    auth_mode: Literal["deny", "development", "oauth"] = "deny"
    deployment: Literal["local", "production"] = "local"
    development: bool = False
    dev_token: str = field(default="", repr=False)
    max_body_bytes: int = 65_536
    max_header_bytes: int = 16_384
    request_timeout_seconds: float = 65.0
    max_concurrency: int = 8

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            raise ConfigurationError() from None
        if (
            type(self.port) is not int
            or not 1 <= self.port <= 65_535
            or self.auth_mode not in ("deny", "development", "oauth")
            or self.deployment not in ("local", "production")
            or type(self.development) is not bool
            or (
                (self.auth_mode == "oauth" or self.deployment == "production")
                and (self.auth_mode == "development" or self.development or self.dev_token)
            )
            or (
                self.auth_mode == "development"
                and (
                    not self.development
                    or re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", self.dev_token) is None
                )
            )
            or type(self.max_body_bytes) is not int
            or not 16_384 <= self.max_body_bytes <= 1_048_576
            or type(self.max_header_bytes) is not int
            or not 1_024 <= self.max_header_bytes <= 65_536
            or not math.isfinite(self.request_timeout_seconds)
            or not 0 < self.request_timeout_seconds <= 300
            or type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= 64
        ):
            raise ConfigurationError()


def load_remote_settings(environ: Mapping[str, str] | None = None) -> RemoteSettings:
    env = os.environ if environ is None else environ
    try:
        mode = env.get("MCP_HTTP_AUTH_MODE", "deny")
        development = env.get("MCP_HTTP_DEVELOPMENT", "false")
        deployment = env.get("MCP_HTTP_DEPLOYMENT", "local")
        if mode not in ("deny", "development", "oauth") or development not in ("true", "false"):
            raise ConfigurationError()
        return RemoteSettings(
            host=env.get("MCP_HTTP_HOST", "127.0.0.1"),
            port=int(env.get("PORT", env.get("MCP_HTTP_PORT", "8000"))),
            auth_mode=cast(Literal["deny", "development", "oauth"], mode),
            deployment=cast(Literal["local", "production"], deployment),
            development=development == "true",
            dev_token=env.get("MCP_HTTP_DEV_TOKEN", ""),
            max_body_bytes=int(env.get("MCP_HTTP_MAX_BODY_BYTES", "65536")),
            max_header_bytes=int(env.get("MCP_HTTP_MAX_HEADER_BYTES", "16384")),
            request_timeout_seconds=float(env.get("MCP_HTTP_REQUEST_TIMEOUT", "65")),
            max_concurrency=int(env.get("MCP_HTTP_MAX_CONCURRENCY", "8")),
        )
    except (ValueError, TypeError, OverflowError):
        raise ConfigurationError() from None
