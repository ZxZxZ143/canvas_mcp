# Remote file OCR and original downloads

Phase 6.4 status: **COMPLETE**. The user manually verified saved-chat replay and
correct original preview/download, superseding the historical automated replay
failure. See [phase6-4-ocr-and-downloads.md](phase6-4-ocr-and-downloads.md). Phase 6.4.1
adds automatic native originals to successful analysis and has its own product checks.

Phase 6.4 extends the Phase 6.3 public_url pipeline. The user-facing file tools and
app-only byte transport accept only a Canvas FileReference (course/file/source IDs),
reauthorize the source, request a fresh
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
| Ordinary HTTP result capture, including preparation | 262,144 bytes |
| Exact native resource read / retained app-only byte transport | 5,700,000 bytes, including binary base64/framing |

One shared gate serializes downloads and parser work. Large embedded PDF images
remain constrained by the address-space and CPU limits even when output pixels are
small. Oversized or malformed files fail safely. Native imports/model initialization
are bounded too. Trusted dependencies: Pillow 12.3.0, pypdfium2 5.13.0,
Debian bookworm libtesseract5 5.3.0-2 and tesseract-ocr-eng 1:4.1.0-2.
The model is packaged with the image; no paid OCR API or runtime model fetch exists.
Linux build sample measurements: English model 4,113,088 bytes, aggregate child peak
RSS 107,304 KiB; a synthetic one-page OCR call took 0.380 seconds wall/0.363 CPU seconds.
Actual file complexity varies; fixed limits remain enforced regardless of these
sample timings. The existing Render Free instance has 512 MB and 0.15 CPU. Live
memory/CPU metrics are gated behind a paid compute plan, which was not enabled.
The live three-page hybrid Canvas call took 18.429 seconds including authorization,
download, OCR and serialization. Render warns idle cold starts may add 50 seconds
or more. The model is about 3.92 MiB and no multi-language or GPU stack is installed;
total runtime container-image size was not independently measured.

## Sandbox

The worker gets an already-open read-only artifact FD and a clean environment with
no PAT, Auth0 token, URL or Canvas configuration. Only trusted fixed codec imports,
native libraries and model data load before the unchanged seccomp allowlist seals.
Fixed EXIF metadata support is preloaded as well, so ordinary phone JPEGs do not
need imports after sealing; TIFF image input remains unsupported. No document is
decoded before sealing. Bootstrap rejects extra OS threads and
surviving readable FDs. OpenMP is restricted to one thread. After sealing, arbitrary
open/openat, sockets/connect, exec/fork/clone and artifact writes remain denied.
The worker is killed and reaped before staging is closed on timeout/cancellation.
Private anchored temporary directories are removed in finally on every outcome.

## Automatic native original

Phase 6.4.1 `canvas_get_file_content` also prepares an original within 4 MiB from
the same validated read-only download used by the parser/OCR. It returns a standard
MCP ResourceLink beside bounded text and structured metadata. ChatGPT ingests a
separate authenticated binary resource and renders its native inline file card.
Original bytes are absent from model-visible tool content and structuredContent.
List/metadata tools remain data-only; no custom UI or file-library save is requested.

The exact opaque resource is bound to the current subject/Canvas connection. An
unlisted memory registry holds at most two entries and 4 MiB aggregate, with a real
300-second abandonment expiry and a bounded 10-second successful-delivery retry
grace. Staging is already cleaned before the tool result leaves. Publication and
resource delivery are independently guarded against reflected credentials and
rolled back on failed send/cancellation. Host acceptance is separate from preparing
the reference. See [phase6-4-1-native-downloads.md](phase6-4-1-native-downloads.md)
for transfer invariants and actual ChatGPT product evidence.

## Retained explicit original-file card

