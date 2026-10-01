# Phase 7.2 — Persistent Canvas State & Change Detection

Local implementation is tested and reviewable. Production acceptance is pending a
durable external PostgreSQL store. On 2026-09-30 the user-confirmed Render `main`
workspace (`tea-d29et0adbo4c73bel8b0`) returned **No Postgres instances found**.
No database, paid resource, plan upgrade or deployment was created.

## Architecture

`Canvas → current-student normalized GradeFact collector → StateRepository
transaction → pure comparison → bounded prepared MCP response → commit`.
Application code depends on the repository port, not SQLite or PostgreSQL.
Only assignment grades are tracked in this phase. Course total APIs were evaluated:
current/final summaries can be hidden, depend on enrollment/aggregation policy, and
do not identify assignment attempts. Existing current course-grade reads remain;
`course_grade_changes` is an empty reserved category, not an implemented total diff.

## Persistence and ownership

SQLite is local/test storage. Remote composition rejects SQLite and requires a
durable external PostgreSQL database via the separate, runtime-secret
`STATE_DATABASE_URL`. Missing, invalid or unreachable configuration disables only
grade-change checks. Ordinary Canvas tools remain independent.

The internal owner is a SHA-256 digest of the server-verified principal,
connection identity, canonical Canvas origin and verified current Canvas user ID.
Remote `personal_canvas` remains stable across chats/devices/OAuth token refresh.
Local session UUIDs are replaced in the state key by `local_canvas`; Canvas user
and origin remain part of that key. Email, token and chat IDs are never keys.
Changing to a different Canvas account intentionally creates an isolated baseline.
The internal owner and Auth0 subject are never projected through MCP.

Three concepts remain separate: live current Canvas facts, stored latest-known
facts, and the dedicated change baseline. Only the change-check use case writes
these snapshots. Planner, assignment and current-grade reads write neither.
Latest and baseline normally match after a successful report; deferred changes
and inherited prior-attempt grades can leave them different.

## Stored data and retention

Schema version 1 stores owner initialization and per-course baseline markers;
two compact rows per current course/assignment (`latest`, `baseline`) with IDs,
known grade attempt, score, display grade, points possible, graded/posted UTC
timestamps, allowlisted workflow and interpreted visibility, first/last seen UTC,
normalized hash and version. Display names are fetched live and not persisted.
Hidden/prior-attempt scores and grades are stripped before storage.

No descriptions, file/OCR/PDF text, attachments, signed URLs, PAT, Auth0 access or
refresh tokens are stored. There is no event history or sequence of full snapshots.
Successful exhaustive course scans replace compact current rows and remove
assignments absent from that course. Failed/inaccessible courses retain their previous
compact snapshot until observed again or explicitly reset. Retention has no age
expiry, so a long Render sleep cannot silently forget a baseline. Operator reset
deletes that owner's rows and returns it to first-run behavior.

## Grade interpretation and first run

The first successful check records historical exposed grades and returns
`baseline_created=true`, empty new/changed lists. UI wording:
“Базовая точка создана. С этого момента я смогу показывать только новые или
изменившиеся оценки.” A course first collected later gets its own historical
baseline, named by `baselined_course_ids`, without calling those grades new.

A new grade is now exposed where the prior baseline had none. An edit preserves
old and new normalized visible score/grade/points. Timestamps, workflow alone,
last-seen and API order do not trigger an event. Numeric and display grades are
both retained; letters, pass/fail, complete/incomplete and visible EX are supported.
No percentage is generated, including for zero/unknown points. EX is preserved
only if Canvas actually exposes that display grade; status-only excused changes
are outside this grade-fact scope and are never synthesized into a score.

Posted visibility is taken from submission facts. Null `posted_at`, explicit
hidden status or an inaccessible assignment strips grade values. Missing posted
visibility with a score is an incomplete course, not an invented visible grade.
`hide_final_grades` hides totals and does not suppress posted assignment grades.
`grade_matches_current_submission=true` is required to associate a grade with
the submission attempt. False or an ungraded workflow with missing match evidence
does not consume the prior-attempt baseline. Missing match on a graded record
keeps attempt provenance unknown. Two proven different grade attempts yield
`new_attempt`; unknown metadata never fabricates repeat-attempt semantics.

