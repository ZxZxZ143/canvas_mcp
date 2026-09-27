"""One killable worker with bounded pipes; caller owns its read-only artifact FD."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from canvas_mcp.domain.errors import FileContentTimeoutError, FileParseError
from canvas_mcp.domain.file_content import ContentSelection, REMOTE_PARSER_TIMEOUT_SECONDS

MAX_WORKER_OUTPUT_BYTES = 131_072
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from canvas_mcp.infrastructure.files.content_worker import main; main()"
)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def _reap_spawn(spawn: asyncio.Task[asyncio.subprocess.Process]) -> None:
    try:
        process = await spawn
    except (OSError, ValueError):
        return
    await _terminate(process)


async def parse_file(
    fd: int, size: int, sha256: str, fmt: str, selection: ContentSelection
) -> dict[str, Any]:
    process: asyncio.subprocess.Process | None = None
    spawn: asyncio.Task[asyncio.subprocess.Process] | None = None
    try:
        async with asyncio.timeout(REMOTE_PARSER_TIMEOUT_SECONDS):
            spawn = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    _BOOTSTRAP,
                    str(Path(__file__).resolve().parents[3]),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    close_fds=True,
                    pass_fds=(fd,),
                    env={"LANG": "C.UTF-8", "TZ": "UTC"},
                    cwd="/tmp",
                    limit=MAX_WORKER_OUTPUT_BYTES + 1,
                )
            )
            process = await asyncio.shield(spawn)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(
                json.dumps(
                    {
                        "fd": fd,
                        "size": size,
                        "sha256": sha256,
                        "format": fmt,
                        "start_page": selection.start_page,
                        "end_page": selection.end_page,
                    }
                ).encode("ascii")
            )
            await process.stdin.drain()
            process.stdin.close()
            pieces: list[bytes] = []
            count = 0
            while piece := await process.stdout.read(
                min(65_536, MAX_WORKER_OUTPUT_BYTES + 1 - count)
            ):
                count += len(piece)
                if count > MAX_WORKER_OUTPUT_BYTES:
                    raise FileParseError()
                pieces.append(piece)
            await process.wait()
            if process.returncode != 0:
                raise FileParseError()
            response = json.loads(b"".join(pieces))
            if type(response) is not dict or set(response) != {"content"}:
                raise FileParseError()
            return response["content"]
    except TimeoutError:
        raise FileContentTimeoutError() from None
    except (OSError, ValueError, TypeError, KeyError):
        raise FileParseError() from None
    finally:
        # One independent owner tracks spawn through reap. Repeated outer
        # cancellations cannot release the artifact while this child is alive.
        if spawn is not None:
            cleanup = asyncio.create_task(_reap_spawn(spawn))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError()
