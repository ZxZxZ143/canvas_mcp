# Academic read layer (Phase 2)

This phase extends, rather than replaces, the Phase 1 client, credential boundary, origin policy, pagination and normalized models. Phase 1's live connection was reported successful by the operator. A subsequent Phase 2 live run exposed a cross-course failure; see [Phase 2 resilience](phase2-resilience.md) for the traced path, diagnostics, partial-success policy and request bounds. Changes are verified with synthetic offline fixtures; a live retest remains an explicit operator action. MCP, file downloads and all writes remain absent.

## Supported reads and application entry points

Use `async with open_canvas_connection() as connection` from `canvas_mcp.composition`. The connection constructs a trusted, aware observation time and a fresh bounded request context per operation. `AcademicService` can also be tested directly with a normalized `LmsAcademicQueries` fake and a supplied context.

| Connection capability | Canvas GET source |
| --- | --- |
| `get_profile`, `list_courses` | Existing `/api/v1/users/self`, `/api/v1/courses` |
| `get_course(course_id)` | `/api/v1/courses/:course_id` |
| `list_assignments(course_id, page, query)` | `/api/v1/courses/:course_id/assignments` |
| `get_assignment(course_id, assignment_id)` | `/api/v1/courses/:course_id/assignments/:assignment_id` |
| `get_submission(course_id, assignment_id)` | The assignment's `/submissions/self` |
| `list_modules(course_id, page)` | `/api/v1/courses/:course_id/modules` |
| `list_module_items(course_id, module_id, page)` | `/api/v1/courses/:course_id/modules/:module_id/items` |
| `get_assignment_context(course_id, assignment_id)` | Course + assignment + `/module_item_sequence?asset_type=Assignment&asset_id=...`; optional own-submission fallback |
| `get_upcoming(...)`, `get_overdue(...)` | Active student courses + paginated assignments with embedded own submissions |
| `list_calendar_events(start_at, end_at, courses=(...), page=..., event_type="event")` | `/api/v1/calendar_events`, course contexts only; type `event` or `assignment` |
| `list_announcements(start_at, end_at, courses=(), page=...)` | `/api/v1/announcements`; empty courses means active-course discovery |
| `get_course_grade(course_id)` | Course enrollments filtered by verified subject and `StudentEnrollment`; published grade keys only |

IDs are canonical positive decimal strings in this Canvas implementation. `AssignmentFilter` allows a search term of 1–100 characters, `order_by` of `position`, `name`, `due_at`, and optional `bucket` of `past`, `overdue`, `undated`, `ungraded`, `unsubmitted`, `upcoming`, `future`. No arbitrary query dictionary is accepted. Search is URL-encoded, never a path. These filters extend the earlier draft's local-only search sketch; they do not change an ADR or expose MCP.

Lists return `Result[Page[T]]`. Continue using the same operation, filters and limit with `PageRequest(limit, next_cursor)` until `next_cursor` is null. Cursors are one-use, connection/query-bound, opaque and bounded. A changed filter/date/course set is invalid. Calendar accepts at most 10 courses, matching Canvas's API ceiling; split larger scopes explicitly. Announcements accepts at most 50. A default announcement cursor also binds the resolved course set; if active membership changes, restart with explicit course IDs.

## Normalized models and content

- `Assignment`: identity/title, effective due/unlock/lock dates, points, submission types, allowed attempts, published/can-submit facts, rubric, attachment metadata, own submission and classified references. Lists omit descriptions/rubrics/references (`not_requested`); detail reads include them.
- `RubricCriterion` / `RubricRating`: opaque rubric IDs, descriptions/long descriptions, points and ratings. No separate rubric requests. Canvas's omitted rubric is known absence (`Observed(available, None)`), not an invented empty rubric.
- `Submission`: verified own user/assignment IDs, workflow evidence, independent submission/graded/late/missing/excused/required dimensions, submitted/graded timestamps, type, attempt and attachment metadata. It deliberately excludes scores, comments, body, graders and other users. Scores are a separate explicit course-grade operation.
- `Module`, `ModuleItem`, `CompletionRequirement`, `ModuleSequence`: structure, ordering, progress facts and current/previous/next item context. External items remain metadata. Sequence results preserve multiple assignment appearances; at Canvas's 10-appearance cap, completeness is explicitly unknown.
- `AttachmentMetadata`: display name, filename, type, byte count and timestamps. It is **not** a downloaded file or an authorized download capability. Source/signed URLs are omitted. HTML references may contain a first-party ID but remain `not_fetched`; Phase 3 must authorize file metadata independently.
- `CalendarEvent`, `Announcement`: bounded untrusted content, contextual course identity and date/availability facts. Unrelated nested users, reservations, authors and child payloads are not projected.
- `CourseGrade`: current/final published score and display grade, current points. Missing or null grades remain absent/unknown, never zero. Conflicting multi-section enrollments return unavailable rather than choosing a score. Unposted grade keys are never projected. A normalized optional `Course.grades_hidden` records Canvas's `hide_final_grades`; when true, totals are unavailable and the adapter does not request enrollment scores.

