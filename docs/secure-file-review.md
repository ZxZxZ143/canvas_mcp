# Independent Phase 3 secure-file review

Historical review of the Phase 3 policy. [ADR-007](architecture/decisions/ADR-007-same-origin-download-auth.md)
later changed same-origin redirect authorization. Findings and test counts below
describe the policy at the time of this review.

The requested `secure_file_reviewer` subagent reviewed the completed implementation read-only, including native Windows storage, HTTP controls, source authorization, smoke behavior, tests and documentation. It did not edit files, use real credentials or contact Canvas. Independent checks: 141 targeted tests, followed by 5 fix-regression cases, passed.

## Findings and dispositions

Main-agent final verification after fixes: `pytest -q` **518 passed**; mypy **45 source files**; Ruff lint and formatting check **71 files** passed; wheel build and force-reinstall succeeded; isolated imports and installed/source SHA-256 equality **45 modules/files**; all three CLI help commands and `pip check` passed. Commands used the project's `.venv` Python. Tests use synthetic data only; no new parsing dependencies.

No **CRITICAL** or **HIGH** finding was established.

- **MEDIUM, fixed and rechecked:** `resolve_download` fetched fresh metadata but originally ignored newly returned lock/hidden flags. It now applies the same metadata policy before republishing a descriptor. All four flag variants have regressions; local cleanup still works after revocation.
- **MEDIUM, fixed and rechecked:** A 401 on an anonymous redirect request originally invalidated Canvas authentication. Only an authenticated initial-hop 401 can now do so; anonymous-hop 401 is download rejection. Regression confirms subsequent profile access still succeeds.
- **LOW, clarified:** Original architecture language about anonymous initial downloads and path-free internal models was updated or marked historical under ADR-006. Legacy artifact ports are explicitly unimplemented sketches, not another runtime implementation.

## Required reviewer answers

| Question | Independent conclusion |
| --- | --- |
| 1. Arbitrary URLs from user input? | No supported ingress: IDs/references only, internally constructed routes. |
| 2. URLs from Canvas HTML? | No; external/HTML links are not download capabilities and metadata URLs are ignored. |
| 3. Authorization leakage across redirects? | No demonstrated forwarding path; only hop zero carries it. |
| 4. Signed URLs in logs/errors? | No demonstrated supported-output path; private locators, fixed errors, suppressed HTTP traces. |
| 5. Filename traversal? | No; names are metadata, storage names generated. |
| 6. UNC/device/ADS escape? | No demonstrated path; root namespace/component validation rejects them. |
| 7. Junction/symlink escape? | No demonstrated escape within the stated threat model: no-reparse opens, pinned ancestors, private ACLs, identity verification. |
| 8. Content-Length bypass? | No; streamed bytes are authoritative before writes. |
| 9. Endless stream? | Async network bounded by overall/inactivity deadlines; synchronous kernel/filesystem stalls are not hard-real-time preemptible. |
| 10. Concurrent overwrite? | No; exclusive creation, fresh identities, quota reservations and no-replace rename. |
| 11. Usable failed partial? | No published capability; ordinary failures remove bytes. Crash/OS cleanup failures can leave private residuals requiring verified recovery/attention. |
| 12. Automatic execution? | None. |
| 13. Archive extraction? | None; opaque byte storage only. |
| 14. Success implies trust? | No; descriptors explicitly untrusted, digest/type evidence not certification. |
| 15. Future wrong-user file? | Current principal/connection checks reject cross-scope access; restart invalidates capabilities. Multi-user transport/OS isolation not certified. |
| 16. MVP simplicity? | Yes; necessary Windows complexity isolated, no database/daemon/cache/parser/MCP. |

## Remaining risks

- **MEDIUM residual:** Paths/copies previously given to the same OS account cannot be revoked. Fresh permission checks still have an upstream change race. A future inspector must be credential-free and isolated; no unrestricted reader is authorized by a download.
- **LOW operational:** External readers or OS errors can prevent cleanup. Unknown remnants deliberately block storage rather than cause broad deletion.
- **LOW maintenance:** Native Windows APIs require continuing platform-specific regression tests; other platforms fail closed.
- Format evidence is superficial by design. PDFs, Office candidates and archives can still contain malicious data. Successful download conveys no execution, extraction or parsing authority.

No live file smoke was run by either agent. The user's previously reported Phase 1/2 live results do not establish Phase 3 live compatibility; both optional Phase 3 commands remain for explicit operator verification.
