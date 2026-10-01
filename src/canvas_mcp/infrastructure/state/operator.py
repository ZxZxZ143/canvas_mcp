"""Operator-only migration/reset commands. No MCP reset tool or DSN output."""

import argparse
import asyncio
import os
import re
import sys
import json

from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import SQLiteStateRepository, PostgreSQLStateRepository


async def run(command: str, owner: str | None, remote: bool) -> dict[str, object]:
    settings = StateSettings.load(os.environ, remote=remote)
    repo = (
        SQLiteStateRepository(settings)
        if settings.backend == "sqlite"
        else PostgreSQLStateRepository(settings)
    )
    if command == "migrate":
        await repo.migrate()
        return {"event": "state_migration_complete", "schema_version": 1}
    elif command == "inspect":
        return {"event": "state_schema_inspected", **await repo.inspect()}
    elif owner is not None and re.fullmatch(r"[a-f0-9]{64}", owner):
        await repo.reset(owner)
        return {"event": "state_reset_complete"}
    else:
        raise StateStoreUnavailableError()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Canvas application state only.")
    parser.add_argument("command", choices=("migrate", "inspect", "reset"))
    parser.add_argument("--owner")
    parser.add_argument("--remote", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.command, args.owner, args.remote))
    except Exception:
        print("state_store_unavailable", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
