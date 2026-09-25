# Local MIME diagnostic and future approval design

Subsequent operator evidence shows a DOCX candidate returning actual HTML, not a
harmless MIME alias. Use the new [headers-only redirect/auth trace](redirect-auth-diagnostic.md)
for that investigation; do not learn the HTML mapping. The original full MIME
diagnostic below remains available for its separate purpose.

## Status and real evidence

The latest operator-reported Narxoz run validated three candidates and rejected all
three with `mime_evidence_mismatch/http_mime_vs_extension`. This establishes a final
HTTP MIME-claim conflict, not the MIME value or actual byte format. Neither is
inferred here. Real findings require the opt-in diagnostic below.

Implemented: a separate local CLI, bounded quarantine identification and optional
private scoped alias registry. **Normal `download_file()` remains strict and
registry-blind.** Built-in MIME tables are unchanged. No MCP, approval tool,
quarantine promotion, document renderer, archive extraction or Canvas write is
implemented. Earlier guides describe normal production downloads; this document
defines the narrow local diagnostic exception.

## Hidden-token local PowerShell command

Use normal, non-transcribed local PowerShell with the current wheel installed.
Create only the parent; the application creates private-ACL managed leaves. Do not
use the repository, Desktop, OneDrive or shared storage. CDN origins must have been
independently operator-approved; never paste a signed download URL.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode MimeDiagnostic -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

This diagnoses without creating aliases. Add the helper's `-RememberIfValidated` only for an
explicit decision to learn verified aliases. Both `--live` and
`--allow-mismatch-diagnostic` are mandatory. Imports/help/missing opt-in do no
network/storage work. No `.env` is automatically read. Process environments/Python
cannot guarantee zeroization of secret memory.

Discovery reuses existing bounds: 120s, <=10 courses, one first page of five files;
separate metadata validation has 60s and <=3 probes. At most three resulting
candidates run **sequentially**, each with fresh authorization/metadata. This is a
bounded sample, not exhaustive search. Each download uses the production timeout
(120s default, <=300s) and the smaller configured size limit or **1 MiB**. Discovery,
profile and close work are additional. Any download-stage non-MIME failure,
unexpected/unsafe/invalid format or cleanup failure stops the run.

## Quarantine and privacy-safe reports

Only exact final-HTTP `HTTP_MIME_VS_EXTENSION` may be bypassed. Missing/malformed
HTTP MIME, Canvas MIME disagreement, unsupported/restricted metadata, identity,
course/source authorization and authentication remain fatal. The shared production
transfer loop preserves HTTPS/exact origins, vetted pinned DNS/original TLS
identity, validated redirects, same-origin Canvas authorization until an
external hop, permanent anonymity thereafter, credential reflection
checks, no cookies/proxies/decompression, budgets/timeouts, streaming/size and
Content-Length checks, unsafe prefixes and UTF-8 validation.

Bytes enter the existing private generated `.part` file held by an exclusive
Windows handle. No `ManagedStore.finish()` or `DownloadedFile` is used. Only after
all streaming checks succeed does the reader verify pinned identity/path, private
ACL, size and SHA-256, read <=1 MiB and identify the format. The pure identification
function receives no credential or I/O capability. This is bounded in-process
identification, **not a separately sandboxed general document parser**.

Transient `QuarantinedFile` remains untrusted and is never externally resolvable.
Pending bytes are erased before learning or reporting success. Cancellation and
connection shutdown use the manager's existing task/cleanup lifecycle. Cleanup
failure is fatal (`cleanup_unconfirmed`), not artifact publication. Existing
verified owned-session crash recovery handles abandoned partial downloads.

Reports: `mime-report-<random>.json` in the diagnostic runtime directory. Fixed
fields: extension class, normalized bare Canvas/HTTP MIME, detected format, method,
result, actual size/SHA-256, redirects, MIME-conflict flag, registry action, fixed
failure reason, trust and erased state. No filename, course/file/user identity,
URL, header object, cookie, query, token, coursework text or body. MIME parameters
are removed; malformed/oversized/URL-like/reflected-secret MIME aborts before
persistence. Console/logs show fixed events/outcomes/counts, not MIME values.

Non-MIME failure means **no further format inspection**. A failure report may map
the existing unsafe-prefix rejection to `UNSAFE`; others use
`INVALID/not_inspected`. Unverified size/hash/redirects are null, not invented.
Reports are private local artifacts; do not casually share them.

## Deterministic evidence and limits