canvas_download_file uses a separate remote DTO, never the stdio path descriptor.
The model's preparation result uses the ordinary McpResult structured-output path
and contains compact file metadata, trust, integrity hash and FileReference, with
no hidden metadata, original bytes or base64 anywhere. On a user click,
the static card calls a separate app-only canvas_fetch_original_for_card tool via
the standard authenticated tools/call bridge. That tool has no UI template and
repeats authorization, validated download and staging cleanup. Its bounded original
bytes travel in hidden _meta directly to the card. The card rejects any mismatch
against the prepared full reference, size, hash, filename or MIME type before
uploading. No signed Canvas capability or server path is returned. Credential or
current-capability reflection rejects the original rather than altering it.
Before HTTP transport, bounded base64 is decoded inertly, size/SHA-256 are checked,
and original bytes are checked against server secrets and the presented OAuth
bearer, including common reversible text encodings. Credentials remain outside
document decoders and the OCR worker.
Staging is deleted before the tool returns. Original bytes are not interpreted by
the UI and filenames use textContent and a generated safe name.

The user requests the original after analysis. The assistant calls the download
tool once and directs the user to the card's Attach original for download button,
then Download original. A prepared card is a successful first step; the assistant
must not call the tool again to seek automatic confirmation or claim the card
failed simply because its user-click attachment has not happened yet.
The file card verifies size and SHA-256, feature-detects ChatGPT's optional
uploadFile/getFileDownloadUrl helpers, and attaches the original only on a user
click. It saves the returned ChatGPT file ID to avoid duplicate uploads and
presents a download link only after acceptance. Repeated host globals must preserve
an in-progress upload and its confirmed success status. The v4 resource URI avoids
stale host caches of previous cards. Previous v1/v2/v3 URIs remain registered as
fixed aliases to the same reviewed HTML/CSP for resource compatibility.
None of these static resources is exposed by stdio; unknown resource URIs remain rejected.
There is no arbitrary fetch, external publishing or manual user file upload.
If the host cannot create the file, the card reports failure and the assistant must
not claim a downloadable artifact exists. The earlier real ChatGPT workflow saved
the exact 519944-byte original with a matching local SHA-256. The final split workflow
separately passed app-only retrieval (HTTP 200, 13.469 seconds), client SHA-256,
platform acceptance and its actual download link/three-page PDF preview. No fresh
local hash was confirmed for that final path. Some browsers open PDFs in their viewer
first; use its Download control to save them. Platform retention follows ChatGPT's
policies, rather than a server staging TTL. The code can refresh a temporary link
and reuse a retained file ID after reload; this behavior cannot currently be relied
on in actual saved conversations because the host fails to load those conversations.

The UI declares empty external connection/resource domain lists. It renders no
document HTML, raster content or code. The ChatGPT file service handles its own
temporary download link; Canvas capabilities remain private to the server.

Remote tools: 17 registered: 16 model-visible tools (including content reading and
original preparation), plus one app-only byte transport tool with no UI template.
App visibility is a host routing rule; it does not replace server authentication
or FileReference authorization. Local stdio: original 15 tools and managed local
download behavior. The extra transport tool is an evidence-driven compatibility
change: saved ChatGPT conversations with the original 519944-byte binary tool
result became unloadable even before Attach, while the native-only control reloaded.
The first compact split also failed before Attach; binary size is therefore not
an established cause. No exact host payload-size limit or root cause is claimed.
The final standard DTO preparation also failed actual reload before Attach, despite
having no hidden metadata or bytes. A verified compatibility remedy and actual reload
checks before and after Attach remain required for release completion. Bytes never enter
widgetState, ui/message or model-context updates. Each Attach click reauthorizes the
Canvas source before requesting the platform file link, even when a saved ChatGPT
file ID can be reused. Download original uses the already-issued ChatGPT link.

References: [OpenAI component/file APIs](https://developers.openai.com/plugins/reference),
[MCP Apps UI integration](https://developers.openai.com/plugins/build/chatgpt-ui),
[PDFium rendering](https://pypdfium2.readthedocs.io/en/stable/python_api.html),
[Debian Tesseract](https://packages.debian.org/bookworm/libtesseract5).
