"""Operator-only migration/reset commands. No MCP reset tool or DSN output."""

import argparse
import asyncio
import os
import re
import sys

from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import SQLiteStateRepository, PostgreSQLStateRepository


async def run(command: str, owner: str | None, remote: bool) -> None:
    settings = StateSettings.load(os.environ, remote=remote)
    repo = (
        SQLiteStateRepository(settings)
        if settings.backend == "sqlite"
        else PostgreSQLStateRepository(settings)
    )
    if command == "migrate":
        await repo.migrate()
    elif owner is not None and re.fullmatch(r"[a-f0-9]{64}", owner):
        await repo.reset(owner)
    else:
        raise StateStoreUnavailableError()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Canvas application state only.")
    parser.add_argument("command", choices=("migrate", "reset"))
    parser.add_argument("--owner")
    parser.add_argument("--remote", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.command, args.owner, args.remote))
    except Exception:
        print("state_store_unavailable", file=sys.stderr)
        return 1
    print("state_migration_complete" if args.command == "migrate" else "state_reset_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
