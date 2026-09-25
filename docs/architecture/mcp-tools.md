# MCP contract history

The implemented Phase 4 local stdio surface and its current configuration are documented in [Local MCP](../local-mcp.md). The material below is the earlier design draft; its proposed tools and shapes are not the current registered surface.

The [MIME confirmation design](../mime-diagnostic-and-learning.md) adds a proposed approval-gated challenge tool. It is not registered or implemented; the local diagnostic CLI is not an MCP override capability.

Phase 2's executable application API is documented in [the academic guide](../academic-read-layer.md). It refines these draft schemas with typed assignment filters, direct sequence context and explicit course-grade reads; none of the tool names below is registered or exposed.

All names here are proposed future tools, not registered handlers. Contract version: draft v1. `CourseworkQueries` declares the application entry points; the Canvas provider is never called directly by MCP. No generic request, execute, arbitrary-URL, arbitrary-user, write or arbitrary-path tool is planned. The legacy skill name `canvas_get_file` was corrected to the metadata-specific name below; there is no compatibility handler to implement at this phase.

## Common ingress contract

Only documented arguments are accepted; reject unknown keys, null where not specified, wrong types (including booleans masquerading as integers), and oversized payloads. Maximum serialized input: 16 KiB. Provider-agnostic `EntityId` is an opaque string internally; Canvas tool IDs use the shared canonical positive decimal codec specified in [security](security.md). The trusted session supplies principal, connection, provider/origin, current subject, request ID, observation time and budget; none is a caller-selectable identity parameter.

`page` means `{limit: integer=25 (1..100), cursor?: string (max 256 ASCII characters)}`. A cursor must be server-issued and bound to the operation, canonical query and page size as well as session identity. No decoded URL or path in it is ever used directly. Lists are bounded and resumable; if a hard cap prevents continuation, return `complete=false` plus a fixed warning and ask the host to narrow the request. Returning an empty array does not prove completeness.

`window` means `{start: RFC3339 timestamp with offset, end: timestamp with offset, timezone: IANA name}`; use `[start,end)` with `start < end`, maximum 90 days. Validate finite numbers, supported enums and bounded array counts. The host turns relative dates into explicit bounds in the user's timezone; do not guess a timezone when a deadline depends on it. Omitted timezone resolves only to a verified profile timezone, otherwise validation requires one. The typed `DateWindow` is the normalized result after this resolution. All limits are operator ceilings and may only be reduced by callers. Search is deliberately local over bounded lists in v1, with coverage disclosed; no raw search string becomes a URL path.

## Tools and use cases

Every success returns `Result<T>`: `data`, `request_id`, `observed_at`, `complete`, and fixed-code `warnings`. Page results additionally contain `items`, `next_cursor`, and collection `complete`. Text fields use `{text, truncated, format: "plain", trust: "untrusted"}`. `Observed<T>` carries `state`, `value` and a `truncated` marker for bounded component data; it is false for unavailable/unsupported/not-requested or known-absent values. Allowed availability combinations and meanings follow the overview. Total serialized results, including wrappers and warnings, are capped at 128 KiB; descriptions are capped at 16,000 characters. Provider clipping must set a text/component marker; application/MCP projection preserves it and marks the affected requested scope incomplete with a fixed `content_truncated` warning. Never silently drop items from a successful complete result.

