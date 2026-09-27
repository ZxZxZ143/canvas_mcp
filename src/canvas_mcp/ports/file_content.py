"""Remote read boundary: validated identity in, bounded untrusted content out."""

from typing import Protocol

from canvas_mcp.domain.file_content import ContentSelection, FileContent
from canvas_mcp.domain.models import FileReference, RequestContext


class FileContentReader(Protocol):
    async def get_file_content(
        self, ctx: RequestContext, reference: FileReference, selection: ContentSelection
    ) -> FileContent: ...
