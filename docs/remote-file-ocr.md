# Remote file OCR and original downloads

Phase 6.4 extends the Phase 6.3 public_url pipeline. Both tools accept only a Canvas
FileReference (course/file/source IDs), reauthorize the source, request a fresh
capability and download anonymously with the existing pinned destination and MIME
policies. They never accept a URL, server path, archive member or OCR configuration.

## Extraction

PDF uses pypdf native extraction first. Pages with fewer than 80 alphanumeric
characters are candidates for OCR, in page order, up to three per call. Useful
native text is retained if OCR returns less text. Good native pages are never
rasterized. PDFium renders candidate pages in memory at up to 180 DPI, downscaling
to the pixel budget. Standalone PNG, JPEG/JPG and WEBP use Pillow and one English
Tesseract engine. Animated images are rejected. No image URLs are followed.

DOCX/PPTX retain the bounded native XML readers. Embedded images, charts, media and
equations are not OCR'd. TXT/MD/CSV/JSON retain native bounded readers. Neither tool
executes code, document actions, macros, archive members or embedded links.

Results report extraction_mode (native/ocr/hybrid), ocr_used, ocr_pages,
native_pages, page_count_processed, limitations, truncation and availability.
The file includes a reusable FileReference. OCR is best effort for English
printed text; symbols, formulas, handwriting and photographs can contain errors.
Diagrams are not structurally interpreted and non-text visuals are incomplete.
Empty OCR never implies invisible text was recovered. Page cap exhaustion preserves
remaining native pages and reports partial coverage through limitations/truncated.

## Bounds

| Resource | Limit |
| --- | --- |
| Inspection input | 8 MiB |
| Original downloadable input | 4 MiB |
| Selected PDF pages/PPTX slides | 30 |
| OCR pages per call | 3 |
| Image source | 12 million pixels; 8192 pixels per side |
| Rendered/OCR image | 4 million pixels |
| Parser address space | 256 MiB |
| Parser CPU / wall clock | 8 seconds / 20 seconds, including bootstrap |
| Entire remote file operation | 50 seconds, also bounded by request deadline |
| Extracted Unicode characters | 16,000 |
| Text MCP result | 131,072 bytes, including duplicated SDK payload/framing |
| Artifact MCP result | 5,700,000 bytes, including hidden base64 payload/framing |

One shared gate serializes downloads and parser work. Large embedded PDF images
remain constrained by the address-space and CPU limits even when output pixels are
small. Oversized or malformed files fail safely. Native imports/model initialization
are bounded too. Trusted dependencies: Pillow 12.3.0, pypdfium2 5.13.0,
Debian bookworm libtesseract5 5.3.0-2 and tesseract-ocr-eng 1:4.1.0-2.
The model is packaged with the image; no paid OCR API or runtime model fetch exists.

## Sandbox

The worker gets an already-open read-only artifact FD and a clean environment with
no PAT, Auth0 token, URL or Canvas configuration. Only trusted fixed codec imports,
native libraries and model data load before the unchanged seccomp allowlist seals.
No document is decoded before sealing. Bootstrap rejects extra OS threads and
surviving readable FDs. OpenMP is restricted to one thread. After sealing, arbitrary
open/openat, sockets/connect, exec/fork/clone and artifact writes remain denied.
The worker is killed and reaped before staging is closed on timeout/cancellation.
Private anchored temporary directories are removed in finally on every outcome.

## Original-file card

canvas_download_file uses a separate remote DTO, never the stdio path descriptor.
The model receives compact file metadata, trust, integrity hash and FileReference;
validated original bytes travel only in hidden tool _meta to a static MCP Apps UI
resource. No signed Canvas capability or server path is returned. Credential or
current-capability reflection rejects the original rather than altering it.
Before HTTP transport, bounded base64 is decoded inertly, size/SHA-256 are checked,
and original bytes are checked against server secrets and the presented OAuth
bearer, including common reversible text encodings. Credentials remain outside
document decoders and the OCR worker.
Staging is deleted before the tool returns. Original bytes are not interpreted by
the UI and filenames use textContent and a generated safe name.

The user requests the original after analysis. The file card verifies size and
SHA-256, feature-detects ChatGPT's optional uploadFile/getFileDownloadUrl helpers,
and attaches the original only on a user click. It saves the returned ChatGPT file
ID to avoid duplicate uploads and presents a download link only after acceptance.
There is no arbitrary fetch, external publishing or manual user file upload.
If the host cannot create the file, the card reports failure and the assistant must
not claim a downloadable artifact exists. Platform persistence and exact original
integrity require real ChatGPT Web verification before release completion.

The UI declares empty external connection/resource domain lists. It renders no
document HTML, raster content or code. The ChatGPT file service handles its own
temporary download link; Canvas capabilities remain private to the server.

Remote tools: 16 (content reading plus original download). Local stdio: original
15 tools and managed local download behavior.

References: [OpenAI component/file APIs](https://developers.openai.com/plugins/reference),
[MCP Apps UI integration](https://developers.openai.com/plugins/build/chatgpt-ui),
[PDFium rendering](https://pypdfium2.readthedocs.io/en/stable/python_api.html),
[Debian Tesseract](https://packages.debian.org/bookworm/libtesseract5).
