# Phase 3 live-smoke discovery reliability follow-up

Historical first follow-up: the 20-second stages / 60-second discovery / 25-file page described below have since been superseded by the [final smoke timing update](final-file-smoke-timing.md). Current smoke defaults are a separately authorized 35-second file-list stage, 20-second metadata stage, 120-second discovery and 5-file page, with bounded CLI overrides/debug timing. Production security behavior remains unchanged. The earlier evidence/review/results below are preserved as history, not the current timing contract.

Scope: optional `file_smoke` orchestration only. No MCP or changes to production FileService, Canvas HTTP, academic aggregates, download policy, redirect/Authorization/DNS controls, Windows storage, byte/deadline limits, parsing or execution behavior. Source hashes against the installed pre-fix Phase 3 build confirmed that **only `file_smoke.py` changed among runtime modules**.

## Observed result and exact code path

The operator reported a successful metadata-only run (3 checked courses, 13 files, metadata PASS) followed by a sample run with PARTIAL discovery, zero checked courses/files and NOT_TESTED download. This was a discovery failure; the secure body download was not attempted.

Both flags called the **same `discover(connection)`**, already sequential, with no course tasks/fan-out. `--download-sample` only additionally capped the configured body limit at 1 MiB before opening the connection; it did not select a different scan or concurrency algorithm. Discovery already stopped after the first freshly resolved eligible candidate. The current source and installed smoke matched before edits, so no local stale-install difference explained the two code paths.

The defect was a compound `asyncio.timeout(5)` around every course probe:

1. `FileService.list_course_files` calls provider course authorization (`courses.get` unless memoized), then `files.list`. Each upstream request normally has a 20-second allowance, up to three attempts, and consumes the shared ledger.
2. The smoke's much shorter **five-second total** could cancel that chain, including gate wait, HTTP and retry delay, even after course authorization succeeded.
3. `checked` and `files` incremented only after the entire file-list service call returned. Thus `courses.get` success followed by a cancelled file listing still counted zero.
4. Metadata resolution also used the remaining part of those same five seconds. A usable listing could therefore be followed by a starved metadata check.
5. `CancelledError` from the outer smoke deadline propagates through the HTTP client, bypassing its `except Exception` and post-exception FAILED emission. Its `finally` still resets logging state and its async contexts close resources. STARTED without a matching HTTP terminal event does not prove concurrency or a dangling task.
6. The smoke collapsed caught timeouts/errors/budget exhaustion into `partial=True`, then printed the same no-eligible reason for every missing candidate. The flags did not expose why a listing never returned.

This establishes the implementation-level cancellation/accounting/diagnostic mechanism. The supplied logs lack per-request timing/error details, so they **do not establish the underlying cause of Narxoz's slower second run** or prove that every probe hit the identical cutoff. No live request was made during this fix.

## Fix and request behavior

Discovery remains deterministic/sequential, one awaited operation at a time; no background tasks, broad fan-out or shielded work. Only the smoke has changed:

- A course-file listing (including existing course authorization) has a **20-second stage cap**. A candidate metadata resolution receives a **separate 20-second stage cap**, not the listing's leftover allowance. These are ceilings, not extra global budget.
- Every stage uses the same monotonic deadline and HTTP/page ledger. Overall discovery remains **60 seconds**, or a shorter preexisting context deadline. There is no reset per course/metadata candidate.
- Existing HTTP request timeouts/retries and shared four-request gate remain unchanged. A standalone sequential smoke normally has no self-induced gate contention.
- At most **one course-list page / ten course probes / 25 first-page files per course**. At most **ten metadata resolutions total** protects against spending the entire run refreshing many stale candidates. No continuation crawling.
- The normal run performs at most **31 logical discovery GET operations**: one course list, ten course authorization reads, ten file listings and ten metadata reads. Up to three attempts each gives at most 93 HTTP attempts, still constrained by the shared 100-attempt ledger and 60-second deadline. At most 11 paged requests, within the 20-page ledger. Profile runs before discovery; download and verification have their existing independent contexts and bounds.
- Check budget before starting more work. An active stage is cancelled and unwound by its timeout before control continues/returns; there are no sibling tasks to await or discard.
- Retain successful listing counters immediately, before any metadata await. Commit the first freshly resolved eligible candidate immediately and perform **no subsequent probe, await or budget check** before ending discovery. An exhausted ledger after that successful response does not erase the candidate.
- A partial result with a candidate proceeds to the existing secure download, fresh authorization, containment/hash verification and cleanup. Completeness is never a prerequisite for a positive sample.

No timeout can guarantee a remote response; several slow courses can still consume the 60-second cap. A metadata-stage failure moves on to another course instead of repeatedly waiting on the same course. Complete policy rejections can continue to another candidate within the metadata cap. If the ten-metadata-probe allowance is exhausted, report `request_budget_exceeded` rather than claiming no eligible file.

