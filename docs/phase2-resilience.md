# Phase 2 resilience and safe live diagnostics

Scope: academic reads only. No MCP, files/downloads, Canvas writes, timeout-default increase or institution-specific behavior. All test payloads are synthetic. An operator's next live run is needed to establish the actual remote failure category and whether that upstream condition still exists.

## Traced original failure

Before this change, `academic_smoke.run` selected `course_id or courses[0]`, counted assignments and modules, then fetched the first assignment again with limit 1. An empty first course therefore left submission/context untested even if other active courses had work.

Immediately after `Submission data accessible: not tested (no assignments)`, execution was:

1. `CanvasConnection.get_upcoming()` constructs a fresh context with a 60-second shared ledger.
2. `AcademicService.get_upcoming()` calls `application.workload.scan()`.
3. `discover_courses()` paginates active student courses (`courses` log label).
4. For each course, `CanvasProvider.list_assignments()` calls `get_course()` to verify the current-user student enrollment, then `_page()` / `CanvasHttpClient.get_assignments_page()` / `_page_request()` / `_get()`.
5. Both the course authorization GET and assignments GET (including continuation pages) were labeled `academic`.

The missing `Upcoming items` line places the reported failure inside `get_upcoming`, not the later announcements, calendar or grades steps. Assuming the supplied excerpt contains the final request failure, its generic `academic` label narrows it to course authorization or assignment listing/pagination within that scan. **It cannot distinguish those endpoints or identify timeout vs network failure vs 5xx.** The original `upstream_unavailable` mapping discarded that distinction. Two failed attempts also do not establish a timeout: configured attempt limits or the shared deadline could have stopped further retries. We do not infer an institution-specific endpoint incompatibility.

## Diagnostics

HTTP and pagination logs use fixed labels: `profile`, `courses`, `courses.get`, `assignments.list`, `assignments.get`, `submission.get`, `modules.list`, `modules.items`, `module_sequence.get`, `calendar.list`, `announcements.list`, `grades.get`.

Failure events retain an opaque UUID, attempt number, allowlisted `reason`, `retry_exhausted`, and `retry_stop` (`not_retryable`, `attempt_limit`, `request_budget`, `retry_scheduled`). The latter distinguishes the configured attempt ceiling from an earlier shared-budget stop. No course IDs, names, bodies, grades, queries, URLs, headers or raw exception strings are logged. HTTP library tracing remains suppressed while secrets/transport are in use.

The stable application class/code taxonomy remains compatible. `UpstreamUnavailableError.diagnostic_code` additionally distinguishes `upstream_timeout`, `upstream_connection_error` and `upstream_http_500` through `upstream_http_599`. Constructors accept a closed enum and validated numeric status, not arbitrary upstream text. Sanitizing an exception copies only approved fields into a fresh exception, retaining no original traceback/cause/context. Malformed data remains `malformed_upstream`, not a transient outage. Credential-source failures are separately sanitized and fatal, even when their original type is a timeout or OSError.

The academic smoke reports the actual failing step and safe reason, without a traceback. It no longer prints the student's display name. `--debug` also prints per-step PASS/PARTIAL/FAIL/NOT_TESTED and fixed component/warning codes. The aggregate step is named `upcoming`; endpoint logs identify the underlying HTTP operation. Optional context warnings retain the same safe diagnostic detail.

## Partial-success policy

| Failure or outcome | Workload policy |
| --- | --- |
| Timeout, connection failure, exhausted 5xx retries within one course | Discard that course's incomplete scan; warn; continue on the same ledger |
| Course GET or assignment-list 404 within one course | Same partial policy; may represent deleted/inaccessible course or unavailable endpoint, never assert the exact reason |
| Any 403 / `AuthorizationError` | Fatal: HTTP status alone cannot prove course-only denial; also used for learner/subject/scope mismatches |
| Authentication / credential / configuration failure | Fatal immediately; 401 still invalidates the connection |
| Aggregate validation, malformed data, invariant/programming failure | Fatal, never converted to an empty result |
| Exhausted 429 / `RateLimitError` | Fatal: conservatively treat token/account throttling as shared, do not fan out more requests |
| Shared deadline, total HTTP-attempt or page ledger exhausted | Stop, no further requests; retain completed courses if any, mark remaining coverage failed/uncompleted |
| Content/body/cursor-state/output-size bounds or other `BudgetExceededError` | Fatal safety/invariant limit; not confused with shared ledger exhaustion |
| Discovery failure or incomplete discovery | Fatal: the candidate source set is not authoritative |
| Zero fully scanned courses from a nonempty candidate set | Raise the first safe course failure; shared-budget exhaustion before any success raises that budget error |
| A fully scanned course has no assignments/matching deadlines | Authoritative success; can support an empty incomplete aggregate if other courses failed |
| Exhaustive discovery returns zero active courses | Complete empty result; successful discovery is evidence of absence |

