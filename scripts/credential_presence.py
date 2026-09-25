"""Dev-only, offline credential handoff check; stdout is exactly two booleans.

Run with the project's Python, optionally -I. This checks local handoff, NOT
server acceptance. Never serialize settings, credentials, errors or environment.
"""

import asyncio
import os
import socket
from unittest.mock import patch

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.ports.credentials import AccessToken


async def check() -> tuple[bool, bool]:
    env = dict(os.environ)
    empty = not bool(env.get("CANVAS_ACCESS_TOKEN", ""))
    present = False

    def offline(*args: object, **kwargs: object) -> None:
        raise RuntimeError("offline credential check")

    try:
        with (
            patch.object(socket, "getaddrinfo", offline),
            patch.object(socket.socket, "connect", offline),
        ):
            async with open_canvas_connection(env) as connection:
                client = connection.service._provider._client
                token = await client._access_token(
                    connection._context(), connection._settings.request_timeout_seconds
                )
                present = (
                    "CANVAS_ACCESS_TOKEN" in env
                    and not empty
                    and isinstance(token, AccessToken)
                    and token.value == env["CANVAS_ACCESS_TOKEN"]
                )
    except Exception:
        pass  # Only the fixed booleans can cross this dev diagnostic boundary.
    return present, empty


def main() -> int:
    present, empty = asyncio.run(check())
    print(f"credential_present = {str(present).lower()}")
    print(f"credential_empty = {str(empty).lower()}")
    return 0 if present else 1


if __name__ == "__main__":
    raise SystemExit(main())
