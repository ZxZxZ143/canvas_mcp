"""Remote inspection use case, independent of storage and document parsers."""

from canvas_mcp.application.academic_results import result
from canvas_mcp.application.contracts import Result, Warning
from canvas_mcp.domain.file_content import ContentSelection, FileContent
from canvas_mcp.domain.file_validation import file_reference
from canvas_mcp.domain.models import FileReference, RequestContext
from canvas_mcp.ports.file_content import FileContentReader


class FileContentService:
    def __init__(self, reader: FileContentReader) -> None:
        self._reader = reader

    async def get_file_content(
        self, ctx: RequestContext, reference: FileReference, selection: ContentSelection
    ) -> Result[FileContent]:
        file_reference(reference)
        selection.validate()
        content = await self._reader.get_file_content(ctx, reference, selection)
        warnings: tuple[Warning, ...] = ()
        if content.truncated:
            warnings += (Warning("file_content", "file_content_truncated"),)
        if not content.content_available:
            warnings += (Warning("file_content", "file_content_unavailable"),)
        return result(content, ctx, "file_content", warnings)