The existing `ExternalText` representation is retained: **plain text converted from HTML**, `format="plain"`, `trust="untrusted"`, plus an explicit truncation marker. Raw HTML is not mislabeled as text: it is not returned at all. Scripts/styles, markup and non-whitespace control characters are removed. Academic bodies preserve block/list/preformatted line boundaries; display names and titles remain single-line. Absolute URLs, recognizable Canvas paths, protocol-relative host URLs and signed-query references are replaced with a fixed omission marker without consuming adjacent paragraphs or ordinary division/comment syntax. This lossy projection preserves readable instructions, not source markup/layout. Description references are classified before conversion and are never dereferenced. Descriptions are bounded to 16,000 characters; titles to 512. Rubric/attachment collections have hard bounds, with explicit errors instead of silent tuple clipping.

## Identity and trust boundary

Every academic course read checks the course's current-user enrollment summary for a student enrollment. Teacher/observer-only access is not sufficient for this personal learner API. Child endpoints remain course-scoped, mapped parent IDs must match, and submissions/enrollments must match the verified profile subject. There is no normal API argument for another user. A 401 invalidates the existing connection/cursors as in Phase 1.

Only infrastructure sees raw JSON or credentials. Fixed internally constructed GET routes use the same public-origin/DNS pinning/TLS boundary as Phase 1. No links in descriptions, items, attachments, announcements or calendar content cause requests. No application file creation, download, parsing, command execution, configuration mutation or LLM/prompt layer exists. Untrusted labels do not themselves prevent prompt injection: the future host must still apply trusted AGENTS/skills and tool-authorization rules.

## Submission facts

`SubmissionState` records `submitted`, `not_submitted`, or `unknown`. A submission timestamp or Canvas `submitted`/`pending_review` state establishes submission; `unsubmitted` establishes non-submission when there is no submission timestamp. **Graded alone does not establish submission.** A graded item may still have an unknown submission state, such as a manual offline grade or automatic zero. Such future work stays in an ordinary upcoming view with a submission-unknown warning rather than being excluded as already submitted.

Late, missing, excused and graded remain independent nullable facts. Late+submitted and late+graded are preserved. Missing is never inferred from the clock. Known Canvas submission types establish whether submission is required; `on_paper` remains required coursework, `none` is not a submission requirement, absent/unrecognized types stay unknown. A dedicated submission read cannot infer assignment requirements and leaves `required` unknown.

## Dates and workload aggregates

All mapped timestamps must contain an offset and are normalized to aware UTC. Relative windows use `ctx.as_of`, not machine-local time. `days=7` means a rolling 168-hour window, not seven local calendar dates. Callers needing local-calendar/DST boundaries supply explicit offset-aware `start_at` and `end_at`. Windows use **[start, end)**, must be nonempty and at most 90 days. Calendar/announcement APIs have inclusive upstream bounds; application projection excludes rows exactly at the end. Unknown event dates remain marked unknown rather than assigned a guessed date.

`get_upcoming(days=7, courses=(), include_overdue=False, include_submitted=False)` discovers active student courses by default. Explicit courses are bounded and authorized. It scans assignment pages sequentially with `include[]=submission` and `override_assignment_dates=true`, filters the effective current-user `due_at`, deduplicates by course/assignment, then sorts by due instant/course ID/assignment ID. It does not use base `all_dates` or fetch override student lists. Undated work is excluded; unknown dates produce a coverage warning. No separate submission calls occur in this scan.

The scan intentionally does not use a Canvas bucket: a local bounded window may cross Canvas's own bucket definitions, and selecting only one bucket could silently miss relevant overdue or submitted work. This costs assignment-list pages but avoids per-assignment calls and preserves explicit status semantics.

`get_overdue(days=7, courses=())` scans the preceding seven days by default. It includes only past-due, confirmed not-submitted, required, explicitly not-excused and not-graded work. A late accepted submission is not overdue work to submit. Unknown decisive facts are skipped with a warning. `include_overdue=True` on upcoming adds an equally sized lookback (default seven days behind and ahead; at most 45 each). With explicit bounds it uses only those bounds, not unbounded history. `include_submitted=True` affects upcoming rows, not overdue predicates.

General request cost without retries, after the connection's initial profile request:

- Assignment context: **3 GETs** (course, assignment, sequence), **4** if embedded submission is absent and fallback is needed. No rubric/files/grades/module-enumeration calls.
- Workload: **course-discovery pages + one course-authorization GET per course + assignment pages per course**. For five courses fitting one page each: **11 GETs**, plus an initial profile if not already bound. Cost is per course/page, not per assignment.

Normalized course authorization is memoized only inside a single trusted request context and discarded afterward; there is no persistent cache. All branches share the existing 60-second deadline, 100-attempt and 20-page ceilings, with at most 50 courses. Retries stay at the HTTP boundary (at most three attempts), never around the aggregate. A course-local temporary outage or 404 excludes that entire course and adds a safe warning; completed course scans remain useful with explicit incomplete coverage. Zero completed courses raises, unless successful discovery established that no courses exist. Shared deadline/attempt/page exhaustion stops further scanning and can retain completed courses. Authentication, credentials/configuration, all 403/authorization failures, validation, throttling, malformed data, programming failures and content/output safety limits remain fatal. See the [full classification and 5/20-course request table](phase2-resilience.md).

