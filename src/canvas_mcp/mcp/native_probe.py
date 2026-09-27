"""Temporary bounded synthetic MCP resource-link experiment; never Canvas I/O."""

import hashlib

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ResourceLink, TextContent, ToolAnnotations

URI = "canvas://experiments/native-original.pdf"


def synthetic_pdf() -> bytes:
    stream = b"BT /F1 18 Tf 40 150 Td (Synthetic inline file experiment) Tj ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    )
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    )
    return bytes(data)


def register_native_probe(server: FastMCP) -> None:
    body = synthetic_pdf()

    @server.resource(URI, mime_type="application/pdf")
    def synthetic_original() -> bytes:
        return body

    @server.tool(
        name="canvas_test_native_file_reference",
        description="Temporary synthetic native-file experiment. Returns only a standard MCP resource link to a fixed tiny synthetic PDF. No Canvas credentials/content or I/O. Inspect whether the host itself produces an attachment; never invent a fileId, temporary URL or download card.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        structured_output=False,
    )
    async def native_file_reference() -> CallToolResult:
        return CallToolResult(
            content=[
                ResourceLink(
                    type="resource_link",
                    uri=URI,
                    name="Synthetic-original.pdf",
                    mimeType="application/pdf",
                    size=len(body),
                ),
                TextContent(
                    type="text",
                    text=f"Synthetic PDF: {len(body)} bytes, one page, SHA-256 {hashlib.sha256(body).hexdigest()}. This MCP resource link is not proof of a ChatGPT-owned attachment. Report actual host rendering only.",
                ),
            ]
        )
