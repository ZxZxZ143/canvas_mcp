"""Scoped immutable managed storage. Windows implementation fails closed elsewhere."""

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

from canvas_mcp.domain.errors import (
    ArtifactUnavailableError,
    ConfigurationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    FileTooLargeError,
    StorageError,
)
from canvas_mcp.domain.models import AccessScope, ArtifactId, DownloadedFile, FileMetadata
from canvas_mcp.domain.mime import DiagnosticStatus, ExpectedFormat
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES, identify_archive_reader
from canvas_mcp.infrastructure.files.windows import WindowsFS, check


class Digest(Protocol):
    def update(self, data: bytes) -> None: ...
    def hexdigest(self) -> str: ...


def bounded_entries(path: Path, limit: int) -> list[Path]:
    entries: list[Path] = []
    with os.scandir(path) as iterator:
        for item in iterator:
            check(len(entries) < limit)
            entries.append(path / item.name)
    return entries


@dataclass(repr=False)
class PendingFile:
    identity: ArtifactId
    path: Path
    handle: int
    file_identity: tuple[int, int, int]
    count: int = 0
    digest: Digest = field(default_factory=hashlib.sha256)


@dataclass(repr=False)
class StoredFile:
    descriptor: DownloadedFile
    handle: int
    identity: tuple[int, int, int]
    deadline: float


