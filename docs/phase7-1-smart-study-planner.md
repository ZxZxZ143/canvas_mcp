# Phase 7.1 — Smart Study Planner

Implementation and local validation are recorded below. Completion additionally
requires the updated service's real ChatGPT Web and native mobile behavior; this
document does not substitute responsive browser emulation for a phone test.

## Architecture

Canvas → bounded workload aggregation → deterministic metadata features → MCP →
ChatGPT planner skill. The server does not generate plans, rank priorities, call an
LLM, estimate effort, persist a private schedule or poll in the background.
Existing credentials, course authorization, own-submission rules, OAuth scope
`canvas:read`, request budgets and file security remain in force.

The existing upcoming/overdue scans already batch assignment lists and submissions.
They deliberately discard descriptions/rubrics and have a different overdue window.
Obtaining richer context with assignment-context calls for every candidate repeats
course authorization, assignment and module-sequence requests. Therefore one new
aggregate is justified; the other tools retain their existing shapes/semantics.

## Tool/service

`canvas_get_workload(days=7, timezone=null)` is read-only in both stdio and HTTP.
`days` is a strict integer from 1 to 30; `timezone` is a validated IANA zone (at most
64 characters). Omitted timezone uses UTC with `timezone_defaulted_to_utc` and
`complete=false`, rather than consulting or returning a personal profile. The
profile may still be read internally by the existing subject-authentication layer.

`AcademicService.get_workload` reuses the Phase 2 sequential, staged per-course scan.
The assignment-list endpoint's existing response supplies descriptions, submission
types, dates, availability, attachments and rubrics when present. A provider-only
mapping mode retains these fields; it adds no Canvas endpoint or write permission.
An omitted list rubric is unknown, unlike a known absent rubric in a detail response.
No assignment details, module sequences, grades, file downloads or OCR run by default.
Static evidence is reused within a request; there is no new long-lived cache.

Items contain course/assignment identities and names, source UTC due timestamps,
local due timestamps, calendar days/time until due, own submission facts, missing
and graded status, submission types, availability, points, a 600-character bounded
description excerpt, known description/rubric/attachment/link counts, literal task
word signals and warning flags. `effort.state=not_estimated` is explicit. Required
deliverable counts are not guessed. Related material counts and module dependencies
remain unknown/not requested; selected tasks can use assignment context to obtain
verified structure. Text length or word signals are evidence, not complexity scores.
No personal profile, user IDs, submission bodies/comments, grade values or file
capabilities are exposed in this planner output.

## Bounds and dates

- Active course discovery: existing maximum 50 courses, exhausted before claiming
  discovery success. Existing request ledger: 60 seconds, 100 HTTP attempts,
  20 pages, 100 items per internal assignment page, 2,000 seen assignments/course.
- Forward window: from the request's UTC instant through local midnight after
  `days` calendar dates including today; upper endpoint excluded. DST days can have
  23 or 25 hours. Due exactly now is not overdue; lock exactly now is closed.
- Output: at most 40 upcoming, 30 overdue, and 10 undated/unknown-deadline items.
  Past overdue work is not restricted to the forward horizon's length. Submitted,
  excused and non-required work are omitted. A grade alone does not prove completion:
  a missing-work zero stays visible with `graded_without_submission`.
- Unknown submission/requirements/availability and closed work remain visible for
  status checks. Closed work is not presented as an actionable submission promise.
- Items are retained by absolute distance to due time, with IDs for stable ties;
  this is evidence ordering, explicitly not a recommended priority order. Counts
  and the response-byte cap may reduce the result. Every omission sets warnings and
  `complete=false`. Domain and exact MCP wire budgets are enforced; remaining facts
  and coverage survive output trimming. No unlimited history is returned.

Today's action horizon is distinct from evidence lookahead: retrieve at least seven
days for today's priorities, quick wins or time budgets, so tomorrow's quiz and a
large report due in two days can influence today. `days=1` supports due-today facts.
The skill displays natural dates in the known user timezone and preserves source
timestamps internally; no institution timezone is inferred from a course name.

## Priorities and effort