| Expected class | Evidence |
| --- | --- |
| PDF | Leading `%PDF-`; no render/full PDF grammar validation |
| TXT/MD/CSV/PY | Complete strict UTF-8, no NUL, no recognized unsafe prefix; scripts inert |
| JSON | UTF-8/no NUL, JSON syntax, no NaN/Infinity; <=64 depth, <=100,000 structural tokens |
| IPYNB | Inert JSON, nbformat 4, metadata object, <=2,048 cells; no cell execution |
| DOCX/PPTX/XLSX | ZIP, `[Content_Types].xml`, exactly one core family: `word/document.xml`, `ppt/presentation.xml`, `xl/workbook.xml` |
| ZIP | Bounded well-formed directory, `opaque_zip`; generic ZIP never establishes OOXML |

ZIP limits: 256 entries, 256 KiB central directory, 512 bytes/member name, within
the 1 MiB file limit. No member decompression, content parsing, extraction or
writing. Fixed local-header/name/bounds cross-checks reject ambiguous offsets,
overlap, duplicate/case-colliding names, traversal, unsupported encoding, symlinks,
encrypted/ZIP64/multidisk archives and unsupported methods. Known active suffixes,
`.bin`, ActiveX/embedding parts and VBA/XLM macro names are rejected, including XML
macro sheets with no VBA binary.

Only the supported `ExpectedFormat` enum is learnable. Executable, HTML/SVG,
macro-enabled and legacy binary Office extensions cannot be selected even with
consent. Additional diagnostic guards conservatively reject leading markup, OLE
and other executable signatures. Ambiguous packages never teach a claimed Office
format.

Results: `VALIDATED_EXPECTED_FORMAT`, `VALIDATED_OTHER_SAFE_FORMAT`, `UNRECOGNIZED`,
`UNSAFE`, `INVALID`. The requested “SAFE” label means a recognized supported
*format class*, not harmless contents. Signatures/names are not a malware scan,
full document validator or proof against disguised active content/polyglots.
PDFs, text/scripts and notebooks remain untrusted; no result permits execution.

## Registry scope, storage and fail-closed lifecycle

The `MimeCompatibilityRegistry` port has a personal JSON implementation at
`<CANVAS_MIME_RUNTIME_DIRECTORY>\mime-compatibility.json`, default
`C:\canvas_mcp_runtime\diagnostics\mime-compatibility.json`. Source tables/project
files are never changed. Project roots are rejected; runtime names are also
Git-ignored as defense in depth. No registry file exists until a rule is remembered.

Key: hash of **canonical Canvas origin + local application profile + authenticated
Canvas user ID + expected format + bare HTTP MIME**. User ID comes from verified
provider state, never CLI/model input. Origin/profile/user are hashed, not written
in cleartext. Hashes provide namespacing, not anonymization/authentication. Two
universities, users or local profiles have separate rules. Profile defaults to
`personal`. Hosted use must add server-authenticated tenant/principal and
credential-binding version, never caller-selected identity.

Rows contain the format/MIME pair, exact evidence method, count, first/last UTC
timestamps and active/disabled state. Learning requires explicit remember, a pure
HTTP MIME conflict, every other check, expected-format evidence and successful
erasure. One valid observation may establish an alias; the counter counts
observations, not independent files or confidence. No arbitrary rule insertion
tool is exposed. Internal Python methods and same-owner files are trusted local
infrastructure, not a defense against arbitrary code running as the Windows owner.

An existing active rule is **durably disabled before reading the body**. Only a
single-use same-process revalidation ticket plus matching evidence restores it and
increments its counter. Contradictions, cancellation, transfer/storage failures
after that point, or crashes leave it disabled. This intentionally disables on
more than just proven contradictions. Disabled rules are sticky: later remember
consent cannot silently reactivate them. No reset/edit override tool exists.

Private owner/SYSTEM ACLs, pinned non-reparse ancestors, single-link files, exclusive
runtime lease, <=64 KiB JSON, <=64 rules, strict schema/version and flushed atomic
fixed-name registry replacement protect persistence. Reports have generated
non-overwriting names and a directory quota (at most 33 reports overall; new report
admission becomes tighter once a registry exists).
No automatic report/unknown-file deletion occurs. Quota, corruption or unknown
crash temp files fail closed for operator review. Persistence failure poisons the
instance so it cannot continue with unsaved in-memory state.

The supported CLI is sequential; a runtime lease excludes a second process. The
internal registry is **not a concurrent multi-user service**. Future integration
must serialize the whole encounter/validate/commit lifecycle per scoped rule,
including unknown aliases, and invalidate all outstanding tickets on contradiction.
Otherwise an older concurrent success could undo a newer disable. This is a Phase 4
acceptance requirement, not implemented MCP concurrency.

## Future MCP approval and promotion — design only

1. Strict `canvas_download_file` returns `mime_confirmation_required` only for a
   pure final MIME conflict, with expected supported format, sanitized MIME, fixed
   reason and opaque challenge ID; no URL/path/token.
2. Server creates `MimeMismatchChallenge` in bounded memory (proposed <=64, <=120s
   TTL), bound to tenant/principal/Canvas subject, connection/binding version, exact
   file/source identity, expected format, observed bare MIME and policy version.
   No signed URL or locator is persisted.