Canvas API references: [submission semantics](https://developerdocs.instructure.com/services/canvas/resources/submissions)
and [course total visibility](https://developerdocs.instructure.com/services/canvas/resources/courses).

## Atomic reporting, partial failures and concurrency

PostgreSQL locks the owner row; SQLite uses `BEGIN IMMEDIATE`. The lock precedes
live course collection, so another check cannot collect stale data and commit it
after a newer check. All successful course replacements and initialization commit
in one transaction. Exceptions/cancellation roll back. Failed or incomplete
course pages are discarded, their previous baseline is preserved, and warnings
mark `complete=false`. Discovery must itself finish exhaustively.

At most 100 events are prepared per report, reducing further to fit the exact
128 KiB MCP text-plus-structured wire budget. Unreported events retain their old
entity baseline (a known-ungraded marker when no old row existed); full collected
latest facts can still be stored. `response_limit_reached`, `complete=false`
explains that later fresh checks can report the rest. A report too small to fit
even its envelope fails without advancing anything. Idempotent entity keys and
serialized comparisons prevent duplicate events on identical transport retries.

The prepared response is validated before writes. Commit confirms backend
preparation, not delivery to the ChatGPT UI: a connection lost after commit can
leave the user without that response, and retry will not replay committed events.
This unavoidable delivery boundary is explicit; there is no acknowledgement or
durable event-replay protocol in this phase.

## Performance and availability

One model call, `canvas_get_grade_changes`, scans accessible student courses internally,
including completed courses so later final grades can still be observed.
The existing sequential cross-course policy is reused (concurrency 1), with shared
HTTP retry/page/deadline limits. No per-assignment submission requests, background
poller or scheduler is added. Bounds: 50 courses, 2,000 assignments per course,
5,000 total collected facts and the existing 20-page/100-request/60-second Canvas
ledger. Parameterized `executemany` batches both snapshots per course. Blocking
database work runs in worker threads; a 90-second overall cancellation deadline, 5-second
connect timeout and database lock/statement limits prevent indefinite checks.
Cancellation joins an in-progress driver operation before rollback/close;
driver cleanup can extend the observed timeout until that bounded operation ends.

Safe audit logs contain fixed event classes and counts only. No grades, names,
subjects, owners, DSNs or raw payloads are logged. PostgreSQL driver diagnostics
are suppressed while state operations run, including DEBUG configuration.
Unavailable stores produce `state_store_unavailable`, never “no changes”.
Timing and audit callbacks are best effort: an observability failure cannot hide
a prepared response after its baseline commit or mask a safe state-store error.

## Migrations, deployment and operator reset

Tables are installed only with an explicit operator migration, never during
production requests. The packaged `001.sql` is transactional and versioned;
re-running the migration verifies version 1. Incompatible/missing schemas fail
state operations safely. Normal requests only verify the schema.

Set `STATE_DATABASE_URL` through runtime secret configuration; never put its real
value in Git, tool responses, health output or command-line arguments. Remote
PostgreSQL URLs must request `sslmode=require` or `sslmode=verify-full`. The driver
always enforces `verify-full` and uses the system CA store unless an explicit
trusted `sslrootcert` is supplied. Neon's `channel_binding=require` URL option is
accepted; insecure preferences, duplicate options and disabled remote TLS fail
closed. The pinned binary driver provides libpq 18, which supports the system CA
store. Loopback PostgreSQL with TLS disabled is allowed only
for local tests. For SQLite use an absolute path URL (`sqlite:///E:/.../state.db`
on Windows, `sqlite:////absolute/path/state.db` on Unix).

After an approved durable database exists, run:

```text
python -m canvas_mcp.infrastructure.state.operator migrate --remote
```

Use a dedicated runtime role granted SELECT/INSERT/UPDATE/DELETE on these three
data tables and SELECT on `state_schema`, with schema USAGE, no schema CREATE and
no broader coursework/database permissions. Run migrations with an operator role.
Use a dedicated database/schema; backup/access control belong to the approved
database provider and operator. A runtime role should not own the tables.

Reset is operator controlled, absent from MCP and never automatic:

```text
python -m canvas_mcp.infrastructure.state.operator reset --owner VERIFIED_OWNER_DIGEST --remote
```

Resolve the digest through application `state_owner` using verified server scope,
origin and current Canvas subject. Do not accept a model-provided identity as an
authorization shortcut. Reset clears only this application's snapshots/markers;
it never alters Canvas. Local operators can omit `--remote`.

## Validation and acceptance

Real SQLite and an isolated loopback PostgreSQL 18 cluster run the same contract
suite; no fake PostgreSQL adapter is substituted. It covers float precision,
first/new/changed/identical checks, per-owner isolation, unknown/known attempts,
failed-course recovery, schema errors, cancellation, write/commit rollback,
bounded backlog drainage and collection after the previous concurrent commit.
The actual provider-to-MCP path and the state-error projection are tested too.

An initial full run passed 1,570 cases and found one outdated HTTP annotation
assertion: grade checks write application state, so the new tool correctly has
`readOnlyHint=false` while retaining Canvas read-only behavior. That test was
corrected; the final full-suite result is recorded below after completion.

Ruff check/format, mypy (99 source files), wheel+sdist build, main and isolated
installation, and pip dependency checks passed. All 99 Python sources plus
`001.sql` match the wheel and isolated installed package byte for byte. Both
updated study-overview skills passed the official skill validator.

Local restart proof passed: one fresh Python process established a synthetic
baseline; the isolated PostgreSQL 18 cluster was stopped and restarted; another
fresh Python process returned `baseline_created=false` and no grade events from
the same persistent baseline. This is **not** a Render restart acceptance claim.

Synthetic 5-course/1,000-assignment benchmark, 12 normalized provider calls per
check, no Canvas network latency: SQLite baseline/no-change/50-edits took
39.12/68.45/70.99 ms; PostgreSQL took 151.15/173.31/241.98 ms. These are one local
run, not production latency estimates. `scripts/grade_state_benchmark.py`
reproduces the benchmark and restart probe using synthetic data only.

The requested independent read-only reviewers completed initial review and
re-review. Security review found a High course-total/assignment-visibility mixup
and Medium error-code/write-duration issues; correctness review found High
unknown-attempt provenance and oversized-backlog issues. All were fixed and
covered by regressions. Both reviewers reported no unresolved Critical/High
findings. Their independent focused reruns passed 19 security cases and 35
correctness cases; PostgreSQL was verified by the primary test run, not claimed
as independently repeated by the reviewers. The additional completed-course
coverage caveat was resolved by bounded accessible-course discovery. The timeout
cleanup caveat is documented above.

Original implementation full suite: **1,574 passed, 56 Linux-only tests skipped, 2 dependency
deprecation warnings**, 244.16 seconds. PostgreSQL parametrizations were enabled
against the real isolated PostgreSQL 18 cluster. This includes contract, grade
diff, partial/concurrency, remote HTTP/OAuth, planner/assignment, files/OCR and
stdio/plugin regressions. The skipped Linux descriptor/container tests remain
unverified for this revision because the local Docker engine is unavailable.

Tool counts are now 17 on stdio and 19 on HTTP. The new grade-change tool is
annotated as writing application state, with no destructive/open-world hint;
Canvas access remains read-only. No current-grade analytics or other change
categories were added.

## Production Neon acceptance

The selected persistence provider is **Neon PostgreSQL Free**, provisioned
manually by the user. No card/payment, project creation or database provisioning
has been performed by this agent. Whether the user's signup required a card is
unverified. The existing Render Free service continues hosting MCP, Canvas
access, Auth0, file processing and OCR. Neon holds only the compact external
state described above. It survives Render process/filesystem replacement.

The user supplies the connection secret only through Render's runtime
`STATE_DATABASE_URL`. No value, hostname, username or password belongs in commands,
build arguments, images, logs, health, MCP responses or this document. On
2026-10-01 a key-only inspection of the loaded Render Environment page found no
`STATE_DATABASE_URL` entry. Live production migration/acceptance therefore remain
pending; the deployed service still reports revision `2bd0bee`.

Neon compute may scale to zero. Every state transaction opens and closes its own
connection; there is no persistent connection or oversized pool. Opening retries
at most three times, each with a five-second driver timeout, separated by 250/500
ms. Established transactions and commits are never replayed automatically after
an ambiguous disconnect. A failed transaction gives the safe unavailable error;
a later user check opens a fresh connection. No keepalive traffic is added.

Render Free has no shell, one-off jobs or pre-deploy command. The explicit
migration can run through a temporary operator-selected Docker Command override
on the reviewed image, before its server accepts requests:

```text
sh -c 'python -m canvas_mcp.infrastructure.state.operator migrate --remote && python -m canvas_mcp.infrastructure.state.operator inspect --remote && exec python -m canvas_mcp.mcp_http_server'
```

This command contains no secret. Migration emits only a fixed success event and
schema version; failure emits only `state_store_unavailable`. Operator inspection
emits version, fixed table names, column names and row counts, never grade values,
titles or owner identities. It does not initialize a baseline. Restore the Docker
Command to its normal empty override after migration and the first two checks;
then restart the same reviewed image against the unchanged database. The normal
image command neither migrates nor contacts Canvas or Neon on startup, and
`/health` remains exactly `{"status":"ok"}` independent of database availability.
If the runtime credential lacks migration privileges, migration must use an
operator-approved role through the provider; do not broaden credentials silently.
The bootstrap uses one credential throughout. A role that creates the tables is
their owner and is not the restricted runtime role recommended above. After an
operator-role migration, the operator must arrange ownership/grants and change
the Render secret to the restricted runtime credential before normal-startup
acceptance. If the user instead supplies only the default Neon owner credential,
record that actual privilege limitation; do not claim least privilege. The
production role's privileges remain unverified until configuration exists.

Safe `grade_check_timing` logs split `canvas_collection_ms`,
`state_processing_ms` and `total_ms`. Canvas time includes profile, discovery and
grade collection, including failed calls. State processing is the remaining
application time: connection/retries, lock wait, SQL, comparison, response
validation and commit/close. It is not a database-server-only query measurement.
The total covers the grade-check use case; existing HTTP tool timing additionally
includes transport projection. Actual production figures and cold-start effects
must be recorded after live requests, without attributing all latency to Neon.

Final read-only production-preparation reviews: security reported no new
Critical/High findings and the Medium shared migration/runtime credential caveat
above (13 focused tests passed). Correctness reproduced a High post-commit
callback failure that could consume a grade without returning its report. It was
fixed with best-effort timing/audit and SQLite/PostgreSQL regression coverage;
independent re-review reported no remaining Critical/High findings (14 passed,
5 PostgreSQL cases deselected). Neither review claims live Neon verification.

The Neon preparation patch builds on commit `56df74e`. Its final full regression
run passed **1,593 tests**, with **56 Linux-only skips** and **2 dependency
deprecation warnings**, in **241.29 seconds**. Both SQLite and actual isolated
PostgreSQL 18 parametrizations were enabled, including the post-commit
observability fix. Ruff check/format and mypy passed; wheel and sdist build,
main/isolated installs, byte-for-byte verification of all 99 sources plus
migration, and both dependency checks passed. The installed operator CLI migrated
and inspected the isolated PostgreSQL schema at version 1; remote TLS-disable
configuration emitted only `state_store_unavailable`. These are local results,
not Neon migration or Render acceptance. The Docker engine remains unavailable,
so the skipped Linux sandbox checks require the existing Render image build.

Web/mobile acceptance uses the same verified `personal_canvas` connection and
owner digest across devices, chats and refreshed OAuth tokens. A first check
must establish the baseline with no historical grades reported new; an immediate
second and a post-restart check must preserve it. Native mobile verification
requires the user's phone and an actual reported result. Natural future new or
edited grades add evidence later and are not a deployment prerequisite.

Pending production sequence, only after durable PostgreSQL is configured:

1. Explicit migration, verify least-privilege runtime reads/writes, deploy reviewed code.
2. First live Web check creates a baseline without reporting historical grades new.
3. Restart Render and repeat in a new chat: same baseline, no reset.
4. Native mobile and Web share that verified connection baseline; reverse only if useful.
5. Confirm ordinary reads leave the grade baseline untouched and record minimal regressions.

Never change real Canvas grades for a test. Phase 7.2 is **not production complete**
until these live conditions are verified. Natural new/edited grades can provide
later evidence. No paid provisioning is authorized. Phase 7.3 is not started.
