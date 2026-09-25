"""File use cases: validated identities in, normalized local descriptors out."""

import re

from canvas_mcp.application.academic_results import result
from canvas_mcp.application.contracts import Result
from canvas_mcp.domain.errors import ApplicationError, ValidationError
from canvas_mcp.domain.file_validation import file_filter, file_reference
from canvas_mcp.domain.models import (
    ArtifactId,
    DownloadedFile,
    EntityId,
    FileFilter,
    FileMetadata,
    FileReference,
    Page,
    PageRequest,
    RequestContext,
)
from canvas_mcp.domain.validation import canvas_id, page_request
from canvas_mcp.ports.artifacts import FileDownloads
from canvas_mcp.ports.lms import LmsFileQueries


class FileService:
    def __init__(self, provider: LmsFileQueries, downloads: FileDownloads) -> None:
        self._provider, self._downloads = provider, downloads

    async def list_course_files(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest = PageRequest(),
        query: FileFilter = FileFilter(),
    ) -> Result[Page[FileMetadata]]:
        canvas_id(course_id)
        page_request(page)
        file_filter(query)
        return result(
            await self._provider.list_course_files(ctx, course_id, page, query), ctx, "files"
        )

    async def get_file_metadata(
        self, ctx: RequestContext, reference: FileReference
    ) -> Result[FileMetadata]:
        file_reference(reference)
        return result(await self._provider.get_file_metadata(ctx, reference), ctx, "file_metadata")

    async def download_file(
        self, ctx: RequestContext, reference: FileReference
    ) -> Result[DownloadedFile]:
        file_reference(reference)
        downloaded = await self._downloads.download_file(ctx, reference)
        try:
            return result(downloaded, ctx, "download")
        except ApplicationError:
            await self._downloads.cleanup_download(ctx, downloaded.artifact_id)
            raise

    @staticmethod
    def _identity(value: ArtifactId) -> None:
        if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
            raise ValidationError()

    async def resolve_download(
        self, ctx: RequestContext, artifact_id: ArtifactId
    ) -> Result[DownloadedFile]:
        self._identity(artifact_id)
        return result(await self._downloads.resolve_download(ctx, artifact_id), ctx, "download")

    async def cleanup_download(self, ctx: RequestContext, artifact_id: ArtifactId) -> None:
        self._identity(artifact_id)
        await self._downloads.cleanup_download(ctx, artifact_id)
