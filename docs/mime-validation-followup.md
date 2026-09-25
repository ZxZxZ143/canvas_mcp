# MIME evidence diagnostics and separate sample-validation budget

## Live evidence and scope

The latest operator report found four listing-eligible entries but validated only one before the shared discovery deadline. The second metadata probe had approximately 2.7 seconds left. That one downloaded candidate passed two redirect validations and was rejected with `mime_evidence_mismatch`. The report does not reveal the actual MIME values; none are inferred here. This update makes the source of a MIME mismatch explicit and gives saved sample identities a separate bounded validation phase.

No live Canvas requests were made for this update. Successful real Narxoz download remains unverified. No MCP, parser, execution, archive extraction, persistent candidate cache, or Canvas write is added.

## Actual production evidence model

| Source | Role and existing behavior |
| --- | --- |
| Canvas `filename` extension | Untrusted claim selecting an allowlisted expected MIME and provisional classification. Missing/unsupported extension rejects. `display_name` does **not** select the type. |
| Canvas metadata MIME | Untrusted optional claim. If present, must satisfy the existing compatibility predicate against the filename-derived expectation. A conflicting/unsupported claim can veto; it cannot prove safe content. Missing metadata MIME alone does not reject. |
| HTTP Content-Type | Independent untrusted response claim checked against the same filename-derived expectation. Not compared pairwise with Canvas MIME. Missing/empty HTTP MIME rejects. Non-ASCII decoding and duplicate headers retain their separate protocol/malformed error paths. |
| PDF / ZIP prefix | Bounded supporting evidence required for the selected PDF or ZIP/Office-candidate classification. PDF requires its existing leading signature; ZIP/Office permits the existing ZIP signatures. Generic MIME cannot bypass these checks. |
| UTF-8 / NUL evidence | Every text chunk must decode as strict UTF-8 without NUL, including successful decoder finalization at EOF. Not a JSON parser, language detector or proof of safe execution. |
| Executable / HTML / SVG prefix | Explicit contradictory byte evidence: existing rejection regardless of permitted MIME claims. |

The relationship is a sequence of necessary checks, **not a strongest-source-wins precedence**: filename chooses a candidate class; supplied claims must be compatible; required byte evidence must also pass. No source can override a contradiction from another enforced check. Successful checks yield an untrusted inert artifact, not trusted document contents or permission to inspect/execute it. Office ZIP signatures identify only container candidates; internal package format, macros and polyglots are not parsed here.

The compatibility predicate normalizes MIME parameters/case, then permits the expected specific MIME, the approved generic MIME, and the already-existing compatibility set for selected text formats. Therefore different Canvas/HTTP MIME strings need not contradict one another when both satisfy that predicate. There is no fabricated `canvas_mime_vs_http_mime` comparison.

## Generic MIME: no production acceptance fix needed

Architecturally, generic MIME is **absence of useful positive type evidence**, not proof of format and not inherently contradictory. Acceptance is conditional on the allowlisted filename/classification and all existing prefix/text checks.

The approved architecture explicitly names `application/octet-stream` in [the security contract](architecture/security.md). The current implementation already permits that spelling and still runs BytePolicy. In particular, consistent PDF metadata/filename + that generic HTTP value + the required PDF prefix was already accepted. Regression tests now exercise this directly, including generic claims at both metadata and HTTP layers, and failure when body evidence contradicts them.

`binary/octet-stream` is not in the current compatibility set or an explicitly approved alias. It remains rejected. That is an unsupported claim under the existing allowlist, not evidence that its bytes are a different format. No alias was added simply to accommodate this live test. No MIME acceptance predicate, normalization, extension map, prefix check, UTF-8 rule or classification changed. The independent reviewer compared 836 representative old/new MIME outcomes and found them identical.

## Fixed safe subreasons

The existing `DownloadRejectionReason.MIME_MISMATCH` now optionally carries an exact `MimeMismatchReason` enum, preserved through transport/manager sanitization:

