"""Controlled download boundary; no arbitrary URL/path/filename parameters.

Concrete infrastructure coordinates an LMS metadata/source resolver and safe
storage. Source URL and authentication remain private infrastructure details.
Artifact access is independently authorized; possession of an ID is insufficient.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from canvas_mcp.domain.models import (
    Artifact,
    ArtifactId,
    EntityId,
    RequestContext,
    DownloadedFile,
    FileReference,
)


class FileDownloads(Protocol):
    async def download_file(
        self, ctx: RequestContext, reference: FileReference
    ) -> DownloadedFile: ...

    async def resolve_download(
        self, ctx: RequestContext, artifact_id: ArtifactId
    ) -> DownloadedFile: ...

    async def cleanup_download(self, ctx: RequestContext, artifact_id: ArtifactId) -> None: ...


class CourseArtifacts(Protocol):
    """Unimplemented architecture-phase sketch; Phase 3 uses FileDownloads."""

    async def download_course_file(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        file_id: EntityId,
    ) -> Artifact: ...


@dataclass(frozen=True)
class LocalArtifactAccess:
    """Local bridge DTO only; never returned by provider/application queries.

    Path must be generated and revalidated by storage, not copied from metadata.
    A remote transport must not expose this record or accept local file paths.
    """

    artifact_id: ArtifactId
    local_path: str
    sha256: str
    expires_at: datetime


class LocalArtifactResolver(Protocol):
    """Future bridge sketch, not an implemented MCP registration or interface."""

    async def resolve_for_inspection(
        self,
        ctx: RequestContext,
        artifact_id: ArtifactId,
    ) -> LocalArtifactAccess: ...
