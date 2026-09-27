# Phase 6.3: remote file content

Status: implemented; final deployment and real ChatGPT evidence pending. Phase 6.4 has not started.

## Transport contract

HTTP has 15 tools: the 14 existing read tools plus `canvas_get_file_content`.
Stdio has its original 15 tools, including `canvas_download_file` and excluding
`canvas_get_file_content`. The installed personal plugin 0.1.0 remains independent.
Its approved Windows download creates a durable managed artifact and returns a
local path. HTTP returns extracted content only; durable download is not exposed.

```text
ChatGPT → canvas_get_file_content → validated FileReference
→ fresh Canvas course/source/file authorization → authenticated public_url API
→ existing anonymous secure downloader → MIME/byte/structure validation
→ private Linux artifact → bounded isolated reader
→ normalized untrusted content → artifact cleanup → bounded MCP result
```

Inputs are positive course/file/source identifiers, with source/module identifiers
where required. There is no URL, signed capability, filesystem path or archive-member
argument. Assignment attachments, module File relations, own submissions and course
files retain the existing fresh membership checks. Wrong OAuth subjects are rejected
before application composition. Canvas credential invalidation is checked after
parsing as well as around the authorized download.

## Readers and bounds

| Format | Representation | Intentional omissions |
| --- | --- | --- |
| PDF | Numbered page text | OCR/images, attachments, active actions |
| DOCX | Body paragraphs, available headings and table rows in document order | Headers/footers, comments, revisions, graphics |
| PPTX | Slides in presentation order; safely associated speaker-note text | Images, media, charts, embedded objects |
| TXT/MD | Inert UTF-8 text | Rendering/execution/following links |
| CSV | Numbered rows with bounded columns | Automatic large table expansion |
| JSON | Valid compact normalized JSON | Trailing members/items or string suffixes when bounded |

Only these seven formats are supported. No ZIP/RAR/7z, macro Office, legacy Office,
executables/scripts, HTML/SVG, images, OCR or notebooks. Plaintext readers reject
recognized binary/nontext signatures even if extension and MIME claim text.

| Bound | Value |
| --- | --- |
| File bytes | 8 MiB, also capped by the operator's existing download limit |
| Extracted Unicode characters | 16,000 total |
| PDF pages/PPTX slides processed | 30 per call; selectors 1–2,000 with at most 30 selected |
| Normalized units | 256 |
| Table cells | 2,000; CSV also 100 rows and 32 columns |
| Whole file call | 50 seconds, including waiting for the single reader slot |
| Worker wall time / CPU | 20 seconds / 8 CPU seconds |
| Worker address space | 256 MiB |
| Worker pipe / actual duplicate MCP serialization | 128 KiB each |
| OOXML members / expanded bytes | Existing structural limit 256 / 20 MiB aggregate |
| Individual XML / expansion ratio | 4 MiB / 100 |
| XML tree | 50,000 nodes, depth 64; no DTD/entities/external resolution |
| PDF decompressed stream / page tree | 4 MiB per configured decoder / 2,000 entries, depth 32 |

The existing HTTP deadline defaults to 65 seconds. File deadlines stay inside it.
One file call at a time per connection avoids overlapping reader memory; other
HTTP requests retain the existing concurrency limit. Each call gets new generated
storage. No cross-request document cache, background sync or keepalive is added.

`truncated=true`, `complete=false` and `file_content_truncated` describe bounded
coverage. Range selection reports original page/slide numbers and total count.
Wire fitting accounts for both MCP text and structured JSON, escaping and UTF-8.
Unicode is sliced before encoding; JSON truncation preserves valid structure,
original keys and types, with no invented placeholders. Availability is recomputed
after wire trimming. Image-only PDF reports unavailable text rather than guessing.

## Security and lifetime

The existing downloader still authenticates only the Canvas `public_url` API.
The capability GET is anonymous, with no Canvas Authorization or cookies. Existing
origin policy, public/global IP validation, pinned DNS, redirect validation, split
horizon policy where configured, MIME evidence and byte limits remain in force.
The remote service's existing public/global network configuration is unchanged.

Linux storage uses generated private directories (0700), exclusive no-follow files
(0600), directory descriptors and inode/link/size checks. The completed artifact is
reopened read-only (0400) and the writer is closed. Only that descriptor is inherited
by a disposable `python -I -B` reader. Its environment contains only LANG and TZ;
no PAT, OAuth token, subject, URL or server artifact path is passed. The worker
verifies size and SHA-256 before extraction.

Before parsing, the worker installs Linux no-new-privileges, resource limits and a
seccomp syscall allowlist. It can read only the artifact FD and write its bounded
result/stdout or discarded stderr. Opening paths, sockets, network, exec, cloning
processes and cross-process memory access are denied. Fixed supported font codecs
are loaded before lockdown; document contents cannot select filesystem imports.
Unknown CPU architectures fail closed. Supported implementations are x86_64 and
aarch64; actual deployed architecture verification is recorded below.

Cleanup owns subprocess creation through kill and reap, even across repeated
cancellation. The parent removes its one generated file and directory only after
the child ends. Success, malformed input, unsupported format, deadline, client
cancellation and output-budget failure all close the temporary scope. Cleanup never
walks arbitrary trees and never removes a substituted directory. Container disposal
is an additional fallback, not the normal cleanup mechanism.

