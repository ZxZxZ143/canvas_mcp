"""Streaming sink contract shared by durable local and ephemeral remote storage."""

from typing import Protocol, TypeVar


class DownloadTarget(Protocol):
    count: int


P = TypeVar("P", bound=DownloadTarget, contravariant=True)


class DownloadSink(Protocol[P]):
    def write(self, pending: P, chunk: bytes) -> None: ...
