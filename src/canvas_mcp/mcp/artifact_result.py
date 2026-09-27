"""Separate allowlisted remote artifact projection with exact hidden-payload cap."""

import base64
import hashlib

from mcp.types import CallToolResult, TextContent

from canvas_mcp.application.contracts import Result
from canvas_mcp.domain.errors import BudgetExceededError, FileParseError
from canvas_mcp.domain.file_content import REMOTE_ARTIFACT_MAX_BYTES
from canvas_mcp.domain.remote_artifact import RemoteArtifact
from canvas_mcp.mcp.projection import envelope, file_reference

MAX_ARTIFACT_WIRE_BYTES = 5_700_000


def artifact_result(
    value: Result[RemoteArtifact], *, include_original: bool = True
) -> CallToolResult:
    artifact = value.data
    size = len(artifact.data)
    if (
        not 0 < size <= REMOTE_ARTIFACT_MAX_BYTES
        or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256
    ):
        raise FileParseError()
    info = {
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size": size,
        "sha256": artifact.sha256,
        "file_reference": file_reference(artifact.metadata.source),
        "trust": "untrusted",
        "artifact_status": "validated_original_ready_for_chatgpt_attachment",
        "instruction": "The file card is ready. Tell the user to click Attach original for download, then Download original. A ready status is normal: the card confirms ChatGPT acceptance after the user clicks. Do not repeat this tool to check attachment or claim automatic upload success.",
    }
    output = envelope(value, lambda _: info)
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text="Original-file card ready. Click Attach original for download, then Download original. A ready status is normal; the card confirms acceptance after the click. Do not repeat the tool to check attachment.",
            )
        ],
        structuredContent=dict(output),
        _meta={
            "canvasArtifact": {
                **info,
                **(
                    {"base64": base64.b64encode(artifact.data).decode("ascii")}
                    if include_original
                    else {}
                ),
            }
        },
    )
    if len(result.model_dump_json().encode("utf-8")) + 1024 > MAX_ARTIFACT_WIRE_BYTES:
        raise BudgetExceededError()
    return result