- `mime_evidence_mismatch/canvas_mime_vs_extension`: present Canvas MIME failed the filename-derived compatibility check.
- `mime_evidence_mismatch/http_mime_vs_extension`: nonempty HTTP MIME failed that check, including unapproved generic aliases.
- `mime_evidence_mismatch/http_mime_missing`: missing or empty normalized HTTP MIME failed that check.

Prefix and text contradictions continue to report `prefix_mismatch`, `unsafe_content_prefix` or `invalid_text_encoding`, not a MIME mismatch. There is no new generic-MIME-insufficient-evidence rejection: approved generic MIME proceeds to the existing byte-evidence checks.

The exception's public code/text remains `download_rejected`; unspecified manually created MIME errors may retain the older generic diagnostic, but both production MIME call sites now label their source. Only fixed enums are retained: no MIME value, extension, name, identity, URL, signed query, path, response body or credential is included. Arbitrary subreason strings and subreasons attached to non-MIME failures are rejected. Non-debug `download_candidate` rejection output remains generic; metadata-stage failures can report fixed safe subcategories even without debug. Existing structured logs retain their fixed reason codes and completed temporary-write count semantics.

## Bounded sample phases

| Phase / limit | Exact final bound |
| --- | --- |
| A: course/file discovery | 120s default via `--discovery-timeout`, finite >0 and <=180s |
| Course inventory and individual course authorization | 20s each, clipped by phase A |
| File-list probe | 35s default via `--probe-timeout`, finite >0 and <=60s, clipped by phase A |
| Discovery volume | <=10 courses; one first page of <=5 files per course; retain <=3 unique plausible references |
| B: sample metadata validation | **60s fixed total**, starting with a new context/deadline after phase A |
| Each validation probe | **20s**, clipped by phase B; includes that operation's authorization/HTTP retries |
| Metadata candidates | **<=3 total probes**, with <=3 validated candidates retained |
| C: secure sample download | **<=3 attempts**; each has unchanged production DOWNLOAD_TIMEOUT (default120s, finite >0 and <=300s) |
| Actual bytes per attempt | <=1 MiB or a smaller configured operator limit; rejected data still erased |

Phase A performs no fresh candidate metadata requests in sample mode. Once a useful listing page is returned, it saves up to three unique policy-plausible references and stops scanning—even if that page supplies only one or two. Listing eligibility is not download permission. No pagination, later-course search to fill slots, repeated discovery, or identity persistence is introduced.

Phase B obtains a **new** RequestContext with its own attempt/page ledger and empty authorization memo. Its 60-second deadline is set once before work and never reset per candidate. The original phase A deadline/counters remain untouched. Production metadata resolution therefore authorizes the course afresh as needed; subsequent candidates may use only the normal phase-B-local memo. A metadata timeout is recorded as partial and the next saved reference is tried if phase time remains. Unknown size is skipped. The existing explicit file-policy fallback whitelist may skip a rejected metadata candidate; authentication, authorization, configuration, storage, credential invariants, size/security subclasses and unknown errors abort. Validated candidates survive a later timeout/budget stop.

Each phase retains the existing 100-attempt/20-page ledger. After the separate profile check, phase A has at most 21 logical GETs / 63 retry-inclusive attempts and 11 paged requests. Phase B has no pagination and at most three resolutions (conservatively <=6 authorization/metadata GETs, <=18 attempts), also bounded by its ledger and 60s deadline. Production request timeout remains 20s by default; normal production aggregate timeout remains 60s.

Phase C calls the unchanged public secure download for each retained candidate, with **fresh authorization and metadata again**. Its reviewed exact-class/six-reason fallback whitelist is unchanged. No prefix/type rule is bypassed, and a rejected file never receives a usable descriptor. Rejection cleanup must succeed before fallback; storage failure is fatal. Hash verification and cleanup after a successful download remain outside fallback, and at most one successful sample is verified/cleaned.