## Observation and diagnostics

`DiscoveryResult` is internal smoke state, not a changed production contract:

| Field | Meaning |
| --- | --- |
| `courses_attempted` | Course-file probes started after the preflight budget check |
| `courses_completed` | File-list service calls that returned successfully; not just authorization GETs and not proof of course exhaustion |
| `courses_partial_or_failed` | Affected course probes, counted once each for incomplete page coverage or failed/cancelled metadata/listing work |
| `files_seen` | Entries in successfully returned lists, retained if later metadata fails |
| `eligible_candidates` | Listed entries passing unchanged policy, known-size and effective smoke-cap checks, before fresh resolution |
| `metadata_probes` | Fresh metadata resolutions started; maximum ten |
| `candidate` | First freshly resolved metadata record that still passes those same checks |

Completed and partial counters can overlap: a listing can succeed while metadata fails or further pages remain. Output labels this explicitly as `listings_completed`, replacing the ambiguous `Checked courses` count. Names, IDs, URLs, contents and paths are not printed.

`complete` describes exhausted scan coverage, not merely success in finding a sample. An early positive stop with unvisited courses is PARTIAL but still downloads. Complete negative coverage requires the course page and all encountered file pages to be exhausted and no failed/timed-out/budget-stopped probe.

| Outcome | Safe reason and action |
| --- | --- |
| Complete scan, no files | `no_files`; metadata/download NOT_TESTED |
| Complete scan, files but none eligible | `no_policy_eligible_file`; NOT_TESTED |
| Overall deadline/ledger or metadata-probe allowance exhausted without candidate | `request_budget_exceeded`; NOT_TESTED, never “no eligible file” |
| Every attempted course listing fails | `all_course_probes_failed`, unless budget/auth reason is more specific |
| Course/metadata authorization unavailable (403) | `authorization_unavailable`; incomplete result |
| Page cap, local-stage timeout or other incomplete coverage | `discovery_incomplete` when no more specific summary applies |
| Partial coverage with candidate | metadata PASS; download proceeds despite files PARTIAL |

Each smoke stage emits STARTED followed by PASS, FAIL, TIMEOUT or CANCELLED with a fixed code. Stage names (`course_discovery`, `course_file_probe`, `file_metadata_probe`) explain API STARTED lines interrupted by smoke cancellation. Overall deadline cancellation uses `request_budget_exceeded`; shorter stage timeout uses `probe_timeout`; external cancellation uses `cancelled` and propagates. Fatal authentication, malformed data, configuration and security errors remain errors, not empty/partial success. Production HTTP logging semantics are unchanged.

## Security and verification scope

The sample must retain a verified Canvas FileReference, supported type and known declared size <=1 MiB (or a smaller configured download cap), both in listing and fresh metadata. The production downloader still freshly authorizes, applies the unchanged policy and enforces actual bytes, redirects, network and managed storage controls. A successful discovery is not authority to bypass any of them.

Synthetic regressions cover immediate first-match return, first-course timeout followed by successful download, preserving a committed candidate at the budget boundary, completed-list observations surviving metadata cancellation, deadline/attempt/page/probe caps, complete negative reasons, all-failure/auth reasons, page truncation, fatal security errors, external cancellation, safe terminal diagnostics and smaller configured file limits. An end-to-end fixture exercises partial discovery through the real FileService/downloader/Windows storage/hash/cleanup path using stubbed HTTP only.

The user must rerun `python -m canvas_mcp.file_smoke --live --download-sample` manually with the existing hidden-token PowerShell pattern and independently approved CDN origins. This change does not establish live download success.

## Independent read-only review

Final main-agent verification: full `pytest -q` **544 passed**; focused file/smoke suite **102 passed**; mypy **45 source files**; Ruff lint and formatting check **72 files**; wheel build and force-reinstall; isolated import checks **45 modules**; all three CLI `--help` checks; installed/source SHA-256 equality **45 files**; `pip check` with no broken requirements. All commands used the project's `.venv` Python. No live Canvas access was performed.

The requested `file_smoke_reliability_reviewer` inspected the code, tests and this report without editing files or making live calls. No actionable CRITICAL, HIGH, MEDIUM or LOW finding was established. It independently verified that only `file_smoke.py` differed from the prior installed Phase 3 runtime, and ran **32 passing focused tests**, including native Windows managed download/verification/cleanup after partial discovery.

The review confirmed immediate candidate commitment, inline cancellation without sibling tasks/leaks, correct partial-vs-empty diagnostics, unchanged fresh download authorization, the 31 logical GET / 93-attempt upper bound and the shared discovery ledger. The remaining deliberate limitation is that sufficiently slow upstream responses can still exhaust a bounded sequential scan. Root-cause claims do not attribute that slowness to a specific live network/server cause.