class ManagedStore:
    def __init__(self, settings: DeploymentSettings, scope: AccessScope) -> None:
        self._settings, self._scope = settings, scope
        self._fs: WindowsFS | None = None
        self._pins: list[int] = []
        self._lease: int | None = None
        self._session: Path | None = None
        self._root: Path | None = None
        self._pending: dict[ArtifactId, PendingFile] = {}
        self._records: dict[ArtifactId, StoredFile] = {}
        self._closed = False

    def _authorized(self, scope: AccessScope) -> None:
        if scope != self._scope or self._closed:
            raise ArtifactUnavailableError()

    def _start(self) -> None:
        if self._fs is not None:
            return
        root = self._settings.download_directory
        if root is None:
            raise ConfigurationError()
        # Reject Windows namespace/UNC/ADS ambiguity even in trusted config.
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
            ):
                raise ConfigurationError()
        root = root.absolute()
        # No project overwrite: operator must choose a dedicated external root.
        projects = [Path.cwd()]
        projects.extend(p for p in Path(__file__).parents if (p / "pyproject.toml").is_file())
        if any(root == project or root.is_relative_to(project) for project in projects):
            raise ConfigurationError()
        fs = WindowsFS()
        self._fs, self._root = fs, root
        try:
            current = Path(root.anchor)
            self._pins.append(fs.open_directory(current))
            for index, part in enumerate(root.parts[1:]):
                current /= part
                # Parents must already exist. Only the managed leaf is created.
                if index == len(root.parts) - 2 and not current.exists():
                    fs.mkdir(current)
                handle = fs.open_directory(current)
                self._pins.append(handle)
                check(fs.final_path(handle) == current)
            fs.private(self._pins[-1])
            # Delete-on-close exclusive lease: no stale lock after process death.
            self._lease = fs.open_file(root / ".lease", create=True, write=True, lease=True)
            self._recover()
            nonce = secrets.token_hex(16)
            self._session = root / ("s-" + nonce)
            fs.mkdir(self._session)
            self._pins.append(fs.open_directory(self._session))
            fs.private(self._pins[-1])
            marker = {
                "version": 1,
                "session": nonce,
                "owner": fs.owner,
                "created": datetime.now(timezone.utc).isoformat(),
                "expires": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=self._settings.artifact_ttl_seconds)
                ).isoformat(),
            }
            handle = fs.open_file(
                self._session / ".session.json", create=True, write=True, delete=True
            )
            try:
                fs.write(handle, json.dumps(marker, separators=(",", ":")).encode("ascii"))
                fs.flush(handle)
            finally:
                fs.close_handle(handle)
        except BaseException:
            self._release()
            self._closed = True
            raise

    def _recover(self) -> None:
        """One bounded prior session, proven nonlive by exclusive root lease.

        Validate the entire candidate before deleting by handle. Unknown objects
        block new storage, never trigger recursive cleanup or capability recovery.
        """
        assert self._fs is not None and self._root is not None
        fs = self._fs
        entries = bounded_entries(self._root, 2)
        sessions = [p for p in entries if p.name != ".lease"]
        for session in sessions:
            check(re.fullmatch(r"s-[a-f0-9]{32}", session.name) is not None)
            pin = fs.open_directory(session)
            handles: list[int] = []
            try:
                fs.private(pin)
                paths = bounded_entries(session, self._settings.max_artifact_count + 1)
                check(1 <= len(paths) <= self._settings.max_artifact_count + 1)
                check(any(p.name == ".session.json" for p in paths))
                total = 0
                for path in paths:
                    check(
                        path.name == ".session.json"
                        or re.fullmatch(r"f-[a-f0-9]{32}\.(?:part|blob)", path.name) is not None
                    )
                    handle = fs.open_file(path, delete=True)
                    handles.append(handle)
                    info = fs.info(handle)
                    size = (info.size_high << 32) | info.size_low
                    if path.name == ".session.json":
                        check(size <= 2048)
                        raw = fs.read(handle, 2048)
                        valid = False
                        try:
                            marker = json.loads(raw)
                            valid = (
                                type(marker) is dict
                                and set(marker)
                                == {"version", "session", "owner", "created", "expires"}
                                and marker["version"] == 1
                                and marker["session"] == session.name[2:]
                                and marker["owner"] == fs.owner
                                and datetime.fromisoformat(marker["created"]).tzinfo is not None
                                and datetime.fromisoformat(marker["expires"]).tzinfo is not None
                            )
                        except (ValueError, TypeError, KeyError):
                            pass
                        check(valid)
                    else:
                        total += size
                        check(total <= self._settings.max_artifact_bytes_total)
                for handle in handles:
                    fs.delete(handle)
            finally:
                for handle in handles:
                    fs.close_handle(handle)
                fs.close_handle(pin)
            fs.rmdir(session)

    def begin(self, scope: AccessScope) -> PendingFile:
        self._authorized(scope)
        self._start()
        assert self._fs is not None and self._session is not None
        reserved = (
            sum(r.descriptor.size for r in self._records.values())
            + len(self._pending) * self._settings.max_download_bytes
        )
        if (
            len(self._records) + len(self._pending) >= self._settings.max_artifact_count
            or reserved + self._settings.max_download_bytes
            > self._settings.max_artifact_bytes_total
        ):
            raise StorageError()
        identity = ArtifactId(secrets.token_hex(16))
        if identity in self._pending or identity in self._records:
            raise StorageError()
        path = self._session / ("f-" + identity + ".part")
        handle = self._fs.open_file(path, create=True, write=True, delete=True)
        pending = PendingFile(identity, path, handle, self._fs.identity(handle))
        self._pending[identity] = pending
        return pending

    def write(self, pending: PendingFile, chunk: bytes) -> None:
        assert self._fs is not None
        check(self._pending.get(pending.identity) is pending)
        if pending.count + len(chunk) > self._settings.max_download_bytes:
            raise FileTooLargeError()
        self._fs.write(pending.handle, chunk)
        pending.digest.update(chunk)
        pending.count += len(chunk)

    def inspect_pending(self, pending: PendingFile, maximum: int) -> bytes:
        """Read a bounded, still-exclusive quarantine handle; never publish it."""
        assert self._fs is not None
        fs = self._fs
        check(not self._closed and self._pending.get(pending.identity) is pending)
        check(0 < maximum <= 1_048_576 and pending.count <= maximum)
        fs.flush(pending.handle)
        fs.private(pending.handle)
        check(fs.identity(pending.handle) == pending.file_identity)
        check(fs.final_path(pending.handle) == pending.path)
        info = fs.info(pending.handle)
        check(((info.size_high << 32) | info.size_low) == pending.count)
        fs.rewind(pending.handle)
        chunks: list[bytes] = []
        count = 0
        while data := fs.read(pending.handle, min(65536, maximum + 1 - count)):
            count += len(data)
            check(count <= maximum)
            chunks.append(data)
        content = b"".join(chunks)
        check(
            count == pending.count
            and hashlib.sha256(content).hexdigest() == pending.digest.hexdigest()
        )
        return content

    def validate_pending_archive(self, pending: PendingFile, metadata: FileMetadata) -> None:
        """Validate ZIP/OOXML directory structure before publishing an artifact."""
        name = metadata.filename.value
        extension = name.text.rsplit(".", 1)[-1].lower() if name is not None else ""
        if extension not in ("zip", "docx", "pptx", "xlsx"):
            return
        assert self._fs is not None
        fs = self._fs
        check(not self._closed and self._pending.get(pending.identity) is pending)
        fs.flush(pending.handle)
        fs.private(pending.handle)
        check(fs.identity(pending.handle) == pending.file_identity)
        check(fs.final_path(pending.handle) == pending.path)
        info = fs.info(pending.handle)
        check(((info.size_high << 32) | info.size_low) == pending.count)
        inspected = 0

        def read_at(offset: int, length: int) -> bytes:
            nonlocal inspected
            if (
                offset < 0
                or length < 0
                or offset + length > pending.count
                or inspected + length > MAX_PROBE_BYTES
            ):
                raise ValueError()
            inspected += length
            fs.seek(pending.handle, offset)
            parts: list[bytes] = []
            remaining = length
            while remaining:
                chunk = fs.read(pending.handle, min(65536, remaining))
                if not chunk:
                    raise ValueError()
                parts.append(chunk)
                remaining -= len(chunk)
            return b"".join(parts)

        evidence = identify_archive_reader(pending.count, read_at, ExpectedFormat(extension))
        if evidence.result is DiagnosticStatus.UNSAFE:
            raise DownloadRejectedError(reason=DownloadRejectionReason.UNSAFE_PREFIX)
        if evidence.result is not DiagnosticStatus.EXPECTED:
            raise DownloadRejectedError(reason=DownloadRejectionReason.PREFIX_MISMATCH)

    def finish(
        self,
        pending: PendingFile,
        metadata: FileMetadata,
        content_type: str,
        redirects: int,
        classification: Literal[
            "untrusted_document", "untrusted_text", "opaque_archive", "office_candidate"
        ],
    ) -> DownloadedFile:
        assert self._fs is not None and self._session is not None
        fs = self._fs
        check(self._pending.get(pending.identity) is pending)
        fs.flush(pending.handle)
        final = self._session / ("f-" + pending.identity + ".blob")
        fs.rename(pending.handle, final)  # source handle, pinned parents, replace=False
        pending.path = final
        check(fs.final_path(pending.handle) == final)
        fs.close_handle(pending.handle)
        pending.handle = -1
        handle = fs.open_file(final)  # deny writes/deletion for descriptor lifetime
        try:
            check(fs.identity(handle) == pending.file_identity)
            info = fs.info(handle)
            check(((info.size_high << 32) | info.size_low) == pending.count)
            descriptor = DownloadedFile(
                pending.identity,
                metadata.source,
                str(final),
                metadata.display_name,
                metadata.filename,
                final.name,
                content_type,
                metadata.content_type,
                pending.count,
                pending.digest.hexdigest(),
                redirects,
                datetime.now(timezone.utc) + timedelta(seconds=self._settings.artifact_ttl_seconds),
                classification,
            )
            self._records[pending.identity] = StoredFile(
                descriptor,
                handle,
                pending.file_identity,
                time.monotonic() + self._settings.artifact_ttl_seconds,
            )
            del self._pending[pending.identity]
            return descriptor
        except BaseException:
            fs.close_handle(handle)
            raise

    def abort(self, pending: PendingFile) -> None:
        assert self._fs is not None
        if self._pending.get(pending.identity) is not pending:
            return
        handle = pending.handle
        if handle == -1:
            handle = self._fs.open_file(pending.path, delete=True)
        try:
            check(self._fs.identity(handle) == pending.file_identity)
            self._fs.delete(handle)
        finally:
            self._fs.close_handle(handle)
            pending.handle = -1
        del self._pending[pending.identity]

    def get(
        self, scope: AccessScope, identity: ArtifactId, *, verify: bool = True
    ) -> DownloadedFile:
        self._authorized(scope)
        record = self._records.get(identity)
        if record is None:
            raise ArtifactUnavailableError()
        if time.monotonic() >= record.deadline:
            self.cleanup(scope, identity)
            raise ArtifactUnavailableError()
        assert self._fs is not None
        fs = self._fs
        check(fs.identity(record.handle) == record.identity)
        check(fs.final_path(record.handle) == Path(record.descriptor.local_path))
        if verify:
            fs.rewind(record.handle)
            digest, count = hashlib.sha256(), 0
            while data := fs.read(record.handle):
                count += len(data)
                check(count <= record.descriptor.size)
                digest.update(data)
            check(
                count == record.descriptor.size and digest.hexdigest() == record.descriptor.sha256
            )
        return record.descriptor

    def cleanup(self, scope: AccessScope, identity: ArtifactId) -> None:
        self._authorized(scope)
        record = self._records.get(identity)
        if record is None:
            raise ArtifactUnavailableError()
        assert self._fs is not None
        fs = self._fs
        if record.handle != -1:
            fs.close_handle(record.handle)
            record.handle = -1
        handle = fs.open_file(Path(record.descriptor.local_path), delete=True)
        try:
            check(fs.identity(handle) == record.identity)
            fs.delete(handle)
        finally:
            fs.close_handle(handle)
        del self._records[identity]

    def _release(self) -> None:
        if self._fs is not None:
            for pending in self._pending.values():
                if pending.handle != -1:
                    self._fs.close_handle(pending.handle)
                    pending.handle = -1
            for record in self._records.values():
                if record.handle != -1:
                    self._fs.close_handle(record.handle)
                    record.handle = -1
            for handle in reversed(self._pins):
                self._fs.close_handle(handle)
            self._pins.clear()
            if self._lease is not None:
                self._fs.close_handle(self._lease)
                self._lease = None
            self._fs.close()
            self._fs = None

    def close(self) -> None:
        if self._closed:
            return
        failed = False
        try:
            for pending in tuple(self._pending.values()):
                self.abort(pending)
            for identity in tuple(self._records):
                self.cleanup(self._scope, identity)
            if self._fs is not None and self._session is not None:
                marker = self._fs.open_file(self._session / ".session.json", delete=True)
                try:
                    self._fs.delete(marker)
                finally:
                    self._fs.close_handle(marker)
                self._fs.close_handle(self._pins.pop())
                self._fs.rmdir(self._session)
        except Exception:
            failed = True
        finally:
            self._release()
            self._closed = True
        if failed:
            raise StorageError()
