"""Local, credential-safe setup for the personal Windows Canvas connection."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import ApplicationError, ConfigurationError
from canvas_mcp.infrastructure.config.windows_credentials import (
    read_token,
    remove_token,
    save_token,
)


async def _validate(token: str) -> None:
    env = dict(os.environ)
    env.update(
        CANVAS_BASE_URL="https://canvas.narxoz.kz",
        CANVAS_TRUSTED_PRIVATE_IPS="192.168.4.200",
        CANVAS_CREDENTIAL_PROVIDER="environment",
        CANVAS_ACCESS_TOKEN=token,
    )
    try:
        async with open_canvas_connection(env, log_stream=sys.stderr) as connection:
            await connection.get_profile()
    finally:
        env.pop("CANVAS_ACCESS_TOKEN", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m canvas_mcp.credentials_cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "configure", help="Validate and save a Canvas token in Windows Credential Manager"
    )
    subcommands.add_parser("logout", help="Remove only the Canvas Student credential")
    subcommands.add_parser("status", help="Report only whether the credential is available")
    args = parser.parse_args(argv)

    if args.command == "logout":
        try:
            removed = remove_token()
        except ConfigurationError:
            print("Canvas credential removal failed.", file=sys.stderr)
            return 1
        print("Canvas credential removed." if removed else "Canvas credential was absent.")
        return 0

    if args.command == "status":
        try:
            read_token()
        except ConfigurationError:
            print("Canvas credential unavailable.")
            return 1
        print("Canvas credential available.")
        return 0

    try:
        token = getpass.getpass("Canvas personal access token: ")
        asyncio.run(_validate(token))
        save_token(token)
    except (ApplicationError, ConfigurationError):
        print(
            "Canvas credential validation or storage failed; existing credential was kept.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print("Canvas credential setup failed; existing credential was kept.", file=sys.stderr)
        return 1
    finally:
        token = ""
    print("Canvas credential validated and stored in Windows Credential Manager.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
