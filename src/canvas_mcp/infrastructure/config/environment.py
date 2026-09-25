"""Explicit environment loading; no dotenv, hot reload or import-time access."""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, Literal

from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError
from canvas_mcp.domain.models import AccessScope
from canvas_mcp.infrastructure.config.origin import (
    normalize_origin,
    normalize_trusted_private_ips,
)
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.ports.credentials import AccessToken


def load_settings(environ: Mapping[str, str] | None = None) -> DeploymentSettings:
    env = os.environ if environ is None else environ
    invalid = False
    try:
        origin = normalize_origin(env.get("CANVAS_BASE_URL", ""))
        private_value = env.get("CANVAS_ALLOW_PRIVATE_ORIGIN", "false")
        if private_value not in ("true", "false"):
            raise ConfigurationError()
        trusted_value = env.get("CANVAS_TRUSTED_PRIVATE_IPS", "")
        if not isinstance(trusted_value, str) or len(trusted_value) > 2048:
            raise ConfigurationError()
        trusted_private_ips = normalize_trusted_private_ips(
            tuple(item.strip() for item in trusted_value.split(","))
            if trusted_value.strip()
            else ()
        )
        # The legacy boolean remains accepted only alongside exact IPs. It
        # never grants broad RFC1918/ULA access by itself.
        if private_value == "true" and not trusted_private_ips:
            raise ConfigurationError()
        allow_private_origin = bool(trusted_private_ips)
        timeout = float(env.get("REQUEST_TIMEOUT", "20"))
        download_timeout = float(env.get("DOWNLOAD_TIMEOUT", "120"))
        download_redirects = int(env.get("MAX_DOWNLOAD_REDIRECTS", "3"))
        cache_ttl = int(env.get("CACHE_TTL", "0"))
        max_bytes = int(env.get("MAX_DOWNLOAD_BYTES", "26214400"))
        level = env.get("LOG_LEVEL", "INFO").upper()
        if (
            not math.isfinite(timeout)
            or not 0 < timeout <= 60
            or not math.isfinite(download_timeout)
            or not 0 < download_timeout <= 300
            or not 0 <= download_redirects <= 3
            or cache_ttl != 0
            or not 0 < max_bytes <= 262_144_000
            or level not in ("DEBUG", "INFO", "WARNING", "ERROR")
        ):
            raise ConfigurationError()
        directory = env.get("DOWNLOAD_DIRECTORY", "")
        path = Path(directory) if directory else None
        if path is not None and not path.is_absolute():
            raise ConfigurationError()
        origins = tuple(
            normalize_origin(item.strip())
            for item in env.get("CANVAS_DOWNLOAD_ORIGINS", "").split(",")
            if item.strip()
        )
    except (ValueError, TypeError, OSError):
        invalid = True
    if invalid:
        raise ConfigurationError()
    return DeploymentSettings(
        canvas_origin=origin,
        allow_private_origin=allow_private_origin,
        trusted_private_ips=trusted_private_ips,
        download_directory=path,
        download_origins=origins,
        request_timeout_seconds=timeout,
        download_timeout_seconds=download_timeout,
        max_redirects=download_redirects,
        cache_ttl_seconds=cache_ttl,
        max_download_bytes=max_bytes,
        log_level=cast(Literal["DEBUG", "INFO", "WARNING", "ERROR"], level),
    )


@dataclass(frozen=True, repr=False)
class EnvironmentCredentialSource:
    _scope: AccessScope
    _token: AccessToken = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        scope: AccessScope,
        environ: Mapping[str, str] | None = None,
    ) -> "EnvironmentCredentialSource":
        env = os.environ if environ is None else environ
        value = env.get("CANVAS_ACCESS_TOKEN", "")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]{1,4096}=*", value):
            raise ConfigurationError()
        if len(value) > 4096:
            raise ConfigurationError()
        return cls(scope, AccessToken(value))

    async def get_access_token(self, scope: AccessScope) -> AccessToken:
        if scope != self._scope:
            raise AuthorizationError()
        return self._token
