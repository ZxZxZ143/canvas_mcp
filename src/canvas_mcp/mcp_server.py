"""Personal local stdio adapter. Stdout belongs exclusively to MCP."""

import asyncio
import sys

from canvas_mcp import composition
from canvas_mcp.domain.errors import ApplicationError
from canvas_mcp.infrastructure.config.environment import load_settings

# Preserve the existing import contract for plugin clients and tests.
from canvas_mcp.mcp.tools import create_server

__all__ = ["create_server", "main"]


async def _serve() -> None:
    settings = load_settings()
    async with composition.open_canvas_connection(log_stream=sys.stderr) as connection:
        server = create_server(connection, settings.download_directory)
        await server.run_stdio_async()


def main() -> None:
    if len(sys.argv) > 1:
        if sys.argv[1:] in (["--help"], ["-h"]):
            print(
                "usage: python -m canvas_mcp.mcp_server\n\nRun the local Canvas stdio MCP server."
            )
            return
        print("Canvas MCP startup failed: invalid_arguments", file=sys.stderr)
        raise SystemExit(2)
    try:
        asyncio.run(_serve())
    except Exception as error:
        # No exception text: it may contain credentials, paths, or signed URLs.
        code = error.code if isinstance(error, ApplicationError) else "internal_error"
        print(f"Canvas MCP startup failed: {code}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
