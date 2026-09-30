"""One opt-in installed-stdio workload read; safe metrics, optional coursework facts."""

import argparse
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(show_facts: bool) -> int:
    env = dict(os.environ)
    env.pop("CANVAS_ACCESS_TOKEN", None)
    env.update(
        {
            "CANVAS_BASE_URL": "https://canvas.narxoz.kz",
            "CANVAS_TRUSTED_PRIVATE_IPS": "192.168.4.200",
            "CANVAS_CREDENTIAL_PROVIDER": "windows",
            "DOWNLOAD_DIRECTORY": "C:\\canvas_mcp_runtime\\plugin_downloads",
        }
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-I", "-m", "canvas_mcp.mcp_server"], env=env
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {x.name for x in (await session.list_tools()).tools}
            print(f"tool_count={len(names)} workload_available={'canvas_get_workload' in names}")
            started = time.perf_counter()
            output = await session.call_tool(
                "canvas_get_workload", {"days": 7, "timezone": "Asia/Qyzylorda"}
            )
            elapsed = time.perf_counter() - started
            if output.isError:
                print(f"workload=failed elapsed_seconds={elapsed:.3f}")
                return 1
            payload = output.structuredContent or json.loads(output.content[0].text)
            print(
                json.dumps(
                    {
                        "workload": "ok",
                        "elapsed_seconds": round(elapsed, 3),
                        "complete": payload["complete"],
                        "item_count": len(payload["data"]["items"]),
                        "coverage": payload["data"]["coverage"],
                        "warnings": payload["warnings"],
                    }
                )
            )
            if show_facts:
                # DTO has no credential, URL capability, personal profile or local path.
                print(json.dumps(payload, ensure_ascii=True))
            return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--show-facts", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.show_facts)))
    except Exception:
        print("planner_live_smoke=failed", file=sys.stderr)
        raise SystemExit(1) from None