`CourseCoverage.scanned` contains only fully exhausted course scans; `failed` includes failed or budget-unchecked courses. `items.complete=false` and `Result.complete=false` when any course was not completed. Unknown facts and optional metadata warnings can also make `Result.complete=false`. The aggregate has no continuation cursor: the returned items are sorted only among successful courses, not a claim of globally exhaustive chronological coverage. Narrow course selection when a shared budget or the fatal 128-KiB output cap is reached.

## Optional context and warnings

Course and assignment are mandatory. Authentication, authorization, malformed primary/secondary identity and budget failures propagate. Optional submission/module lookups may return useful incomplete context on not-found, throttling, unsupported capability or temporary outage, with fixed component/category warnings. There is no broad exception-to-empty-array fallback.

`Observed` distinguishes known absence from unknown/not-requested. The generic academic result wrapper carries truncation/unavailable/unfetched-reference warnings and bounds the full serialized normalized envelope. Valid missing optional fields are not parsing errors.

Phase 1's reported `Optional metadata warnings: 1` is only a count; the saved output cannot establish which field was absent. Validation has not been loosened. Both smoke commands now accept `--debug` to print fixed component/code pairs (for example `timezone/unavailable` or `term/unavailable`), never a private payload, search term, ID or token. Inspect the corresponding optional metadata capability, not credentials, when diagnosing that count.

## Offline verification and optional live command

```powershell
Set-Location E:\canvas_mcp
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m build --wheel --no-isolation
```

New fixtures in `tests/fixtures/academic.json` are synthetic. Tests mock HTTP streams and DNS, exercise application fakes and verify malicious content does not trigger filesystem/process/configuration actions. Existing Phase 1 tests remain in the same suite.

Run the live command yourself in a separate PowerShell terminal. The existing `.venv` must contain the current package; after source changes, install it with `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` or install the newly built wheel. Do not paste tokens into chat or tracked files.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode Academic -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

Optional `--course-id 123` restricts the sample course search to that known course (upcoming still scans active courses). Otherwise the smoke searches active courses for the first assignment-bearing course, reuses its first page and exercises assignment detail/context and the current user's submission representation. There are at most 50 first-page probes and a 60-second selection deadline; a probe has at most two logical GETs and six attempts after profile binding. No assignment found in fully checked courses is `NOT_TESTED`; failures/deadline-limited coverage is explicitly partial, not proof of absence. Zero successful probes raises.

`--debug` adds fixed step outcomes (`PASS`, `PARTIAL`, `FAIL`, `NOT_TESTED`) and diagnostic codes. `PASS` on a list/availability step means the endpoint was checked; separate metadata warnings can still describe unavailable fields. Output contains counts, API/submission availability, grade availability and workload completeness, not student/course/assignment names, coursework bodies, IDs, URLs, rubric text, comments or scores. Each public call has its own bounded context; every paginated count is capped at 20 pages. The command is a series of bounded checks, not one globally timed aggregate. Same-OS-user process inspection remains outside the local secret guarantee.

## Compatibility and future boundary

This phase refines earlier unimplemented schema sketches (sequence context instead of a module-item scan, attachment metadata instead of file resolution, explicit course grades and bounded workload methods). `CourseworkQueries` remains a future MCP/file facade declaration; the executable services are `ConnectionService` and `AcademicService`. No ADR is replaced. The original profile/course smoke still works unchanged, with an optional safe debug flag added.

Unusual Link formats, private Canvas origins, staff-only contexts and malformed upstream schemas fail closed. Native Canvas behavior and institutional permissions still require an operator's live check. Assignment sequence identity is verified for Assignment module items; quiz/discussion-backed assignment sequence behavior has not been live-verified. A mismatched current asset fails closed, rather than being asserted to represent the assignment. Module-item listing itself preserves all documented types, and sequence neighbors may cross modules. The application uses the shared Canvas ingress ID profile in this personal MVP; a non-Canvas provider would need its own ingress validation profile rather than inheriting numeric IDs. Core `EntityId` records remain opaque.

Phase 3 would cover secure file metadata authorization/resolution, controlled downloads, download-origin/size validation, safe local storage and isolated inspection handoff; none is implemented here.

## Primary API references

- [Assignments and effective user dates](https://developerdocs.instructure.com/services/canvas/resources/assignments)
- [Current-user submissions](https://developerdocs.instructure.com/services/canvas/resources/submissions)
- [Module item sequence](https://developerdocs.instructure.com/services/canvas/resources/modules)
- [Calendar events and context limits](https://developerdocs.instructure.com/services/canvas/resources/calendar_events)
- [Announcements](https://developerdocs.instructure.com/services/canvas/resources/announcements)
- [Enrollments and grades](https://developerdocs.instructure.com/services/canvas/resources/enrollments)
- [Current-user course enrollment summaries](https://developerdocs.instructure.com/services/canvas/resources/courses)
