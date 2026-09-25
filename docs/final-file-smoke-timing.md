# Final Phase 3 smoke timing follow-up

Historical timing-fix report. Its timing limits remain current, but the subsequent [download rejection follow-up](download-rejection-followup.md) adds fixed safe rejection diagnostics and up to three saved candidates from the first useful page. That guide supersedes the immediate-single-candidate sample behavior and contains the latest verification/retest instructions. Results below describe this earlier change only.

Scope: the optional live diagnostic only. Runtime hash comparison against the previously installed package found changes only in `file_smoke.py`. Production Canvas HTTP/defaults, FileService, academic behavior, file policy, redirects/Authorization isolation, DNS/TLS checks, Windows managed storage, streaming limits and download deadlines remain unchanged. No MCP, new network capability, cache, parser or execution path is added.

## What the second run establishes

The operator observed successful profile/course discovery, two course-file stage timeouts and a third cancellation at the 60-second overall deadline. All three listings failed to return; therefore zero returned listings/files/candidates is correct. No metadata resolution or body download was attempted. The previous successful metadata-only run establishes accessibility, not fixed response latency. The cause of Narxoz's varying response time is unknown.

The previous 20-second `course_file_probe` surrounded `FileService.list_course_files`, which first authorizes the course and then issues the Files API request. Time spent in authorization, gate wait or retries came out of the same 20 seconds. This outer cap could expire before the HTTP client's own 20-second request deadline/retry handling. The three serial probes consumed the 60-second discovery allowance. This was not evidence of a production downloader failure or concurrent course fan-out.

## Current timing hierarchy

| Layer | Default / hard bound | Scope |
| --- | --- | --- |
| `REQUEST_TIMEOUT` | 20s; existing finite positive <=60s | Production per-HTTP-attempt timeout, unchanged |
| Course inventory | 20s fixed, clipped by discovery deadline | Smoke course-list operation |
| Course authorization | 20s fixed, clipped by discovery deadline | Explicit smoke authorization stage |
| `--probe-timeout` | 35s; finite >0 and <=60s | Smoke file-list stage, including its unchanged HTTP retries, excluding authorization |
| Candidate filtering | Synchronous, at most 5 records | Existing metadata policy, no I/O; optional measured duration |
| File metadata | 20s fixed, clipped by discovery deadline | Smoke fresh metadata resolution |
| `--discovery-timeout` | 120s; finite >0 and <=180s | Entire smoke discovery, all inventory/auth/list/metadata work |
| `DOWNLOAD_TIMEOUT` | 120s; existing finite positive <=300s | Production download including fresh metadata, unchanged |

The smoke obtains a **fresh context**, sets its validated discovery deadline once before any work, and retains its existing 100-attempt/20-page ledger. It does not accidentally inherit the production aggregate's 60-second deadline, and does not reset the deadline/counters per course. All ordinary connection queries still use the existing production defaults.

Each stage uses `min(stage_start + allowance, global_deadline)`. It is an explicit hard cap on the whole operation, not permission to stretch any one HTTP attempt. A file-list operation can now take more than 20 seconds overall through bounded retries (for example, one 20-second attempt followed by a short successful retry). A single HTTP response that exceeds `REQUEST_TIMEOUT` still times out; increasing a smoke option does not change that rule. The 35-second stage may cut short a later retry; its terminal `probe_timeout` diagnostic makes that intentional orchestration cap visible. `request_budget_exceeded` identifies the global/ledger stop. The total command also includes profile, production download and verification outside discovery; 120 seconds is not a promise about the entire command's elapsed time.

## Candidate-first request flow

Discovery remains strictly sequential:

```text
course inventory (maximum 10)
  -> course_authorization (existing AcademicService.get_course)
  -> course_file_probe (FileService.list_course_files, first page=5, size ascending)
  -> candidate_filter (unchanged type/known-size/<=1 MiB policy)
  -> file_metadata_probe (fresh resolution + same policy)
  -> first valid candidate: STOP discovery immediately
  -> unchanged secure download -> containment/hash verification -> cleanup
```

Authorization uses the **same RequestContext** as listing/metadata. The existing CanvasProvider authorization memo prevents a duplicate course GET inside FileService; the smoke does not fabricate authorization from the course-list response. No normal application contract was changed. Ordinary `list_course_files()` still defaults to 25, accepts up to 100 and keeps its original filtering/pagination behavior.

