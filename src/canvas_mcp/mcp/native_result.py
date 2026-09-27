"""Native MCP file reference from the SAME validated analysis invocation."""

import json
import re
from dataclasses import replace

from mcp.types import CallToolResult, ResourceLink, TextContent
from pydantic import AnyUrl

from canvas_mcp.application.contracts import Result
from canvas_mcp.domain.errors import BudgetExceededError, FileParseError
from canvas_mcp.domain.file_content import FileContent
from canvas_mcp.mcp import projection as dto
from canvas_mcp.mcp.native_handoff import OriginalHandoffs


def native_content_result(value: Result[FileContent], handoffs: OriginalHandoffs) -> CallToolResult:
    content = value.data
    link = None
    if content.original is not None:
        original = content.original
        if (
            original.metadata.source != content.metadata.source
            or original.sha256 != content.sha256
            or len(original.data) != content.size
            or not content.content_available
        ):
            raise FileParseError()
        uri = handoffs.publish(original)
        if uri is None:
            value = replace(
                value,
                data=replace(
                    content, original=None, original_unavailable_reason="original_handoff_capacity"
                ),
            )
        else:
            # Never use the untrusted display name as an OS path or MIME choice.
            name = re.sub(
                r"[^A-Za-z0-9._ ()-]", "_", content.metadata.display_name.text[:128]
            ).strip(" .")
            if not name or not name.lower().endswith("." + content.format):
                name = original.filename
            link = ResourceLink(
                type="resource_link",
                uri=AnyUrl(uri),
                name=name,
                title=content.metadata.display_name.text[:256],
                mimeType=original.content_type,
                size=content.size,
                description=f"Validated untrusted original; SHA-256 {content.sha256}."
                + (
                    f" {content.total_units} PDF pages."
                    if content.format == "pdf" and content.total_units is not None
                    else ""
                ),
            )
    output = dto.file_content_envelope(value, maximum_wire_bytes=dto.MAX_MCP_OUTPUT_BYTES - 8192)
    response = CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(output, ensure_ascii=False)),
            *([link] if link else []),
        ],
        structuredContent=dict(output),
    )
    if len(response.model_dump_json().encode()) + 1024 > dto.MAX_MCP_OUTPUT_BYTES:
        raise BudgetExceededError()
    return response
