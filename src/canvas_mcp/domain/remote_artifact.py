"""In-memory original validated bytes, never a server locator."""

from dataclasses import dataclass, field

from canvas_mcp.domain.models import FileMetadata


@dataclass(frozen=True, repr=False)
class RemoteArtifact:
    metadata: FileMetadata
    filename: str
    content_type: str
    sha256: str
    data: bytes = field(repr=False)
