"""Streamable HTTP adapter: local deny/development or personal Auth0 resource server."""

import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import TextIO
from urllib.parse import urlsplit

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.auth.routes import create_protected_resource_routes
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp

from canvas_mcp.domain.models import ConnectionId, PrincipalId
from canvas_mcp.infrastructure.config.remote import RemoteSettings, load_remote_settings
from canvas_mcp.infrastructure.config.oauth import load_oauth_settings
from canvas_mcp.infrastructure.auth0 import Auth0Authenticator
from canvas_mcp.mcp.http import (
    DenyAuthenticator,
    DevelopmentAuthenticator,
    HttpBoundary,
    protect_sdk_logging,
)
from canvas_mcp.mcp.identity import McpPrincipal, McpAuthenticator
from canvas_mcp.mcp.tools import ConnectionFactory, create_server
from canvas_mcp.remote_composition import development_connections, personal_connections


def create_app(
    settings: RemoteSettings | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    connection_factory: ConnectionFactory | None = None,
    log_stream: TextIO | None = None,
    authenticator: McpAuthenticator | None = None,
) -> ASGIApp:
    env = dict(os.environ if environ is None else environ)
    settings = settings or load_remote_settings(env)
    stream = log_stream or sys.stderr
    principal = McpPrincipal(PrincipalId("development"), ConnectionId("development-canvas"))
    connections = None
    oauth = load_oauth_settings(env) if settings.auth_mode == "oauth" else None
    oauth_authenticator = None
    if oauth:
        principal = McpPrincipal(
            PrincipalId(oauth.allowed_subject), ConnectionId("personal_canvas")
        )
        if connection_factory is None:
            connections = personal_connections(env, principal.scope, stream)
            connection_factory = connections.connection
        if authenticator is None:
            oauth_authenticator = Auth0Authenticator(oauth)
            authenticator = oauth_authenticator
    if settings.auth_mode == "development" and connection_factory is None:
        connections = development_connections(env, principal.scope, stream)
        connection_factory = connections.connection
    server = create_server(
        None,
        None,
        transport="http",
        connection_factory=connection_factory,
        oauth_scopes=oauth.required_scopes if oauth else (),
    )
    # Direct peer headers only. Uvicorn proxy_headers=False below preserves scheme,
    # host and client IP until Phase 6.2 defines an explicit trusted proxy list.
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            f"127.0.0.1:{settings.port}",
            f"localhost:{settings.port}",
            f"[::1]:{settings.port}",
        ],
        allowed_origins=[
            f"http://127.0.0.1:{settings.port}",
            f"http://localhost:{settings.port}",
            f"http://[::1]:{settings.port}",
        ],
    )
    server.settings.max_request_body_size = settings.max_body_bytes
    if oauth:
        authority = urlsplit(oauth.public_base_url).netloc
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[authority, authority + ":443"],
            allowed_origins=[oauth.public_base_url],
        )
    sdk_app = server.streamable_http_app()
    protect_sdk_logging()

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            async with sdk_app.router.lifespan_context(sdk_app):
                yield
        finally:
            if connections is not None:
                await connections.aclose()
            if oauth_authenticator is not None:
                await oauth_authenticator.aclose()

    # Reuse SDK Route('/mcp'), including initialization and protocol validation.
    metadata_routes = []
    if oauth:
        metadata_routes = create_protected_resource_routes(
            AnyHttpUrl(oauth.audience),
            [AnyHttpUrl(oauth.issuer)],
            list(oauth.required_scopes),
            resource_name="Canvas Student Remote",
        )
        metadata_routes.append(
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=metadata_routes[0].endpoint,
                methods=["GET", "OPTIONS"],
            )
        )
    app = Starlette(
        routes=[Route("/health", health, methods=["GET"]), *metadata_routes, *sdk_app.routes],
        lifespan=lifespan,
    )
    authenticator = authenticator or (
        DevelopmentAuthenticator(settings.dev_token, principal)
        if settings.auth_mode == "development"
        else DenyAuthenticator()
    )
    names = frozenset(tool.name for tool in server._tool_manager.list_tools())
    return HttpBoundary(
        app,
        settings,
        authenticator,
        names,
        stream,
        (
            settings.dev_token,
            env.get("CANVAS_ACCESS_TOKEN", ""),
            oauth.allowed_subject if oauth else "",
        ),
        oauth,
    )


def main() -> None:
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(
            "usage: python -m canvas_mcp.mcp_http_server\n\nRun HTTP MCP. Default auth denies all MCP requests; production uses Auth0."
        )
        return
    if sys.argv[1:]:
        print('{"event":"mcp_http_startup_failed","code":"invalid_arguments"}', file=sys.stderr)
        raise SystemExit(2)
    try:
        settings = load_remote_settings()
        app = create_app(settings)
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            access_log=False,
            log_config={
                "version": 1,
                "disable_existing_loggers": False,
                "loggers": {
                    "uvicorn": {"handlers": [], "propagate": False},
                    "uvicorn.error": {"handlers": [], "propagate": False},
                    "uvicorn.access": {"handlers": [], "propagate": False},
                },
            },
            limit_concurrency=settings.max_concurrency + 2,
            h11_max_incomplete_event_size=settings.max_header_bytes,
            timeout_keep_alive=5,
        )
    except Exception:
        print('{"event":"mcp_http_startup_failed","code":"configuration_error"}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