Positive result projection contains file identity/name/MIME/size/hash and inert
content units, coverage, availability and warnings. It contains no local artifact
locator, public_url, signed query or headers. Per-call reflected capability URL/query
is rejected before projection. Existing PAT/JWT/subject secrecy guards remain.
Raw parser exceptions and stderr are never returned or logged.

Content always has `trust=untrusted`. Embedded instructions, links, scripts, PDF
actions, Office external relations and tool-like JSON stay data. They never authorize
commands, filesystem reads, requests, tools or credential access.

## Dependencies, MCP interface and cost

Runtime additions are pinned `pypdf==6.19.0` (BSD-3-Clause, pure Python) and
`defusedxml==0.7.1` (PSF license). PDF maintenance, configuration bounds and published
security history were checked against [PyPI](https://pypi.org/project/pypdf/),
[configuration documentation](https://pypdf.readthedocs.io/en/latest/modules/configuration.html)
and [upstream advisories](https://github.com/py-pdf/pypdf/security/advisories).
Defused XML is a mature, slowly released security wrapper; see its
[package metadata](https://pypi.org/project/defusedxml/) and
[reader implementation](https://github.com/tiran/defusedxml/blob/main/defusedxml/ElementTree.py).
DOCX/PPTX use narrow inert XML/ZIP readers instead of adding Office suites, image
renderers or native PDF engines. The Linux runtime lock includes both packages.
Synthetic Docker test dependencies live outside the copied runtime virtualenv.

Normal tool results preserve the proven ChatGPT interface and fresh source
authorization. MCP Resources would introduce URI/lifetime/discovery handling without
improving this first bounded personal workflow, so none are added. File search is
also deferred; safe bounded PDF/PPTX ranges support follow-up inspection.

Annotations are `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`,
consistent with existing reads: Canvas is unchanged and access is restricted to the
configured authorized Canvas domain. Temporary server storage is an implementation
detail. Annotations are hints rather than security enforcement, per the
[MCP annotation explanation](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/).
The durable local downloader keeps its approval prompt.

Render Free currently provides 512 MB and 0.1 CPU; see
[compute plans](https://render.com/docs/compute-plans). One 256 MiB worker, streaming
8 MiB downloads and narrow readers fit the chosen bounds; difficult documents may
fail safely rather than force an upgrade. Free service sleep and ephemeral storage
remain, as described in [Render Free documentation](https://render.com/docs/free).
Same service/domain/Auth0 audience/PAT/configuration, Free plan and one instance;
no paid service, persistent disk, OCR vendor or new identity infrastructure.

## Safe errors

Fixed application categories include `unsupported_file_format`,
`file_content_unavailable`, `file_content_timeout`, `file_content_too_large` and
`file_parse_error`. `file_content_truncated` is a partial-result warning. Existing
association, authentication, MIME/SSRF and budget errors retain their safe categories.
No parser exception string or capability is used as an error message.

## Verification and independent reviews

Security review found a High repeated-cancellation/spawn lifecycle bug; fixed with
one cancellation-resistant cleanup owner. Independent reproduction with four
cancellations confirmed one child, kill/reap before return and no surviving child.
Medium fixes cover creation/open directory identity and capability reflection.
Re-review found no remaining Critical/High.

Parser review found Medium Unicode codec lazy loading, invalid JSON truncation,
stale availability after wire trimming, overwritten duplicate Office content types
and PDF renamed as TXT. These were fixed and independently reproduced as resolved.
Fixed CJK/PDF codecs and an actual ToUnicode fixture are included in Linux worker
checks. Re-review found no Critical/High.

Tests cover synthetic supported formats, prompt injection as inert data, external
relations, active PDF actions, malformed/oversized/expanding documents, bounded XML,
JSON/UTF-8/wire correctness, OAuth-before-composition, Linux containment, symlinks,
hardlinks, collisions, directory replacement, readonly descriptors, deny-network/
deny-open/deny-exec, hash mismatch, timeout/repeated cancellation and cleanup.
Concurrent calls verify distinct scopes, one reader and cancellation in the queue.

Final test totals, deployed image evidence, real ChatGPT content comparison and
post-idle file retry will be recorded after their actual execution. No real coursework
payloads, private identity, credentials, signed URLs or local downloaded files are
committed to this repository.

Local verification: 1,392 tests passed, 35 Linux-only checks skipped on Windows,
340.44 seconds. Two nonfailure warnings are the upstream Starlette/httpx TestClient
deprecation and pypdf's synthetic active-action fixture API deprecation. Mypy passed
81 source files; Ruff check and format check passed. Sdist/wheel built and installed
into an independent virtualenv: 81 source/installed Python files byte-identical,
80 isolated module imports, 11 CLI help paths and local15/remote15 surfaces passed.
Both dependency checks passed, and all four changed source skills passed the
skill-creator frontmatter validator. The local Docker daemon did not answer its
version probe; mandatory synthetic Linux build checks will run on Render.

## Live evidence

Pending deployment, refreshed 15-tool discovery, actual assignment-related file
explanation, manual source comparison and cold-start file retry. Phase 6.3 is not
marked complete until those succeed.
