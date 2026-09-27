"""Linux-only, per-call, pinned private storage; no published artifact or caller path."""

import hashlib
import importlib
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

from canvas_mcp.domain.errors import (
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    FileContentTooLargeError,
    StorageError,
)
from canvas_mcp.domain.file_content import REMOTE_INSPECTION_MAX_BYTES
from canvas_mcp.domain.mime import DiagnosticStatus, ExpectedFormat
from canvas_mcp.infrastructure.files.format_probe import (
    MAX_PROBE_BYTES,
    identify_archive_reader,
    identify,
)
from canvas_mcp.infrastructure.files.storage import Digest


@dataclass(repr=False)
class EphemeralFile:
    handle: int
    identity: tuple[int, int]
    count: int = 0
    digest: Digest = field(default_factory=hashlib.sha256)


class EphemeralStore:
    """Exactly one generated file; cleanup never walks or recursively deletes paths."""

    def __init__(
        self, maximum: int = REMOTE_INSPECTION_MAX_BYTES, *, _root: Path | None = None
    ) -> None:
        if os.name != "posix" or not 1 <= maximum <= REMOTE_INSPECTION_MAX_BYTES:
            raise ConfigurationError()
        self.maximum = maximum
        self._root_path = _root or Path("/tmp")
        self._root = self._directory = -1
        self._name = self._file_name = ""
        self._identity: tuple[int, int] | None = None
        self._pending: EphemeralFile | None = None
        self._reader = -1
        self._directory_created = self._file_created = False

    @staticmethod
    def _check(condition: bool) -> None:
        if not condition:
            raise StorageError()

    @staticmethod
    def _id(info: os.stat_result) -> tuple[int, int]:
        return info.st_dev, info.st_ino

    def begin(self) -> EphemeralFile:
        self._check(self._root == -1 and self._pending is None)
        root = self._root_path
        self._check(root.is_absolute() and root.resolve(strict=True) == root)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY")
            | getattr(os, "O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC")
        )
        self._root = os.open(root, flags)
        info = os.fstat(self._root)
        self._check(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid in (0, getattr(os, "getuid")())
            and (info.st_mode & 0o022 == 0 or bool(info.st_mode & stat.S_ISVTX))
        )
        self._name = "canvas-content-" + secrets.token_hex(16)
        os.mkdir(self._name, mode=0o700, dir_fd=self._root)
        self._directory_created = True
        self._identity = self._id(os.stat(self._name, dir_fd=self._root, follow_symlinks=False))
        self._directory = os.open(self._name, flags, dir_fd=self._root)
        info = os.fstat(self._directory)
        self._check(
            self._id(info) == self._identity
            and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == getattr(os, "getuid")()
        )
        self._file_name = secrets.token_hex(16) + ".blob"
        handle = os.open(
            self._file_name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC"),
            0o600,
            dir_fd=self._directory,
        )
        self._file_created = True
        info = os.fstat(handle)
        self._pending = EphemeralFile(handle, self._id(info))
        self._verify(self._pending)
        return self._pending

    def _verify(self, pending: EphemeralFile) -> None:
        self._check(self._pending is pending and pending.handle >= 0)
        info = os.fstat(pending.handle)
        path_info = os.stat(self._file_name, dir_fd=self._directory, follow_symlinks=False)
        self._check(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == getattr(os, "getuid")()
            and self._id(info) == pending.identity == self._id(path_info)
            and info.st_size == pending.count
            and stat.S_ISREG(path_info.st_mode)
        )

    def write(self, pending: EphemeralFile, chunk: bytes) -> None:
        self._verify(pending)
        if pending.count + len(chunk) > self.maximum:
            raise FileContentTooLargeError()
        view = memoryview(chunk)
        while view:
            count = os.write(pending.handle, view)
            self._check(count > 0)
            view = view[count:]
        pending.digest.update(chunk)
        pending.count += len(chunk)

    def validate_archive(self, pending: EphemeralFile, extension: str) -> None:
        self._verify(pending)
        if extension not in ("docx", "pptx"):
            return
        inspected = 0

        def read_at(offset: int, size: int) -> bytes:
            nonlocal inspected
            if offset < 0 or size < 0 or offset + size > pending.count:
                raise ValueError()
            inspected += size
            if inspected > MAX_PROBE_BYTES:
                raise ValueError()
            return getattr(os, "pread")(pending.handle, size, offset)

        evidence = identify_archive_reader(pending.count, read_at, ExpectedFormat(extension))
        if evidence.result is not DiagnosticStatus.EXPECTED:
            raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)

    def validate(self, pending: EphemeralFile, extension: str) -> None:
        self._verify(pending)
        self.validate_archive(pending, extension)
        if extension in ("png", "jpg", "jpeg", "webp"):
            prefix = getattr(os, "pread")(pending.handle, 12, 0)
            valid = (
                prefix.startswith(b"\x89PNG\r\n\x1a\n")
                if extension == "png"
                else prefix.startswith(b"\xff\xd8\xff")
                if extension in ("jpg", "jpeg")
                else prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
            )
            if not valid:
                raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)
        if extension in ("txt", "md", "csv", "json"):
            # Streaming BytePolicy already verified full UTF-8/no NUL. Here
            # reject recognized nontext signatures before launching a parser.
            prefix = getattr(os, "pread")(pending.handle, 512, 0)
            evidence = identify(prefix, ExpectedFormat.TXT)
            if evidence.result in (
                DiagnosticStatus.OTHER,
                DiagnosticStatus.UNSAFE,
                DiagnosticStatus.INVALID,
            ):
                raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)

    def reader(self, pending: EphemeralFile) -> int:
        fcntl = importlib.import_module("fcntl")

        self._verify(pending)
        self._check(self._reader == -1)
        getattr(os, "fchmod")(pending.handle, 0o400)
        self._reader = os.open(
            self._file_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW") | getattr(os, "O_CLOEXEC"),
            dir_fd=self._directory,
        )
        info = os.fstat(self._reader)
        self._check(
            self._id(info) == pending.identity
            and info.st_nlink == 1
            and info.st_size == pending.count
            and getattr(os, "O_ACCMODE") & fcntl.fcntl(self._reader, fcntl.F_GETFL) == os.O_RDONLY
        )
        os.close(pending.handle)
        pending.handle = -1
        return self._reader

    def close(self) -> None:
        try:
            if self._reader >= 0:
                os.close(self._reader)
                self._reader = -1
            if self._pending is not None and self._pending.handle >= 0:
                os.close(self._pending.handle)
                self._pending.handle = -1
            if self._directory >= 0:
                self._check(self._id(os.fstat(self._directory)) == self._identity)
                if self._file_created:
                    try:
                        os.unlink(self._file_name, dir_fd=self._directory)
                    except FileNotFoundError:
                        pass
                    self._file_created = False
            if self._directory_created and self._root >= 0:
                info = os.stat(self._name, dir_fd=self._root, follow_symlinks=False)
                self._check(stat.S_ISDIR(info.st_mode) and self._id(info) == self._identity)
                os.rmdir(self._name, dir_fd=self._root)
                self._directory_created = False
        finally:
            if self._directory >= 0:
                os.close(self._directory)
                self._directory = -1
            if self._root >= 0:
                os.close(self._root)
                self._root = -1
