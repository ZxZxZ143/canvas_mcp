"""Private subprocess entry point. No credentials, URLs, paths or raw exceptions."""

import hashlib
import codecs
import json
import os
import stat
import sys

from canvas_mcp.domain.file_content import ContentSelection, REMOTE_INSPECTION_MAX_BYTES
from canvas_mcp.infrastructure.files.content_parser import extract
from canvas_mcp.infrastructure.files.parser_sandbox import lock_down

MAX_WORKER_OUTPUT_BYTES = 131_072


def main() -> None:
    output: dict[str, object] = {"error": "file_parse_error"}
    engine = None
    try:
        if sys.platform != "linux":
            raise ValueError()
        raw = sys.stdin.buffer.read(4_097)
        if len(raw) > 4_096:
            raise ValueError()
        args = json.loads(raw)
        if set(args) != {"fd", "size", "sha256", "format", "start_page", "end_page"}:
            raise ValueError()
        fd, size = args["fd"], args["size"]
        if (
            type(fd) is not int
            or fd < 3
            or type(size) is not int
            or not 0 <= size <= REMOTE_INSPECTION_MAX_BYTES
        ):
            raise ValueError()
        import fcntl

        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != size
            or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
        ):
            raise ValueError()
        selection = ContentSelection(args["start_page"], args["end_page"])
        selection.validate()
        # Bound trusted library/model bootstrap; decode no document before seccomp.
        import resource

        from canvas_mcp.domain.file_content import REMOTE_PARSER_MEMORY_BYTES, SUPPORTED_FORMATS

        if args["format"] not in SUPPORTED_FORMATS:
            raise ValueError()
        resource.setrlimit(resource.RLIMIT_AS, (REMOTE_PARSER_MEMORY_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        if args["format"] in ("pdf", "png", "jpg", "jpeg", "webp"):
            from canvas_mcp.infrastructure.files.ocr import OcrEngine

            engine = OcrEngine()
        if len(os.listdir("/proc/self/task")) != 1:
            raise ValueError()
        for entry in os.listdir("/proc/self/fd"):
            extra = int(entry)
            if extra not in (0, 1, 2, fd):
                try:
                    os.fstat(extra)
                except OSError:
                    continue
                raise ValueError()
        # Fixed parser codecs may otherwise lazily import after deny-open.
        for encoding in (
            "utf-8-sig",
            "utf-16",
            "utf-16-be",
            "utf-16-le",
            "latin-1",
            "ascii",
            "cp1252",
            "gbk",
            "gb2312",
            "gb18030",
            "cp950",
            "cp932",
            "cp437",
        ):
            codecs.lookup(encoding)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            lock_down(fd)
            digest = hashlib.sha256()
            count = 0
            while chunk := stream.read(65_536):
                count += len(chunk)
                if count > size:
                    raise ValueError()
                digest.update(chunk)
            if count != size or digest.hexdigest() != args["sha256"]:
                raise ValueError()
            stream.seek(0)
            output = {"content": extract(stream, args["format"], selection, engine=engine)}
    except Exception:
        output = {"error": "file_parse_error"}
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception:
                output = {"error": "file_parse_error"}
    encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKER_OUTPUT_BYTES:
        encoded = b'{"error":"file_parse_error"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
