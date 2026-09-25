"""Live smoke for an installed personal plugin, without a parent-process token."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(plugin_root: Path) -> int:
    entry = json.loads((plugin_root / "mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "canvas_student"
    ]
    env = dict(os.environ)
    env.pop("CANVAS_ACCESS_TOKEN", None)
    params = StdioServerParameters(
        command=entry["command"], args=entry["args"], cwd=str(plugin_root), env=env
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            print("initialize=true")
            tools = await session.list_tools()
            print(f"tools_list=true count={len(tools.tools)}")
            profile = await session.call_tool("canvas_get_profile", {})
            print(f"profile={'false' if profile.isError else 'true'}")
            courses = await session.call_tool("canvas_list_courses", {"limit": 25})
            print(f"courses={'false' if courses.isError else 'true'}")
            return 1 if profile.isError or courses.isError or len(tools.tools) != 15 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.plugin_root.resolve())))
    except Exception:
        print("plugin_smoke_failed=true")
        raise SystemExit(1) from None
