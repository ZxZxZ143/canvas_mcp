# Phase 3 download rejection diagnostics and bounded sample fallback

Historical rejection-diagnostics report. The subsequent [MIME/validation update](mime-validation-followup.md) adds fixed MIME subreasons and separates sample metadata validation from course discovery. It supersedes this report's shared discovery/validation budget. [ADR-007](architecture/decisions/ADR-007-same-origin-download-auth.md) later changed redirect authorization while retaining the rejection taxonomy and fallback whitelist. The old anonymous-redirect/public-only wording below describes the earlier policy.

## Evidence and the byte-count correction

The reported Narxoz invocation reached the real secure downloader and validated two redirects, then reported `download_rejected`. This establishes that discovery, fresh metadata/authorization and those redirect validations succeeded. The exact rejection cannot be recovered from that generic historical message. No new live requests were made for this change, and Phase 3 live download success is not claimed.

One important correction: the old manager's FAILED event omitted `bytes_written`, so EventLogger emitted its default **0 regardless of temporary writes**. Consequently the reported zero did not establish that the failure preceded every document write. The updated FAILED event supplies `pending.count` (or zero before a pending file exists): bytes from completed managed temporary writes, not published/usable bytes. A low-level failed partial write is not a completed write. Rejected files still undergo the original abort/erase path and never return a usable descriptor.

## Exhaustive post-redirect generic rejection paths

These are fixed code categories on the existing rejection conditions; no response evidence is retained in an error.

| Safe category | Existing rejection path | Possible before any completed temporary write? |
| --- | --- | --- |
| `anonymous_download_unauthorized` | Final anonymous redirect response is HTTP 401 | Yes; does not revoke the Canvas credential |
| `unsupported_content_encoding` | Content-Encoding is neither empty nor identity | Yes |
| `invalid_content_length` | Nonempty Content-Length exceeds 12 characters or is not decimal digits | Yes |
| `mime_evidence_mismatch` | Response MIME fails the existing expected-type/octet-stream/text compatibility rule; missing MIME also fails | Yes |
| `transfer_protocol_error` | Existing boundary catches HTTPCore protocol errors, ValueError or UnicodeError; includes non-ASCII Content-Type decoding | Yes, or later during transfer |
| `credential_reflection` | Token appears in the incoming chunk plus retained boundary tail | Yes in the first chunk, or after earlier chunks; always fatal |
| `invalid_text_encoding` | Text chunk is invalid UTF-8 or includes NUL, or final incremental UTF-8 flush is incomplete | Yes during first chunk; final incomplete sequence requires earlier nonempty bytes |
| `content_length_mismatch` | Completed response byte count differs from declared Content-Length | Yes for an empty body with nonzero declared length; also after writes |
| `prefix_mismatch` | PDF lacks `%PDF-`, or ZIP/Office candidate lacks either accepted ZIP signature, at EOF | Yes for an empty document/archive, otherwise after writes |
| `unsafe_content_prefix` | EOF prefix check detects executable, HTML or SVG prefix | No with a genuine zero count; compatible with the historical default-zero log |

Before redirects, metadata rejection additionally has `metadata_restricted` (locked/hidden flags) and `unsupported_file_extension`; metadata MIME disagreement uses `mime_evidence_mismatch`. These checks are not late post-redirect checks.

The remaining generic default `download_rejected` is retained for unspecified callers, but every concrete production generic raise/catch site in policy/download now sets an explicit enum reason. The diagnostic reason survives both transport and manager sanitization; exception text/public `code` remains `download_rejected`.

Distinct existing errors are not relabeled: excessive metadata/declared/streamed size is `file_too_large`; unapproved/looping/excessive redirects are `unsafe_redirect`; duplicate inspected headers and otherwise unsupported statuses are `malformed_upstream`. Initial 401, 403, 404, 429 and 5xx keep their existing authentication/authorization/not-found/rate-limit/upstream mappings. Storage and timeout errors retain their own codes. There is no Content-Disposition acceptance/rejection rule in this implementation: its value is not used for naming or surfaced. No speculative new acceptance check was introduced.

## Diagnostic privacy

`DownloadRejectionReason` is an exact validated enum, not a caller-supplied string. It carries no filename, MIME value, course/file ID, URL/query, token, headers, response body or Content-Disposition value. Sanitization creates fresh exceptions containing only fixed fields and discards transport context. Existing structured failure logs retain their generic allowlisted reason; only their numeric completed-write count is corrected.

`file_smoke --debug` displays the fixed category, for example `download_candidate REJECTED (mime_evidence_mismatch)` before safe fallback, or `download FAIL (credential_reflection)` when aborting. Non-debug rejection output remains generic. Existing stage durations/budgets still contain only bounded integers.

## Bounded candidate fallback