ChatGPT considers unfinished overdue/near-due work, size, verified prerequisites,
submission and availability, uncertainty, course balance, time budgets and user
preferences. It uses Сейчас / Сегодня / Начать сегодня / Можно отложить, explaining
the reasons without scores. A short quiz tomorrow and a large report in two days
can share today's plan. Points alone never determine importance without reliable
grade weighting. User progress and overrides stay in conversation, attributed to
the user when Canvas has not changed.

Allowed approximate effort ranges: 15–30 min, 30–60 min, 1–2 h, 2–4 h, 4 h+, unknown.
Only actual requirements or user input support estimates; each estimate is labeled
and has a short evidence reason. A quiz label alone is unknown, not automatically
two hours. A report is not quick because its deadline is near. Text length and
file count alone cannot establish time, required deliverables or prerequisites.
Estimated completion is distinct from a chosen study block. Unknown work gets an
orientation/start block without a promise to finish. Blocks plus checking/submission
buffer must fit the user's budget. No user availability or exact clock schedule is
invented. Long/uncertain work is split into useful milestones.

## Tool-call efficiency and latency

A representative five-course, ten-candidate workflow formerly needed one upcoming
aggregate plus ten assignment-context calls (11 MCP calls) to retrieve all candidate
descriptions. The new common path uses one workload MCP call and no content reads;
selected uncertain tasks may need one or two targeted contexts. These are architectural
counts, not a measured before/after ChatGPT trace. Cold Canvas network calls under
one-page-per-course assumptions are 12 (subject, course list, five authorization
reads, five assignment pages); the prior additional ten contexts add about 30 reads.
Pagination, retries and authorization state can change those network counts.

The reproducible local `scripts/planner_benchmark.py` performs 50 synthetic runs
over five courses/ten assignments with six mock provider calls per request.
Final aggregation + exact MCP projection median: 2.896 ms; p95: 6.614 ms;
structured JSON: 15,402 bytes. This excludes Canvas/network/model latency and is
not claimed as remote performance. Live measurements are recorded separately below.

## Partial results

Independent course timeout/not-found failures discard that course's staged pages,
retain fully scanned courses and expose failed IDs with `complete=false`. An exhausted
shared request budget retains only previously completed courses. Authentication,
authorization, malformed data and invariant limits are not disguised as course-local
failures; discovery/all-course failure still fails the call.

For an inaccessible course: «Canvas не удалось полностью проверить один из курсов,
поэтому план может быть неполным». Optional metadata loss is distinguished from a
failed course: «Сроки и статус доступны, но часть требований неизвестна». Item/response
limits identify omitted work. Empty partial results never imply no assignments.
No repeated full crawl is triggered by a generic optional metadata warning.

## Synthetic examples

The examples below are planning judgments from the synthetic A–P fixtures, never
student facts. At September 30, 20:00 Asia/Qyzylorda: AI quiz tomorrow at 23:59,
five familiar multiple-choice questions; Physics report October 2 at 23:59 with
dataset/notebook, three plots and six-page discussion; user says at least three
hours remain; Theory homework October 5 at 23:59, four written derivations.

«Что мне делать сегодня?» — Начать Physics report: он объёмный и срок через два
дня; по твоей оценке осталось минимум 3 часа. AI Quiz — завтра 23:59, не сдан;
оценочно 15–30 минут из-за пяти знакомых вопросов, оставь время проверить ответы.
Theory homework можно отложить до следующего блока; оценочно 1–2 часа за четыре
письменные задачи, с неопределённостью из-за сложности выводов.

«У меня два часа» — 75 минут на notebook/первые графики отчёта, 30 минут на quiz,
15 минут на проверку и ручную отправку. Это план блоков, а не гарантия закончить
отчёт; твоя оценка минимум три часа больше сегодняшнего бюджета.

«Распланируй неделю» — Сегодня начать отчёт; quiz завершить не позднее завтра.
Отчёт завершить до пятницы, 2 октября, 23:59, оставив проверку до закрытия.
Homework начать на выходных или раньше при свободном времени, закончить до
понедельника, 5 октября, 23:59. Ежедневные свободные часы не заданы.

«Что могу быстро закрыть?» — Quiz выглядит кандидатом: оценочно 15–30 минут;
для бюджета в 30 минут разумно 25 минут на ответы и пять на проверку, без обещания
успеть наверняка. Project с неизвестными требованиями нельзя назвать быстрым.

## Validation and review record