| Expected future tool | Arguments beyond common envelope | Result data | Application/provider responsibility |
| --- | --- | --- | --- |
| `canvas_get_profile` | none | `Profile`: current user ID, display name, timezone availability | Verify bound subject; omit email, SIS identifiers and roles |
| `canvas_list_courses` | `page`, `active_only=true` | `Page<Course>` | Active enrollments by default; name/code/term only |
| `canvas_get_course` | `course_id` | `Course` | Authorize course before projection |
| `canvas_list_assignments` | `course_id`, `page` | `Page<Assignment>` | Bounded list; descriptions/references may be not requested to keep payload small |
| `canvas_get_assignment` | `course_id`, `assignment_id` | `Assignment` | Title, own effective due date, instructions, points, submission types, classified references |
| `canvas_get_assignment_context` | `course_id`, `assignment_id` | `AssignmentContext` | Aggregate course + assignment + rubric + own submission + attachment metadata + relevant module items |
| `canvas_list_modules` | `course_id`, `page` | `Page<Module>` | Course structure and ordering |
| `canvas_list_module_items` | `course_id`, `module_id`, `page` | `Page<ModuleItem>` | Authorized targets and kinds; external/page targets are metadata only |
| `canvas_list_files` | `course_id`, `page` | `Page<FileMetadata>` | Display metadata without source/download URLs |
| `canvas_get_file_metadata` | `course_id`, `file_id` | `FileMetadata` | Own course access and association verified |
| `canvas_download_file` | `course_id`, `file_id` | `Artifact`; local transport adds `local_artifact` | Reauthorize and download safely; local bridge resolves generated path for isolated inspection |
| `canvas_get_submission` | `course_id`, `assignment_id` | `Submission` | Current user only; no scores, submission body or arbitrary user parameter |
| `canvas_get_grades` | `course_id`, `page` | `Page<Grade>` | Explicit current-user grade query, separate from solving; published/visible scores only |
| `canvas_get_upcoming` | `course_ids=[]`, `mode="upcoming"`, `window?`, `page`, `include_submitted=false`, `include_calendar=false`, `include_announcements=false` | `Workload` | Bounded cross-course aggregate with course coverage |
| `canvas_list_announcements` | `course_id`, `window`, `page` | `Page<Announcement>` | Bounded inert text, never an instruction source |

An empty `course_ids` means bounded discovery of active courses, not all institutions or another user. Explicit courses are deduplicated and individually authorized, max 50. Grade display strings, filenames and title text are also untrusted. Float points/scores must be finite; do not invent scores for unavailable/unpublished grades. Optional provider capabilities fail explicitly rather than fabricating empty objects. `get_rubric`, file-reference extraction and calendar queries are provider methods used by aggregates, not additional invented MCP registrations. `get_rubric` returns an `Observed` tuple so its completeness survives mapping. Bare tuple fields (including ratings and submission types) cannot be silently clipped: over-budget collections cause a safe mapping/budget error, while text clipping on retained entries remains explicitly marked.

## Aggregate semantics and partial results

Assignment context never auto-downloads files, follows web links, or fetches grades. Course and primary assignment are required; their absence/error fails the tool. Rubric absence is known absence, whereas a denied or failed rubric read is unavailable. Each relevant reference is classified, and unresolved required material triggers a warning. Attachment lists and module context are bounded pages. Their v1 aggregate component `next_cursor` is null: on truncation, disclose incomplete coverage and use a fresh scoped `canvas_list_files` or module/item query to inspect further. Do not reuse an adapter/component cursor with a different public tool; each tool's cursors are bound to its own query. Scanning modules to find assignment membership shares the aggregate budget; “not found in scanned subset” is not “not in any module.”

`mode` has these exact meanings:

| Mode | Window/filter | Inclusion |
| --- | --- | --- |
| `upcoming` | Window required; due date in window and >= observation time | Known future due work, excluding confirmed submitted unless requested |
| `overdue` | Window required; due date in window and < observation time | Confirmed not-submitted, required, not excused; reported late/missing flags remain distinct |
| `not_submitted` | Window optional; if supplied, due date in window | Confirmed not-submitted required/not-excused work; without window, include undated and past/future work within bounded active-course scan |
| `undated` | Window must be absent | Assignments with a known absent due date, not unknown dates |

For modes that filter by evidence, unknown status/required/excused/deadline cannot satisfy the predicate: record the skipped-unknown coverage warning so the result does not assert a complete status answer. For ordinary upcoming lists, unknown submission may be included as unknown; don't treat it as submitted or not-submitted. An assignment requiring no Canvas upload can still be legitimate coursework; provider mapping must distinguish “no submission expected” from “required offline work.” Do not infer `required` solely from online submission types. Availability and these semantics require mapping fixtures before implementation.