- Metadata-only smoke still stops at the first resolved eligible candidate and never downloads.
- Sample smoke keeps up to **3 freshly metadata-validated candidates** from the first already-returned useful page, then stops course discovery. Duplicate references within that page are not retried. It does not scan later courses to fill missing slots, paginate, rediscover after rejection, or persist identities.
- The shared discovery limits remain **10 courses, one five-file page per course, 10 metadata probes, 100 HTTP attempts and 20 pages**, with 35s listing / 20s metadata / 120s global defaults and the existing hard bounds. Up to 31 logical discovery GETs / 93 retry-inclusive attempts remain possible. Already retained candidates survive an alternate's timeout/budget exhaustion.
- Every sample attempt uses the existing public `download_file(reference)`, which gives it a fresh context, course authorization and metadata resolution. Metadata validation during discovery is never permission to skip production revalidation.
- Fallback catches only the exact `DownloadRejectedError` class and six explicit reasons: `unsupported_file_extension`, `mime_evidence_mismatch`, `invalid_text_encoding`, `unsafe_content_prefix`, `prefix_mismatch`, `unsupported_content_encoding`. These are per-file policy failures, not permission to make the rejected file usable.
- Everything else aborts: unspecified or future reasons, subclasses (`unsafe_redirect`, `file_too_large`), credential reflection, metadata access restrictions, anonymous 401, malformed framing/length/protocol, authentication/authorization/configuration/storage failures, deadlines and unexpected exceptions. This deliberately conservative policy does not blindly retry errors.
- The manager must finish rejection cleanup before returning the policy error. Abort failure is `storage_error`, so it cannot be skipped. Once a download succeeds, verification/hash checking and cleanup sit **outside** the fallback catch. Failure there aborts, rather than attempting another file.
- At most **3 secure download attempts**, each with the actual <=1 MiB smoke cap (or smaller operator cap), unchanged `DOWNLOAD_TIMEOUT` (default 120s, hard maximum 300s), redirect cap, exact-origin policy, SSRF checks and storage quota. At most one successful sample is verified/cleaned. The 120s discovery deadline is not a whole-command limit: sample attempts can separately consume up to three download allowances.
- If every saved candidate is rejected by a fallback-eligible policy rule: `download FAIL (no_policy_downloadable_candidate)`, exit 1. No candidate found remains NOT_TESTED under the existing discovery semantics.

## Production security and scope

Production edits only annotate existing rejection sites, preserve the fixed reason through sanitization, and correct failed byte-count reporting. The prior installed wheel was used to compare all production predicates/lifecycle operations. Redirect validation, exact-origin allowlist, anonymous redirected requests, public-DNS/TLS pinning, byte limits, MIME/prefix/text evidence, storage confinement, Windows ACL/reparse checks, atomic publication, abort and cleanup are unchanged. No new extension or MIME acceptance, decompression, parsing, execution, archive extraction, cache, MCP, or Canvas write was added. Existing security tests were not edited.

## Manual hidden-token retest

Use a separate PowerShell terminal. Prefer this dedicated nonsynchronized local parent and leave the `downloads` leaf nonexistent on first use. An existing managed leaf may be reused subject to existing checks. Avoid OneDrive, Desktop, project/workspace, UNC/network and pre-existing general-purpose roots. Do not loosen permissions or delete an occupied folder to make the example work.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode FileSample -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

An empty CDN allowlist is valid; retain only origins independently approved by the operator. Unknown redirect origins still fail safely; neither diagnostics nor fallback print signed URLs or automatically trust origins. Do not paste tokens or signed URLs into chat. This change cannot guarantee a policy-acceptable sample or a timely upstream response.

## Actual verification and independent review

Completed locally on 2026-09-24:

- Final full pytest suite: **643 passed in 96.77 seconds**, including the unchanged redirect, SSRF, Authorization isolation, path containment, size and native Windows storage tests. New regressions also verify retained candidates survive alternate/global timeout, no rediscovery, the three-attempt cap, fatal error boundaries and privacy.
- New real-manager/native-storage rejection suite: **21 passed in 7.69 seconds**. Two validated redirects exercise each applicable fixed rejection category, zero/nonzero completed temporary write counts, erase/no-publication, unchanged distinct error codes, and first-rejected/second-successful smoke fallback with fresh authorization, hash verification and cleanup.
- mypy: **45 source files passed**.
- Ruff lint passed; format check: **74 files already formatted**.
- Wheel build and project-local reinstall passed.
- Isolated installed-package imports: **45 modules passed**, with DNS/connection entry points guarded against unexpected network access. All 45 installed Python source hashes match the workspace.
- All three installed CLI help commands passed. Three installed file-smoke guard checks (missing live opt-in and invalid timing bounds) exited safely before live execution.
- `pip check`: **No broken requirements found**.

The independent read-only `download-rejection-reviewer` found **no actionable findings and no HIGH/CRITICAL issues**. It compared production rejection predicates/lifecycle operations against the prior installed baseline and independently passed **125 focused tests in 9.83 seconds**, including native Windows storage and two-redirect rejection paths. A transient page-dedup regression seen during implementation was fixed before final verification; existing tests were not changed. No review finding required a security-policy change.

These are synthetic/local results, not evidence of a successful Narxoz live download. Retesting is still required to learn the actual safe category for the reported candidate.