Synthetic acceptance references cover A–P: tomorrow, mixed deadlines/size, open and
closed overdue, submitted late, partial failure, unknown effort, 30/120-minute budgets,
week, preferences, progress, drill-down, empty work, multiple courses and timezone.
Deterministic regressions exercise real aggregate facts, counts, boundaries, omissions
and Canvas HTTP mapping. Forward model outputs and independent review are recorded
after completion; a fixture inventory test alone is not a model-behavior evaluation.

Actual final checks:

- Full pytest: **1,516 passed, 56 Linux-only skipped, two dependency deprecation
  warnings, 238.75 seconds**. This covers remote HTTP, OAuth/refresh, assignment
  workflow, files/native/OCR and local stdio regressions with synthetic network data.
- Planner unit/HTTP tests: **25 passed** (included in the full suite). Midnight,
  23:59, equality to due time, UTC/+05:00, DST forward/backward, date/input bounds,
  older overdue, closed/submitted/missing-graded work, independent course failure,
  undated limits and exact MCP wire omissions are exercised.
- Mypy: **90 source files**, no errors. Ruff check and format: passed, **232 files**.
- Official skill validator: passed using the already available temporary PyYAML
  dependency. Canonical and portable planner skill/reference files match exactly.
- Wheel + sdist build passed. Project runtime and a separate verification environment
  installed the built wheel without network dependency resolution; `pip check`
  passed. All **90 Python source files** match the installed package byte-for-byte.
  The verification environment reuses the project's dependency installation via
  `site.addsitedir`, which also initializes pywin32; it is not a standalone dependency
  compatibility experiment.
- The first full run had three old 17-tool-count assertions and two launchers using
  the previous 15-tool installed wheel. Those were corrected to HTTP 18/stdio 16
  and the current wheel installed before the final passing run. Earlier sandbox
  pipe/temp-directory failures were addressed by running tests with the required
  Windows facilities; file-containment policies were not weakened.

Independent `study-planner-reviewer` found two High issues: today-only lookahead
excluded tomorrow's work, and graded-only filtering hid missing-work zeros. Both
are fixed and independently rechecked. Two Medium evaluation issues were corrected:
raw synthetic tasks bypassed actual tool windows, and a user duration was embedded
inside Canvas text rather than human conversation evidence.

[Actual frozen model responses and five follow-up replacements](phase7-1-planner-eval-responses.json)
retain the initial failures. The original A/D/I/K calls were retrieval-incompatible;
the final corrected plans passed **16/16** replay checks through production aggregation
(`scripts/planner_evals.py`). The reviewer checked prose expectations and produced a
bounded follow-up for A/D/I/K/P with production-filtered mock evidence. This is one
reviewer-model pass plus a targeted follow-up and deterministic retrieval replay,
not an end-to-end ChatGPT/mobile behavior guarantee. No unresolved implementation
findings were reported.

Live installed stdio, September 30, 2026 at 19:29:27 Asia/Qyzylorda: initialize and
tools/list succeeded with **16 tools**; one `canvas_get_workload(days=7,
timezone="Asia/Qyzylorda")` completed in **27.760 seconds**. All five active courses
were scanned, discovery complete, failed courses none, no warnings, `complete=true`,
zero unfinished items. Window ended October 7 at 00:00 local, exclusive. This proves
the live aggregate/empty-workload path without fabricating a busy study plan; real
nonempty planning and planner-to-assignment handoff remain unverified on the host.
The isolated environment's first smoke attempt failed on missing pywin32 path
initialization before MCP or Canvas access; the corrected attempt above is the only
Canvas workload read in this smoke.

Read-only Render inspection confirmed the existing Free service and disabled auto
deployment. Phase 7.1 has not been deployed. Web/mobile results are **pending**;
existing service behavior must not be attributed to this local implementation.
Native phone behavior requires the existing
ChatGPT mobile connection, with one sequential conversation testing weekly planning,
two-hour replanning and «Начнём с первого — объясни задание». No chats are mass-created.

Phase 7.1 status: **IMPLEMENTED, LOCAL VERIFICATION PASSED; LIVE ACCEPTANCE PENDING**.
It is not COMPLETE: real nonempty plans, time-budget replanning and direct handoff
must still be observed through the updated Web connection, and the native phone
UX must be checked on a real device. Phase 7.2 is outside this change.
