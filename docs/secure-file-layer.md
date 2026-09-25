# Phase 3: secure file layer

The separate [local MIME diagnostic](mime-diagnostic-and-learning.md) may bypass only an explicitly approved final HTTP MIME conflict into quarantine, identify bounded evidence and erase it. Normal production downloads below remain strict and do not consult learned aliases.

The [headers-only redirect/auth diagnostic](redirect-auth-diagnostic.md) traces one candidate without iterating or persisting its body. [ADR-007](architecture/decisions/ADR-007-same-origin-download-auth.md) records the historical browser-route authorization policy; [ADR-008](architecture/decisions/ADR-008-public-url-primary-download.md) records the current production capability flow.

Implemented application-only, GET-only file discovery, fresh resolution and bounded downloads. No MCP, listener, Canvas writes, document parsing, execution, rendering, extraction, inspection dependencies or persistent cache. Phase 1/2 services remain separate. See [ADR-006](architecture/decisions/ADR-006-secure-file-layer.md) for the two deliberate amendments to the original architecture.

The [independent security review](secure-file-review.md) found no critical/high issue; two medium findings were fixed and rechecked. It includes all 16 requested review answers and the remaining limitations.

## Trust and models

Trusted: reviewed code, validated operator configuration, scope binding and generated storage identities. Validated ingress: `FileReference`, numeric course/file/source IDs and bounded typed filters. Untrusted: all Canvas metadata, names, HTTP headers, redirect/signed URLs and bytes, **including after successful download**. SHA-256 is an integrity identifier, not a trust verdict.

`FileReference(file_id, course_id, source_kind, source_id=None, module_id=None)` accepts no URL/path. Course context is mandatory in this personal MVP. Sources are `course_file`, `assignment_attachment`, `submission_attachment` and `module_file`. Assignment/submission source IDs identify the assignment; module source IDs identify the item, with a separate module ID.

`FileMetadata` projects ID, context/source, inert display/filename/MIME evidence, optional size/timestamps and lock/hidden flags. Missing fields retain availability states. URLs and other raw JSON fields are discarded, not returned. A listing is not permission to download.

`DownloadedFile` provides an opaque artifact ID, provenance, generated managed absolute path/name, original inert name metadata, declared Canvas MIME, HTTP MIME, classification, actual size, SHA-256, expiry and `trust=untrusted`. It is the **internal local inspection candidate**, not an MCP or remote DTO. No inspector exists. Future remote projection must exclude paths; future local inspection must be credential-free, isolated and explicitly authorized.

`FileService` depends on `LmsFileQueries` and `FileDownloads`, not HTTP or filesystem APIs. Composition provides `list_course_files`, `get_file_metadata`, `download_file`, `resolve_download` and `cleanup_download`. Cleanup/resolve accept only 32-character generated artifact IDs, not paths. Domain conversion helpers `assignment_attachment_reference`, `submission_attachment_reference`, `module_file_reference` connect Phase 2 metadata without downloading automatically. In particular, use an assignment context's assignment and an attachment member; HTML material references are not attachment capabilities.

## Resolution and flow

```text
User intent -> FileService.download_file(FileReference)
  -> CanvasProvider: scope/profile binding + own enrolled course
  -> fresh source association + Canvas file metadata GET
  -> normalized FileMetadata -> conservative download policy
  -> authenticated GET /api/v1/files/:id/public_url (own submission ID when required)
  -> validate infrastructure-private signed capability and its origin
  -> quota reservation + generated private .part
  -> anonymous capability GET + individually validated redirects
  -> stream -> actual size cap + SHA-256 + bounded type evidence
  -> bounded ZIP/OOXML structural validation before publication
  -> flush -> atomic no-replace rename -> held immutable .blob
  -> DownloadedFile (still untrusted; no inspection)
```

