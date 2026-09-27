"""Bounded private original handoff. No paths, URLs, credentials or Canvas I/O."""

import asyncio
import hashlib
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from canvas_mcp.domain.errors import FileParseError, FileContentUnavailableError
from canvas_mcp.domain.file_content import REMOTE_ARTIFACT_MAX_BYTES
from canvas_mcp.domain.remote_artifact import RemoteArtifact
from canvas_mcp.mcp.identity import McpPrincipal, current_principal

PREFIX = "canvas-original://file/"
PATTERN = re.compile(
    r"canvas-original://file/[0-9a-f]{32}/canvas-file-[1-9][0-9]*\.(pdf|docx|pptx|txt|md|csv|json|png|jpg|jpeg|webp)\Z"
)
PENDING_SECONDS = 300.0
RETRY_SECONDS = 10.0


@dataclass(repr=False)
class PendingOriginal:
    owner: McpPrincipal
    artifact: RemoteArtifact = field(repr=False)
    expires: float


@dataclass(repr=False)
class HandoffReceipt:
    registry: "OriginalHandoffs"
    uri: str
    operation: str


handoff_receipts: ContextVar[list[HandoffReceipt] | None] = ContextVar(
    "handoff_receipts", default=None
)


class OriginalHandoffs:
    """At most two entries and 4 MiB aggregate; real expiry and request rollback."""

    def __init__(self) -> None:
        self._entries: dict[str, PendingOriginal] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def _expire(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        now = time.monotonic()
        for uri in tuple(self._entries):
            if self._entries[uri].expires <= now:
                del self._entries[uri]
        self._schedule()

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._entries and not self._closed:
            delay = max(0, min(item.expires for item in self._entries.values()) - time.monotonic())
            self._timer = asyncio.get_running_loop().call_later(delay, self._expire)

    def publish(self, artifact: RemoteArtifact) -> str | None:
        owner = current_principal.get()
        receipts = handoff_receipts.get()
        if owner is None or receipts is None or self._closed:
            raise FileContentUnavailableError()
        size = len(artifact.data)
        uri = PREFIX + uuid4().hex + "/" + artifact.filename
        if (
            not PATTERN.fullmatch(uri)
            or not 0 < size <= REMOTE_ARTIFACT_MAX_BYTES
            or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256
        ):
            raise FileParseError()
        self._expire()
        if (
            len(self._entries) >= 2
            or sum(len(item.artifact.data) for item in self._entries.values()) + size
            > REMOTE_ARTIFACT_MAX_BYTES
        ):
            return None
        self._entries[uri] = PendingOriginal(owner, artifact, time.monotonic() + PENDING_SECONDS)
        receipts.append(HandoffReceipt(self, uri, "published"))
        self._schedule()
        return uri

    def peek(self, uri: str) -> RemoteArtifact:
        owner = current_principal.get()
        item = self._entries.get(uri) if PATTERN.fullmatch(uri) else None
        if (
            item is None
            or owner is None
            or owner != item.owner
            or item.expires <= time.monotonic()
            or self._closed
        ):
            raise FileContentUnavailableError()
        return item.artifact

    def read(self, uri: str) -> RemoteArtifact:
        value = self.peek(uri)
        receipts = handoff_receipts.get()
        if receipts is None:
            raise FileContentUnavailableError()
        receipts.append(HandoffReceipt(self, uri, "read"))
        return value

    def finish(self, uri: str, operation: str, success: bool) -> None:
        item = self._entries.get(uri)
        if item is not None:
            if not success:
                del self._entries[uri]
            elif operation == "read":
                # Host ingestion can retry. Successful wire delivery is not a
                # host acceptance acknowledgment; allow a bounded retry grace.
                item.expires = min(item.expires, time.monotonic() + RETRY_SECONDS)
        self._schedule()

    def close(self) -> None:
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._entries.clear()