`include_submitted` only affects upcoming and undated modes; reject it when true with the other modes. `include_calendar` and `include_announcements` require a bounded window and cannot be used with undated mode. Calendar data is course-related only in v1, not the user's entire personal calendar. Assignment-derived calendar events are deduplicated by scoped assignment ID; unrelated events remain events, not deadlines/submissions. No-date assignment rows are ordered after dated rows when included.

Sort discovered assignment rows by effective due time then course/assignment ID for stable ties. A “next deadline” answer requires complete relevant course coverage; partial scans can only identify the nearest deadline **among retrieved work**. `CourseCoverage` records requested/resolved scope, successfully exhausted courses, failures and incomplete discovery; include fixed warnings for unscanned/partially scanned courses. `scanned` means fully scanned, not merely touched. Continuations use a live view: data may change between pages and global order across incomplete scans is not guaranteed. Do not label an early aggregate page a complete chronological ranking.

Primary authentication failure aborts the result and invalidates the affected credential session. A secondary calendar outage may produce a useful partial assignment result. Returned `complete=false` and warnings name the affected authorized course/component and safe category; no raw upstream error. Response-size pressure yields explicit truncation/incomplete coverage and a continuation where feasible, not oversized output or silent data loss.

## Artifact result and read-only hints

The local transport may add `{artifact_id, local_path, sha256, expires_at}` from `LocalArtifactResolver` to the normalized download result. Only a generated, authorized path under the controlled root may be projected. The domain/application result itself remains path-free. The file bytes are not embedded in model output. The trusted host stages the approved artifact into a credential-free isolated inspector; see the exact handoff and release gate in [security](security.md#local-artifact-handoff-and-separate-inspection). Remote transport must disable this path projection and implement authenticated streaming before enabling downloads there.

All LMS capabilities are queries. Download has a local storage side effect, so advertise that fact and use a conservative non-read-only MCP hint for that tool; hints never relax enforcement. No write tool, command placeholder or upload path exists.

## Error contract

Tool errors have a fixed `{code, message, request_id, retryable}` shape, mapped from `domain/errors.py`. A future SDK adapter uses protocol errors for invalid protocol messages and safe tool errors for application failures; it must preserve the distinction without exposing internals. Retryable means the user may try again later, not an instruction for an automatic infinite agent loop. Only allowlisted, bounded retry-delay metadata may be added in implementation.

| Error class/code | Safe meaning | Retry policy |
| --- | --- | --- |
| `AuthenticationError` / `authentication_error` | Canvas connection needs authentication | No automatic retry; operator reconnects |
| `AuthorizationError` / `authorization_error` | Requested operation is not permitted | No retry; hide invisible-object existence as not-found |
| `NotFoundError` / `not_found` | Requested accessible object was not found | No retry |
| `ValidationError` / `validation_error` | Arguments/cursor are invalid | Correct request; never echo unsafe value |
| `RateLimitError` / `rate_limit` | Provider throttled the request | Bounded adapter retry, then later retry allowed |
| `UpstreamUnavailableError` / `upstream_unavailable` | Provider timed out or is temporarily unavailable | Bounded GET retry only |
| `MalformedUpstreamError` / `malformed_upstream` | Provider returned unusable data | No repeated automatic retry |
| `DownloadRejectedError` / `download_rejected` | File/source violates the download policy | No retry without policy/source change |
| `FileTooLargeError` / `file_too_large` | File exceeds the configured limit | No retry of same file |
| `ConfigurationError` / `configuration_error` | Connection configuration needs operator attention | No retry; never expose config values |
| `UnsupportedCapabilityError` / `unsupported_capability` | This deployment/provider cannot perform that read | No fabricated substitute |
| `BudgetExceededError` / `budget_exceeded` | Request exceeded bounded resources | Narrow request or use continuation |
| `ArtifactUnavailableError` / `artifact_unavailable` | Artifact is unavailable, expired or unauthorized | No path/existence details; re-download if authorized |
| `ApplicationError` or unknown exception / `internal_error` | Request could not be completed | No raw traceback/message |

Application classes are category skeletons, not safe-by-construction exceptions. Infrastructure must translate library failures without retaining secret-bearing messages in the public surface; the MCP layer never calls generic exception serialization.