The smoke reads one first page per course, now **5 files instead of 25**. This reduces candidate-discovery payload; it is not a claim that Canvas response latency scales proportionally with page size. It does not paginate or expand the page automatically. If an eligible file lies beyond that first page, the scan stays explicitly incomplete rather than claiming none exists.

Limits remain: at most 10 courses, 10 file-list pages / 50 file entries, 10 metadata probes, 11 total paged requests, and 100 HTTP attempts including retries. With the profile already checked, discovery has at most 31 logical GET operations (1 course list + 10 authorization + 10 file lists + 10 metadata reads), or at most 93 attempts at the unchanged three-attempt limit, additionally constrained by the shared ledger and deadline. A fresh candidate is committed without later awaits/probes/budget checks. Partial discovery remains sufficient to download one valid sample; earlier timeouts do not discard a later positive result.

## Safe diagnostics and policy

`--debug` adds only bounded integer `duration_ms` on terminal stage lines and effective `budget_ms` on STARTED lines. Both are clipped to 0..180000 ms. Authorization, file-list, filtering and metadata names are fixed and distinct. Normal output omits timing fields. No course/file identifiers, names, URL/query strings, tokens, absolute clock timestamps or local paths are added.

Every started async stage emits PASS/FAIL/TIMEOUT/CANCELLED where practical. Cancellation unwinds the currently awaited coroutine; no detached/sibling probes are launched. Only fixed safe error codes are reported; no exception text. Production HTTP lifecycle semantics remain untouched. Invalid timeout input fails before opening the connection; CLI validation errors do not echo arbitrary invalid value text.

Candidate policy remains verified Canvas identity, supported classification, known size <=1 MiB (or smaller operator cap), no caller URL/path or HTML/ExternalUrl capability. Production rechecks permission/metadata and actual streamed size. No type relaxation, extra retries or fallback network origins were added.

## Recommended local root and manual retest

Use a dedicated **local, nonsynchronized** parent such as `C:\canvas_mcp_runtime`. The operator creates that parent; leave the final `downloads` leaf **nonexistent on first use**, so secure storage creates its private ACLs. On later runs a managed leaf created by this implementation may be reused subject to its existing checks. Do not pre-create the leaf as a general-purpose directory or change permissions to make a rejection disappear.

Avoid OneDrive, Desktop, the project/workspace, UNC/network folders and pre-existing general-purpose folders. Do not move or delete a directory just to satisfy the example; choose another dedicated local root if it is occupied. This is operator setup advice only, not a relaxation of native storage checks.

In a separate PowerShell terminal:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode FileSample -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

Blank CDN allowlist is permitted: it deliberately rejects unknown external redirect origins. Such rejection reports only the safe existing diagnostic (for example `unsafe_redirect`), never prints a signed URL or automatically trusts the host. Obtain any required exact HTTPS origin independently from trusted institutional/operator information before configuring it. Do not paste tokens or signed URLs into chat.

No live Canvas requests were performed for this fix. These bounded changes improve diagnostic control; they cannot guarantee a timely upstream response or live download success.

## Verification and independent review

Actual local verification on 2026-09-24:

- Full pytest suite: **570 passed in 77.04 seconds**, including unchanged redirect, SSRF, Authorization isolation, containment, size and native Windows storage regressions.
- Focused discovery/smoke/file integration suite: **128 passed in 21.54 seconds**.
- New simulated-clock cases cover a 25-second-equivalent listing under the 35-second cap, a first-course timeout followed by a candidate after the old 60-second deadline, and cancellation at the 120-second discovery deadline. No tens-of-seconds sleeps were introduced.
- mypy: **45 source files passed**.
- Ruff lint passed; format check: **72 files already formatted**.
- Wheel build and local virtual-environment reinstall succeeded.
- Isolated installed-package imports: **45 modules passed** with DNS/connection entry points guarded against unexpected network access. All 45 installed Python source hashes match the workspace.
- All three installed smoke CLI help commands passed; file smoke advertises both timing options and debug mode.
- `pip check`: **No broken requirements found**.

The independent, read-only `final-file-smoke-reviewer` reported **no actionable CRITICAL, HIGH, MEDIUM or LOW findings** after inspecting timing, nested deadlines, short-circuiting, finite request bounds, production contracts, security and diagnostic privacy. Its independent runs passed **57 smoke tests** and **1 native Windows partial-discovery → download → hash verification → cleanup test**. No findings required a code change.

Residual limitations are intentional: bounded discovery can still expire on slow upstream responses, and eligible files beyond a course's first five entries remain undiscovered. Neither automated tests nor review establish a successful real Narxoz download.
