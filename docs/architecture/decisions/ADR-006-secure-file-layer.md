# ADR-006: Phase 3 authenticated first hop and internal local candidate

Status: implemented; narrowly supersedes two choices in ADR-004. Its original
hop-zero-only authorization rule is superseded by [ADR-007](ADR-007-same-origin-download-auth.md).

The Phase 3 request explicitly requires authenticated Canvas download routes and an internal `DownloadedFile.local_path` inspection handoff. Retain ID-only ingress and freshly verified context. Ignore metadata locators, construct a fixed Canvas download route and allow the Canvas bearer token only on that initial request. Every redirect request is anonymous, including a return to Canvas. Exact approved origins, pinned public DNS, TLS and all existing limits remain mandatory. API redirects remain prohibited.

`DownloadedFile` may contain the generated managed absolute path internally; this is not permission to accept a path argument or expose server paths remotely. `resolve_download` rechecks scope, current source permission, TTL and file identity/hash. No MCP serialization/host inspection is implemented or certified here. Future local inspection must be isolated and credential-free; remote transport must provide another authenticated resource boundary.

The current concrete storage adapter uses tested native Windows ACL/no-reparse/held-handle primitives and fails closed on other systems. Single-loop/single-store operation, generated immutable identities, explicit/session cleanup and bounded owned crash recovery avoid a database or daemon. These constraints preserve portability of contracts without pretending untested filesystem guarantees.
