"""Opt-in deployment-only read smoke. Fixed safe output, never coursework or IPs."""

import asyncio
import os
import socket
import sys
from urllib.parse import urlsplit

from canvas_mcp.infrastructure.config.oauth import load_oauth_settings
from canvas_mcp.infrastructure.config.remote import load_remote_settings
from canvas_mcp.infrastructure.config.origin import is_public_address
from canvas_mcp.domain.models import PrincipalId, ConnectionId
from canvas_mcp.mcp.identity import McpPrincipal, current_principal
from canvas_mcp.remote_composition import personal_connections


async def run() -> None:
    env = dict(os.environ)
    if load_remote_settings(env).auth_mode != "oauth":
        raise ValueError()
    oauth = load_oauth_settings(env)
    principal = McpPrincipal(PrincipalId(oauth.allowed_subject), ConnectionId("personal_canvas"))
    registry = personal_connections(env, principal.scope)
    try:
        async with asyncio.timeout(10):
            records = await asyncio.get_running_loop().getaddrinfo(
                urlsplit(registry.settings.canvas_origin).hostname, 443, type=socket.SOCK_STREAM
            )
        if not records or any(not is_public_address(str(record[4][0])) for record in records):
            raise ValueError()
        print("canvas_destination_class=public_global")
        context = current_principal.set(principal)
        try:
            async with registry.connection() as connection:
                await connection.get_profile()
                await connection.list_courses()
        finally:
            current_principal.reset(context)
        print("canvas_connection=ok")
        print("courses_accessible=true")
    finally:
        await registry.aclose()


def main() -> None:
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(
            "usage: python -m canvas_mcp.remote_smoke --verify-canvas\nOpt-in read smoke; run inside the deployment with runtime configuration."
        )
        return
    if sys.argv[1:] != ["--verify-canvas"]:
        print("remote_smoke=explicit_opt_in_required", file=sys.stderr)
        raise SystemExit(2)
    try:
        asyncio.run(run())
    except Exception:
        print("remote_smoke=failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