3. Separate proposed `canvas_download_mime_mismatch` accepts only challenge ID and
   requested `remember_if_validated`. Reject unknown keys, arbitrary URL/path/MIME/
   file identity and stale/unknown/cross-scope challenges. Models cannot create
   challenges or authorize themselves by setting a Boolean.
4. Trusted host offers Cancel, Download once, or Download and remember if validated.
   Explain MIME conflict/quarantine/inert validation, without headers/paths/secrets.
   Bind the actual host-approved mode/challenge to execution authorization; model
   arguments are not consent.
5. Consume single-use challenge atomically before work, including failed attempts.
   Invalidate on cancel/expiry/rebinding/session end. Freshly authorize and resolve
   metadata; changed expected format/MIME requires a new challenge and approval.
6. Secure quarantine -> all non-MIME checks -> deterministic expected format ->
   optional scoped alias -> safe publication. Failure learns nothing. Future
   promotion rechecks identity/hash and uses generated non-overwriting artifacts,
   expiry, authorization-on-resolve and cleanup. Published contents stay untrusted.

Proposed host configuration using eventual names:

```toml
[plugins."canvas-student".mcp_servers.canvas.tools.canvas_download_mime_mismatch]
approval_mode = "prompt"
```

The per-tool policy shape follows official
[Codex plugin configuration](https://developers.openai.com/plugins/build/plugins).
The OpenAI docs skill verified this planned configuration; it did not install or
modify user configuration. Host approval is additional: annotations do not replace
server authorization/confirmation, as the
[MCP server guide](https://developers.openai.com/plugins/build/mcp-server) explains.
If the host cannot reliably attest the chosen mode, keep the operation disabled.

Future aliases may allow HTTP MIME claims to proceed to mandatory byte validation;
they never skip prefix/UTF-8/OOXML/format checks. Approval cannot bypass SSRF,
redirects, credentials, authorization, size, timeouts, storage or dangerous-format
policy. Normal downloads do **not** perform this future registry lookup today.

Implemented privacy-safe events: `mime_mismatch_detected`,
`mime_diagnostic_validated`, `mime_compatibility_learned`,
`mime_compatibility_reconfirmed`, `mime_compatibility_disabled`. Planned-only host
events: `mime_mismatch_confirmation_requested`, `mime_mismatch_approved`,
`mime_mismatch_cancelled`. Only fixed outcomes/correlation IDs, never private data.

## Verification and independent review

Synthetic tests cover formats/work bounds, malformed/ambiguous/macro ZIPs, scoped
aliases, consent/reconfirmation/sticky disable, corrupt state, native Windows
ACL/lease/atomic writes, quarantine erasure, privacy, CLI bounds and failure stop.
All Phase 1–3 tests must pass before live access; final actual checks are reported
with delivery.

The read-only `mime-learning-security-reviewer` found a HIGH XML macro-sheet learning
gap. Explicit XLM/VBA member-name rejection and unit/native quarantine regressions
fix it; the independent original reproduction now reports `UNSAFE`, not learnable.
A MEDIUM future concurrency limitation is constrained by today's sequential CLI
and recorded above as mandatory Phase 4 single-flight/ticket invalidation work.

Final reviewer follow-up also closed a diagnostic prefix-recognition hardening
item: long leading whitespace, including form-feed and vertical-tab, cannot hide
recognized HTML/SVG/executable prefixes. Nine whitespace regressions cover it.
Both LOW wording corrections (port count and report quota) were applied. Final
read-only disposition: **no unresolved CRITICAL/HIGH findings**. The reviewer ran
238 distinct initial security tests, then 101 format/quarantine tests and all nine
final whitespace cases; these latter runs overlap earlier cases, not extra tests
to add to the full-suite total.

### Actual final verification, 2026-09-24

- `pytest -q --tb=short`: **829 passed in 100.11s**, including all existing Phase 1–3 tests.
- `mypy`: **53 source files**, no issues.
- `ruff check src tests`: passed; `ruff format --check src tests`: **89 files** already formatted.
- `build --no-isolation`: wheel and source distribution built successfully.
- Wheel force-reinstalled into the project `.venv`; `pip check`: no broken requirements.
- `python -I`: all **53 installed modules** imported; every installed Python file matched source hashes and the exact source file set.
- Installed help checks passed for `smoke`, `academic_smoke`, `file_smoke`, `mime_probe`; three installed diagnostic opt-in rejection checks passed.
- Only after all automated checks, process-environment presence checks found **neither CANVAS_BASE_URL nor CANVAS_ACCESS_TOKEN available**. No live request was attempted, no real MIME values were inferred, and no personal runtime registry/report was created. Use the hidden-token command above locally.
