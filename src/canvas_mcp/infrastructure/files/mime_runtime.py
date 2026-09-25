"""Private, pinned local JSON state. No general filesystem-path capability."""

import json
import re
import secrets
from pathlib import Path

from canvas_mcp.domain.errors import ConfigurationError, StorageError
from canvas_mcp.infrastructure.files.storage import bounded_entries
from canvas_mcp.infrastructure.files.windows import WindowsFS, check

MAX_STATE_BYTES = 65_536
REGISTRY_NAME = "mime-compatibility.json"


class MimeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._fs: WindowsFS | None = None
        self._pins: list[int] = []
        self._lease: int | None = None

    def __enter__(self) -> "MimeRuntime":
        root = self.root
        if (
            not root.is_absolute()
            or not re.fullmatch(r"[A-Za-z]:", root.drive)
            or root == Path(root.anchor)
        ):
            raise ConfigurationError()
        for part in root.parts[1:]:
            if (
                part in (".", "..")
                or part.endswith((".", " "))
                or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                or part.lower() in ("desktop", "onedrive")
            ):
                raise ConfigurationError()
        projects = [
            Path.cwd(),
            *(p for p in Path(__file__).parents if (p / "pyproject.toml").is_file()),
        ]
        if any(root == p or root.is_relative_to(p) for p in projects):
            raise ConfigurationError()
        fs = WindowsFS()
        self._fs = fs
        try:
            current = Path(root.anchor)
            self._pins.append(fs.open_directory(current))
            for index, part in enumerate(root.parts[1:]):
                current /= part
                if index == len(root.parts) - 2 and not current.exists():
                    fs.mkdir(current)
                handle = fs.open_directory(current)
                self._pins.append(handle)
                check(fs.final_path(handle) == current)
            fs.private(self._pins[-1])
            self._lease = fs.open_file(root / ".lease", create=True, write=True, lease=True)
            for path in bounded_entries(root, 36):
                if path.name == ".lease":
                    continue
                check(
                    path.name == REGISTRY_NAME
                    or re.fullmatch(r"mime-report-[a-f0-9]{32}\.json", path.name)
                )
                handle = fs.open_file(path)
                try:
                    info = fs.info(handle)
                    check(((info.size_high << 32) | info.size_low) <= MAX_STATE_BYTES)
                    check(fs.final_path(handle) == path)
                finally:
                    fs.close_handle(handle)
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fs is not None:
            if self._lease is not None:
                self._fs.close_handle(self._lease)
                self._lease = None
            for pin in reversed(self._pins):
                self._fs.close_handle(pin)
            self._pins.clear()
            self._fs.close()
            self._fs = None

    def read_registry(self) -> object | None:
        assert self._fs is not None
        path = self.root / REGISTRY_NAME
        if not path.exists():
            return None
        handle = self._fs.open_file(path)
        try:
            check(self._fs.final_path(handle) == path)
            info = self._fs.info(handle)
            check(((info.size_high << 32) | info.size_low) <= MAX_STATE_BYTES)
            data = self._fs.read(handle, MAX_STATE_BYTES)
            check(not self._fs.read(handle, 1))

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                output: dict[str, object] = {}
                for key, value in pairs:
                    check(key not in output)
                    output[key] = value
                return output

            try:
                return json.loads(data, object_pairs_hook=unique)
            except (ValueError, RecursionError):
                pass
        finally:
            self._fs.close_handle(handle)
        raise StorageError()

    def _write(self, name: str, value: object, *, replace: bool) -> None:
        assert self._fs is not None
        check(name == REGISTRY_NAME or re.fullmatch(r"mime-report-[a-f0-9]{32}\.json", name))
        check(not replace or name == REGISTRY_NAME)
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
            "ascii"
        )
        check(len(data) <= MAX_STATE_BYTES)
        if not replace:
            check(len(bounded_entries(self.root, 36)) < 34)
        path = self.root / name
        if path.exists():
            check(replace)
            old = self._fs.open_file(path)
            try:
                check(self._fs.final_path(old) == path)
            finally:
                self._fs.close_handle(old)
        temporary = self.root / ("pending-" + secrets.token_hex(16) + ".tmp")
        handle = self._fs.open_file(temporary, create=True, write=True, delete=True)
        renamed = False
        try:
            self._fs.write(handle, data)
            self._fs.flush(handle)
            self._fs.rename(handle, path, replace=replace)
            renamed = True
            check(self._fs.final_path(handle) == path)
        finally:
            try:
                if not renamed:
                    self._fs.delete(handle)
            finally:
                self._fs.close_handle(handle)

    def write_registry(self, value: object) -> None:
        self._write(REGISTRY_NAME, value, replace=True)

    def write_report(self, records: list[dict[str, object]]) -> None:
        check(1 <= len(records) <= 3)
        self._write(
            "mime-report-" + secrets.token_hex(16) + ".json",
            {"version": 1, "candidates": records},
            replace=False,
        )
