"""Bounded Auth0 discovery/JWKS and RS256 verification. Never stores bearer tokens."""

import asyncio
import hmac
import json
import re
import time
from typing import Any

import httpcore
import jwt

from canvas_mcp.domain.models import ConnectionId, PrincipalId
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.config.oauth import OAuthSettings
from canvas_mcp.mcp.identity import McpPrincipal, OAuthRejection


class Auth0Authenticator:
    CACHE_SECONDS = 300
    ROTATION_COOLDOWN_SECONDS = 30

    def __init__(
        self, settings: OAuthSettings, *, pool: httpcore.AsyncConnectionPool | None = None
    ) -> None:
        self.settings = settings
        self._pool = pool or httpcore.AsyncConnectionPool(
            network_backend=PublicOriginBackend(settings.issuer.removesuffix("/")),
            max_connections=2,
            max_keepalive_connections=2,
        )
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires = 0.0
        self._last_fetch = float("-inf")
        self._fetch_failed = False
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._pool.aclose()

    async def _document(self, path: str) -> dict[str, Any]:
        # Both paths are fixed, and DNS is vetted/pinned at the connect boundary.
        async with asyncio.timeout(5):
            async with self._pool.stream(
                "GET",
                self.settings.issuer + path,
                headers={"Accept": "application/json"},
                extensions={"timeout": {"connect": 3, "read": 3, "write": 3, "pool": 3}},
            ) as response:
                if response.status != 200:
                    raise ValueError()
                body = bytearray()
                async for chunk in response.aiter_stream():
                    body.extend(chunk)
                    if len(body) > 65_536:
                        raise ValueError()
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value

    async def _key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            now = time.monotonic()
            if now < self._expires and kid in self._keys:
                return self._keys[kid]
            if now - self._last_fetch < self.ROTATION_COOLDOWN_SECONDS:
                if self._fetch_failed:
                    raise OAuthRejection(503, "oauth_provider_unavailable")
                raise OAuthRejection(401, "invalid_token", self.settings.challenge("invalid_token"))
            self._last_fetch = now
            try:
                metadata = await self._document(".well-known/openid-configuration")
                methods = metadata.get("code_challenge_methods_supported")
                if (
                    metadata.get("issuer") != self.settings.issuer
                    or metadata.get("jwks_uri") != self.settings.issuer + ".well-known/jwks.json"
                    or not isinstance(methods, list)
                    or any(not isinstance(method, str) for method in methods)
                    or "S256" not in methods
                    or metadata.get("authorization_endpoint") != self.settings.issuer + "authorize"
                    or metadata.get("token_endpoint") != self.settings.issuer + "oauth/token"
                ):
                    raise ValueError()
                document = await self._document(".well-known/jwks.json")
                keys = document.get("keys")
                if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
                    raise ValueError()
                refreshed: dict[str, jwt.PyJWK] = {}
                for key in keys:
                    if (
                        not isinstance(key, dict)
                        or key.get("kty") != "RSA"
                        or key.get("use", "sig") != "sig"
                        or key.get("alg", "RS256") != "RS256"
                        or re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", key.get("kid", "")) is None
                        or key["kid"] in refreshed
                        or "d" in key
                    ):
                        raise ValueError()
                    parsed = jwt.PyJWK.from_dict(key, algorithm="RS256")
                    if parsed.key.key_size < 2048:
                        raise ValueError()
                    refreshed[key["kid"]] = parsed
                self._keys, self._expires = refreshed, now + self.CACHE_SECONDS
                self._fetch_failed = False
            except OAuthRejection:
                raise
            except Exception:
                self._fetch_failed = True
                raise OAuthRejection(503, "oauth_provider_unavailable") from None
            if kid not in self._keys:
                raise OAuthRejection(401, "invalid_token", self.settings.challenge("invalid_token"))
            return self._keys[kid]

    async def authenticate(self, authorization: str | None) -> McpPrincipal | None:
        if authorization is None:
            raise OAuthRejection(401, "authentication_required", self.settings.challenge())
        try:
            if (
                re.fullmatch(
                    r"(?i:Bearer) [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", authorization
                )
                is None
            ):
                raise ValueError()
            token = authorization.split(" ", 1)[1]
            if len(token) > 8192:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            kid = header.get("kid", "")
            if header.get("alg") != "RS256" or re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", kid) is None:
                raise ValueError()
            key = await self._key(kid)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            # Auth0 can add only its UserInfo audience when OIDC scopes are used.
            audiences = claims["aud"]
            if audiences != self.settings.audience and (
                not isinstance(audiences, list)
                or len(audiences) not in (1, 2)
                or any(not isinstance(value, str) for value in audiences)
                or len(set(audiences)) != len(audiences)
                or self.settings.audience not in audiences
                or not set(audiences) <= {self.settings.audience, self.settings.issuer + "userinfo"}
            ):
                raise ValueError()
            subject = claims["sub"]
            if not isinstance(subject, str):
                raise ValueError()
        except OAuthRejection:
            raise
        except Exception:
            raise OAuthRejection(
                401, "invalid_token", self.settings.challenge("invalid_token")
            ) from None
        if not hmac.compare_digest(subject.encode(), self.settings.allowed_subject.encode()):
            raise OAuthRejection(403, "authorization_denied")
        scopes = claims.get("scope", "")
        if not isinstance(scopes, str) or not set(self.settings.required_scopes) <= set(
            scopes.split()
        ):
            raise OAuthRejection(
                403, "insufficient_scope", self.settings.challenge("insufficient_scope")
            )
        return McpPrincipal(PrincipalId(subject), ConnectionId("personal_canvas"))
