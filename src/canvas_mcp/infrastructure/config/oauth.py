"""Explicit personal OAuth configuration; no management/client credentials."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from canvas_mcp.domain.errors import ConfigurationError
from canvas_mcp.infrastructure.config.origin import normalize_origin


@dataclass(frozen=True)
class OAuthSettings:
    issuer: str
    audience: str
    public_base_url: str
    allowed_subject: str = field(repr=False)
    required_scopes: tuple[str, ...] = ("canvas:read",)

    def __post_init__(self) -> None:
        origin = normalize_origin(self.issuer.removesuffix("/"))
        public = normalize_origin(self.public_base_url)
        if (
            self.issuer != origin + "/"
            or self.public_base_url != public
            or self.audience != public + "/mcp"
            or not 1 <= len(self.allowed_subject) <= 256
            or not self.allowed_subject.isascii()
            or not self.allowed_subject.isprintable()
            or self.required_scopes != ("canvas:read",)
        ):
            raise ConfigurationError()

    @property
    def authority(self) -> str:
        return urlsplit(self.public_base_url).netloc

    @property
    def metadata_url(self) -> str:
        return self.public_base_url + "/.well-known/oauth-protected-resource/mcp"

    def challenge(self, error: str | None = None) -> str:
        value = f'Bearer resource_metadata="{self.metadata_url}", scope="canvas:read"'
        if error in ("invalid_token", "insufficient_scope"):
            value += f', error="{error}"'
        return value


def load_oauth_settings(environ: Mapping[str, str]) -> OAuthSettings:
    domain = environ.get("AUTH0_DOMAIN", "")
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) is None:
        raise ConfigurationError()
    if environ.get("AUTH0_ISSUER", f"https://{domain}/") != f"https://{domain}/":
        raise ConfigurationError()
    return OAuthSettings(
        issuer=environ.get("AUTH0_ISSUER", f"https://{domain}/"),
        audience=environ.get("AUTH0_AUDIENCE", ""),
        public_base_url=environ.get("MCP_PUBLIC_BASE_URL", ""),
        allowed_subject=environ.get("AUTH0_ALLOWED_SUBJECT", ""),
        required_scopes=tuple(environ.get("AUTH0_REQUIRED_SCOPES", "canvas:read").split()),
    )