Course metadata uses `/api/v1/courses/{course}/files/{file}`. Assignment attachment resolution rereads the assignment and checks attachment membership. Module resolution rereads the specified module item and requires File type plus exact content ID. Own-submission attachment resolution checks the authenticated student's submission and membership before using `/api/v1/files/{file}` (student submission files can live in a user folder). Missing/forged associations fail before file retrieval. Numeric IDs are never treated as authorization. A fresh application request rechecks the course; within one trusted request the existing authorization memo is reused. Resolve of an existing artifact rechecks upstream permission and stored hash/identity/expiry.

Course listing uses bounded name/MIME filters, enum sort/order and existing opaque query/course/scope-bound Canvas pagination. Default page 25, maximum 100. No arbitrary query parameters, HTML URLs, module ExternalUrl or caller download URL is supported.

## Network boundary

The production downloader **ignores metadata URLs completely**. After fresh source membership and metadata checks, the provider requests `/api/v1/files/:id/public_url` with the Canvas bearer token, using `submission_id` for a verified own-submission attachment. The returned signed URL is an infrastructure-private capability; only the provider can supply it to the production downloader. A denied, malformed, or unapproved capability fails explicitly. There is no automatic browser-route fallback. The old `/download` route remains only in opt-in diagnostics. See the [Canvas Files API](https://canvas.instructure.com/doc/api/files.html#get-public-inline-preview-url).

The scoped Canvas bearer token is included only on authenticated Canvas API requests. The capability GET and every redirect are anonymous, even on the configured Canvas origin; no Canvas cookies, Referer or ambient credentials accompany them. HTTPCore has no automatic redirects, cookie jar, netrc, environment proxy discovery or decompression here. Every request is constructed from scratch with Accept and identity-encoding headers. Set-Cookie and Content-Disposition data are ignored. Body downloads get one attempt; metadata retains normal bounded GET retries. No range/resume.

Redirects require HTTPS:443 and an exact origin from configured Canvas plus operator-reviewed `CANVAS_DOWNLOAD_ORIGINS`. No wildcard, learned allowlist or automatic approval. Relative Canvas redirects are normalized; every hop rejects userinfo, unsupported schemes, downgrade, fragments, malformed escaping/control characters, loops and excessive hops. Reflected raw/URL-encoded bearer tokens in locators are rejected. Locators/signatures are never logged, stored in descriptors or included in exceptions. If an unknown CDN is required, obtain its exact origin through trusted institution/operator information; do not paste signed URLs into chat or automatically add a redirect destination.

Each actual connection resolves DNS and dials a vetted IP literal while retaining original-host TLS SNI/certificate verification. Public/global answers need no static IP pin. For the configured Canvas hostname only, `CANVAS_TRUSTED_PRIVATE_IPS` permits exact operator-configured RFC1918/ULA DNS answers; any mixed untrusted answer rejects the whole resolution. The separate configured-origin pool serves authenticated Canvas routes and anonymous API-issued same-origin capabilities. After any external hop, the public-only pool is permanent, including a return to Canvas. Loopback, link-local, metadata, multicast, reserved, unspecified, IPv4-mapped IPv6 and transition addresses remain blocked on every hop. There is no separate hostname re-resolution after validation. Connection pooling for downloads has keepalive disabled. Origin validation rejects local hostnames and ambiguous syntax. DNS-to-socket rebinding is pinned; an operator-approved compromised endpoint, trusted CA compromise and host network compromise remain outside this guarantee.

Structured logs contain fixed operation/event/reason, correlation ID, safe byte/redirect counts only. HTTPCore debug traces are suppressed during sensitive HTTP. Adapters replace transport/OS exceptions without retaining unsafe exception chains. No payload, filename, local path, query string, header or token logging. Exact bearer reflection in body bytes (even split across chunks) is rejected and partials removed; this is not a general information-flow proof against arbitrary encodings or a compromised same-user process.

## Windows storage and lifecycle

The concrete adapter currently supports **Windows only** and fails closed elsewhere. Ports/models remain portable; a POSIX implementation needs separate confinement tests, not a weaker fallback. Choose a dedicated local-drive absolute `DOWNLOAD_DIRECTORY` outside the workspace/project. Its parent must already exist; leave the leaf nonexistent for secure creation. Existing general folders with inherited/broad ACLs fail closed. UNC, device namespaces, drive-relative paths, ADS, reserved components and trailing-dot/space ambiguity are rejected.

Win32 creates the root/session/files with protected owner + SYSTEM full-control ACLs. Every ancestor is opened with OPEN_REPARSE_POINT and pinned without write/delete sharing, rejecting junctions/reparse points and preventing rename/replacement or write-capable reparse mutation. Opened final paths and volume/file identities are checked. Files reject reparse points and multiple hard links. Files are CREATE_NEW; remote filenames never participate in paths. Each explicit repeat download gets a fresh random identity/hash state; no deduplication or overwrite.

One exclusive delete-on-close root lease permits one live local store. Session `s-{random32}` contains `.session.json` and generated `f-{random32}.part`/`.blob`. Finalization flushes and uses Win32 SetFileInformationByHandle, no replacement, pinned destination ancestors; a terminating NUL is included in the variable-size rename structure. The final file is reopened with a held read handle denying write/delete and verified against its original identity. A `.blob` extension avoids interpreting remote executable/Office names. ACL/handle details follow [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) and [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle).

Failures/cancellation delete only the opened partial by handle; no final descriptor is published. Write/flush/rename/cleanup failures are safe fixed errors. If OS cleanup cannot complete, the operation reports storage failure and leaves no usable capability; inaccessible leftovers require later verified recovery/operator attention. Close cancels in-flight downloads, removes registered files/marker/session and releases handles. The private empty root remains. Normal manual cleanup accepts a scoped artifact ID only. Physical deletion is not secure erasure of disk sectors, backups or external copies.

Artifacts have 24-hour **capability expiry**, checked on access; physical cleanup is on expiry access, explicit cleanup or session close. There is deliberately no timer/daemon. Crash leftovers can remain until the next storage use. At that point the exclusive lease proves no live owner; recovery validates a bounded previous session, ACLs, marker/version/owner, generated names, all file identities and total bytes **before any deletion**. It deletes by handle and removes only the empty session. Unknown entries or invalid markers fail closed and are left untouched. Markers contain owner SID, random session ID and times only, no URL/token/course content; capabilities are never recovered across restart. This is temporary lifecycle metadata, not an index/cache.

The in-memory registry binds principal+connection; another scope cannot resolve or delete an artifact. Future multiple users require separate authenticated namespaces/OS protection, storage admission and secure transport; this single-account, single-loop runtime is not a multi-user service. A malicious same-account process/admin can inspect memory or change ACLs and is outside the local threat boundary. Path knowledge is not a sandbox or revocable OS permission. An already opened external reader may delay deletion (reported safely).

## Size, time and type limits

| Boundary | Implemented limit |
| --- | --- |
| Body | Default 25 MiB; `MAX_DOWNLOAD_BYTES` positive, max 250 MiB |
| Storage | 100 files, 250 MiB actual + full per-pending-file reservations |
| Overall download | `DOWNLOAD_TIMEOUT` 120s default, finite positive <=300s; includes metadata/queue/body |
| API/request and download inactivity | `REQUEST_TIMEOUT` 20s default, positive <=60s; API defaults unchanged |
| Redirects | `MAX_DOWNLOAD_REDIRECTS` 3 default, 0..3; max 4 body HTTP requests |
| Aggregate ledger | 100 attempts, 20 pages; metadata retries + redirect requests consume same ledger |
| Active HTTP | Shared gate of 4 API/download requests per local connection runtime |
| Storage chunks | <=64 KiB; hash incrementally; prefix evidence <=512 bytes |
| Returned envelope | Existing 128 KiB bound; failed envelope creation cleans the artifact |

Content-Length above limit rejects before writing, but actual bytes are authoritative even when absent or smaller. A supplied length must match observed bytes. Zero-byte UTF-8 text is supported. Unsupported Content-Encoding, duplicate/malformed relevant headers, protocol errors and partial bodies fail closed. There is no decompression. HTTPCore/h11 owns wire framing; bytes outside a framed body are not treated as document bytes and connections are not reused. Filesystem operations are synchronous bounded local operations, not hard-real-time preemptible kernel calls; deadline checks surround async streaming/final publication.

Conservative current allowlist: PDF, DOCX/PPTX/XLSX container candidates, ZIP opaque archive, UTF-8 TXT/MD/CSV/JSON/PY/IPYNB. Other formats, HTML/SVG, binaries, macro-enabled and legacy Office are rejected. MIME/extension disagreements reject; generic octet-stream is only a claim and still requires expected prefix/text evidence. A PDF must start `%PDF-`; ZIP/OOXML requires ZIP prefix and a bounded structural check of ZIP directory/member-name metadata before publication. That check rejects mismatched Office roots and active members; it reads at most 1 MiB of metadata and never decompresses or extracts archive content. Text requires valid UTF-8 without NUL. Common executable/HTML/SVG prefixes reject. These checks are **not malware detection or proof of Office document safety**. Scripts/notebooks are inert text only. MIME, structure, hashes and successful TLS never authorize execution or inspection.

## Optional live verification

`python -m canvas_mcp.file_smoke --live` reads profile/courses and course-file listing/metadata only; no root is created. `--download-sample` saves up to THREE plausible references from the first useful page, stops course discovery, then freshly validates their metadata in a separate bounded phase. It tries at most those three validated candidates through the production `public_url` downloader, stopping after ONE accepted sample <=1 MiB is containment/identity/hash-verified and cleaned. Actual-stream enforcement and any smaller configured cap remain in force. Only the existing six fixed per-file policy reasons permit another candidate; capability and permission failures do not fall back to the browser route. No sample means NOT_TESTED with a precise safe reason, not proof of an empty scan. See the [MIME/validation update](mime-validation-followup.md) for current flow and the [rejection follow-up](download-rejection-followup.md) for taxonomy/byte-count interpretation.

Smoke timing remains file-list stage 35s (>0, <=60) and course/file discovery 120s (>0, <=180), with course inventory/authorization stages of 20s. Sample metadata validation has a fixed 60s context/deadline, <=3 probes of <=20s each. Discovery scans <=10 courses and five first-page files per course. Each phase retains a bounded attempt/page ledger. Metadata-only mode keeps its shared deadline and <=10 metadata-probe bound. Sample fallback never restarts discovery or searches later courses after finding a useful page. Production timeouts are unchanged. `--debug` adds private-data-free phase timings/counts and fixed MIME mismatch subreasons. Live Narxoz parity showed the browser route returned HTML, while an anonymously fetched `public_url` returned HTTP 200 binary DOCX that passed validation. On 2026-09-25 the installed production `FileSample` completed `public_url`, downloaded 84,996 bytes with no redirects, passed format, local containment and SHA-256 checks, and cleaned the artifact. File discovery was partial, but three candidate metadata checks and the selected download succeeded. Phase 3 is complete.

Use the [canonical hidden-token helper](authentication-parity.md#canonical-live-helper),
with `-Mode Files` for metadata or `-Mode FileSample` for the explicit sample.
It clears the token in `finally` and gates each mode on profile/course preflight.
Prefer `C:\canvas_mcp_runtime\downloads`: create only the dedicated local parent
and leave the final leaf nonexistent for secure creation. Avoid
OneDrive/Desktop/project/UNC/general-purpose folders; do not weaken permissions
to bypass rejection. The CDN allowlist may be empty; unknown redirect origins
remain rejected until independently approved. No `.env` is created or loaded.
