# ADR-004: Download by authorized file ID into controlled artifacts

Status: accepted after architecture review.

Phase 3 amendment: [ADR-006](ADR-006-secure-file-layer.md) supersedes the anonymous initial content request and path-free internal DTO decisions below. The remaining confinement, provenance and isolated-inspection requirements stand. The following paragraphs retain the original rationale, not the current first-hop/DTO contract.

An arbitrary URL/path downloader exposes SSRF and filesystem writes. Accept course/file IDs only; resolve fresh private source metadata, validate exact origins/addresses and redirect hops, and use a separate no-auth content client. Store bounded bytes under generated names with platform-proven confinement, quotas, expiry and no overwrites. Do not parse, extract or execute in that credential-bearing process.

Keep normalized application results path-free. For local stdio only, a narrow scoped resolver provides a generated absolute path/digest/expiry to the trusted host so existing file readers can consume it in isolation. Opaque handles alone were rejected as an incomplete user workflow; introducing a generic filesystem-read tool was also rejected. Host sandbox/bridge compatibility is a release gate, and a path is not itself a sandbox. Remote transport must replace the handoff with authenticated streaming.

Reject unsupported/auth-required content sources and fail closed on platforms without proven safe creation/reopen. Container formats are inert downloads inspected only in a constrained reader; broad archive extraction and legacy-format conversion are deferred. These limitations keep the MVP honest about its security guarantees.
