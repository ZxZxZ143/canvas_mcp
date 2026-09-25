# ADR-008: `public_url` as primary Canvas file download strategy

Status: implemented and live-verified for the local Phase 3 file layer.

## Context

Real Narxoz parity testing showed that the browser `/download` route returned HTTP 200 HTML even with the previously tested Canvas route behavior. The Canvas Files API, requested with the scoped bearer token, issued a same-origin `public_url`; fetching it anonymously returned HTTP 200 binary validated as DOCX. Narxoz resolves `canvas.narxoz.kz` to the institution's trusted exact private IP `192.168.4.200` on its internal network.

## Decision

`FileService.download_file(FileReference)` first rechecks scope, enrolled course, source association and fresh file metadata. The infrastructure provider requests `GET /api/v1/files/:id/public_url` with Canvas API authorization, including a verified `submission_id` for an own-submission attachment. It validates the returned URL as an ephemeral infrastructure-private capability. The downloader fetches that capability and all redirects anonymously. A same-origin capability uses configured-origin DNS policy, including only exact operator-trusted private IPs; a different origin requires explicit `CANVAS_DOWNLOAD_ORIGINS` approval and public-only DNS. No capability failure silently falls back to `/download`.

The signed path and query are never exposed through application models, logs, exceptions or persistent storage. The stream still passes response-header, content-length, actual-byte, encoding, MIME, extension, prefix, deadline and SHA-256 checks. ZIP and OOXML candidates additionally pass bounded structural metadata checks before atomic publication. The result remains `trust=untrusted`; no archive is extracted or executed. `DownloadedFile` and the application call signature remain unchanged.

The old browser route remains available only in explicit diagnostics and for compatibility investigation. ADR-006 and ADR-007 describe that historical route and do not define the production strategy after this decision. MCP and document inspection remain outside Phase 3.

## Verification

Production integration tests cover authenticated API issuance, anonymous same-origin and external capability requests, exact private-IP behavior, denied/malformed/unapproved capabilities, no browser fallback, ZIP/OOXML rejection and cleanup. The real parity result establishes that the capability can return valid Narxoz DOCX bytes. On 2026-09-25 the operator ran the installed `FileSample` against Narxoz with exact trusted private IP `192.168.4.200`. The normal `FileService.download_file` path completed `files.public_url`, downloaded 84,996 bytes without redirects, passed format, local containment and SHA-256 checks, and cleaned the managed artifact. Its file listing reported `PARTIAL (discovery_incomplete)`, while metadata, download and cleanup all passed. No file identity or signed URL is recorded here. Phase 3 is complete; MCP remains future work.
