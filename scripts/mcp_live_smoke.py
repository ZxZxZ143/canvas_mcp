"""Opt-in local MCP protocol smoke with live Canvas; prints no account data."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> int:
    python = Path(sys.executable)
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=str(python),
        args=["-I", "-m", "canvas_mcp.mcp_server"],
        cwd=str(root),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            print("initialize=true")
            tools = await session.list_tools()
            names = {item.name for item in tools.tools}
            listed = "canvas_get_profile" in names and "canvas_list_courses" in names
            print(f"tools_list={'true' if listed else 'false'} count={len(names)}")
            if not listed:
                return 1
            profile = await session.call_tool("canvas_get_profile", {})
            print(f"profile={'false' if profile.isError else 'true'}")
            if profile.isError:
                return 1
            courses = await session.call_tool("canvas_list_courses", {"limit": 25})
            print(f"courses={'false' if courses.isError else 'true'}")
            return 1 if courses.isError else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except Exception:
        print("mcp_smoke_failed=true", file=sys.stderr)
        raise SystemExit(1) from None