Only `get_upcoming` and `get_overdue` orchestrate these independent course scans. Per-course pages/items/warnings are staged and merged only after that course is exhausted. Thus a failed later page cannot claim the earlier pages are an authoritative course result. Direct `list_assignments`, `get_course`, etc. still raise normally.

Both explicit course lists and discovered course lists apply this policy. Normalized `CourseCoverage` keeps requested/scanned/failed IDs for the authorized caller. Warnings never contain private names; CLI/log projections omit these IDs. A failed/uncompleted course makes both the workload page and envelope incomplete. No cursor is fabricated, no `more_pages` warning suggests a nonexistent resume capability, and no failed course is counted as scanned.

Calendar and announcements were also reviewed: they send a **single batched Canvas request** for the requested contexts, not independent per-course content requests. Failure of that request cannot safely be attributed to one course, and their existing query-bound pagination contract has no per-course merge cursor. They therefore remain atomic/fatal (including 403/404/5xx); no silent context dropping or speculative splitting/retrying was added. Their per-course authorization prerequisites also remain fatal. Assignment context retains its narrower pre-existing optional-subquery policy; mandatory course/assignment reads are not weakened.

## Request strategy and bounds

Assignment lists remain course-scoped and request `include[]=submission` with effective user assignment dates. This agrees with the [Canvas assignments API](https://developerdocs.instructure.com/services/canvas/resources/assignments). No per-assignment submission GETs occur in workload scans. Course GETs are retained: this implementation's listing mapper does not establish the verified subject/current student-enrollment invariant required by the academic adapter. Treating list-filter membership alone as cached authorization would weaken that check. The [courses API](https://developerdocs.instructure.com/services/canvas/resources/courses) documents enrollment-filtered discovery; richer verified authorization reuse would require a separately tested mapping change.

For a bound profile and successful one-page discovery, without retries, requested full coverage costs `1 + N course-authorizations + N assignment pages`. Each logical HTTP request has at most three attempts, including the first. There is no provider or aggregate retry layer. All pages and attempts share one ledger and the existing 60-second deadline; the per-attempt network timeout remains configurable (default 20 seconds).

| Scope | Single-page no-retry demand | Default behavior / worst-case attempt ceiling |
| --- | --- | --- |
| 5 courses | 11 GETs, 6 pages | Up to 33 attempts for those requests. If paginated, at most `3 × (20 pages + 5 course GETs) = 75` attempts |
| 20 courses | Full coverage would require 41 GETs, 21 pages | The 20-page ceiling prevents full default coverage. With quick successful responses, 19 courses finish in 40 GETs (the last course authorization precedes the rejected page); incomplete coverage is returned. With retries, never more than 100 attempts |

An initially unbound profile costs at most three additional attempts, **within** the same 100-attempt/60-second budget (five-course single-page upper bound 36; paginated 78). More discovery pages reduce assignment-page capacity. The deadline can stop sooner than any count ceiling; a slow first course can consume the entire budget and yield zero-success failure. Partial success does not promise all later courses can be attempted after a full-budget outage. To obtain exhaustive coverage for 20 or more courses, request smaller explicit groups; no automatic budget reset or outer retry was added.

Synthetic transport tests verify 11/33 attempts for five one-page courses; 40 GETs and 19 completed courses for twenty fast courses; and exactly 100 attempts with partial coverage when each logical request succeeds on its third attempt. They also cover continuation-page failure, direct errors, course-local 404 versus fatal 403, unknown credential errors, privacy, all/partial/zero success, shared deadline stopping, and smoke selection/limits.

See [the academic guide](academic-read-layer.md#offline-verification-and-optional-live-command) for the hidden-token PowerShell retest. Independent review and final verification are recorded in [the resilience review](phase2-resilience-review.md).