Three download attempts may consume **360s total at default settings**, or **900s at the configured production maximum**. These are separate from discovery/validation and the existing bounded profile, verification and connection-close work; 120s is not a whole-command promise. All loops, retries and phases remain finite.

Metadata-only smoke retains its earlier behavior: first valid metadata candidate, no downloads, <=10 metadata probes under the shared discovery limit. The new three-phase separation applies specifically to `--download-sample`.

## Diagnostics and outcomes

Debug output distinguishes `file_discovery`, `candidate_validation`, `validated_candidates`, individual metadata probes and download candidates. It contains only fixed labels, reasons, counts and bounded durations. Listing identities are never called validated before phase B succeeds.

A validation timeout gives PARTIAL and can still lead to a valid download. If all metadata probes fail, download is NOT_TESTED with `candidate_validation_budget_exceeded`, `candidate_validation_timeout` or `candidate_validation_incomplete`, as applicable—not `no_files`. All saved downloads rejected by allowed policy reasons still yields `download FAIL (no_policy_downloadable_candidate)` after their fixed safe diagnostics. A successful sample reports `download_candidate PASS`, `download PASS`, hash verification and cleanup.

## Hidden-token live retest

Run in a separate PowerShell terminal. Use a dedicated nonsynchronized local parent. Create only that parent; leave the `downloads` leaf nonexistent on first use so secure storage creates it. An existing implementation-managed leaf may be reused subject to its checks. Avoid OneDrive, Desktop, project/workspace, UNC/network or general-purpose roots; do not weaken ACLs or delete an occupied directory to make the example pass.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode FileSample -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

An empty CDN allowlist remains valid. Use only independently approved exact origins; unapproved redirects fail safely and are never automatically trusted or printed. Do not share tokens or signed URLs. No acceptance or allowlist change is justified solely by a live rejection.

## Actual verification

Completed locally on 2026-09-24:

- Full pytest: **687 passed in 82.42 seconds**, including all Phase 1–3 security regressions.
- Focused discovery/fallback/rejection/MIME matrix: **155 passed in 11.51 seconds**.
- Separate virtual-clock phase-budget tests plus existing native smoke flow: **20 passed in 0.71 seconds**. Cases include discovery finding three references at 119s, full fresh validation opportunity, first timeout/later success, all three timeouts at the 60s phase cap, fatal errors, external cancellation and no repeated discovery.
- mypy: **45 source files passed**.
- Ruff lint passed; format check: **76 files already formatted**.
- Wheel build and project-local reinstall passed.
- Isolated installed-package imports: **45 modules passed** with DNS/connection entry points guarded against unexpected network access.
- SHA-256 comparison: **all 45 installed Python source files match the workspace**. Compared with the prior installed baseline before replacement, only smoke, error taxonomy, file policy diagnostic annotations and downloader source labeling changed.
- All three installed CLI help commands and three no-live/invalid-timeout CLI guard checks passed.
- `pip check`: **No broken requirements found**.

Existing security tests remain green. Smoke-only fixtures were updated to expect the extra fresh phase-B course authorization and separate request contexts; diagnostic assertions now expect the richer fixed subreason. No rejection/acceptance security assertion was relaxed. Automated upstream transports are synthetic; none of these checks establishes live Narxoz download success.

## Independent review

The read-only `mime-policy-reviewer` completed the requested MIME architecture, generic-evidence, diagnostic privacy, budget, fallback and artifact-lifecycle review:

- **CRITICAL / HIGH / MEDIUM: none.**
- **LOW: resolved.** The documentation had overstated that all non-debug rejection output was generic. It now accurately distinguishes generic download-candidate output from fixed safe metadata-stage subcategories. Related architecture diagnostic wording was aligned with the user-requested fixed subreason capability, without changing data privacy or acceptance policy. The reviewer rechecked both corrections.
- **No unresolved findings.**

The reviewer independently executed **208 passing tests**, including native Windows integration and virtual-clock budgets, and compared **836 representative MIME outcomes** to the prior installed implementation: acceptance and normalization were identical. The reviewer made no edits or live Canvas requests.
