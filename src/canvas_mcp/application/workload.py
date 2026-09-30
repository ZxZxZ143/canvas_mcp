"""Bounded sequential workload scan; no per-assignment submission requests."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from canvas_mcp.application.contracts import CourseCoverage, Warning, WorkItem, Workload
from canvas_mcp.application.planner import features, MAX_UPCOMING, MAX_OVERDUE, MAX_UNDATED
from canvas_mcp.domain.errors import (
    ApplicationError,
    BudgetExceededError,
    NotFoundError,
    RequestBudgetExceededError,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    Availability,
    Course,
    EntityId,
    Observed,
    Page,
    PageRequest,
    RequestContext,
    SubmissionState,
)
from canvas_mcp.domain.validation import course_ids, date_range, instant
from canvas_mcp.ports.lms import LmsAcademicQueries

# Only independently scoped failures. In particular, 403 can mean system-wide
# access denial or a learner/subject mismatch and must not be hidden.
COURSE_FAILURES = (UpstreamUnavailableError, NotFoundError)


async def discover_courses(
    provider: LmsAcademicQueries, ctx: RequestContext, ids: tuple[EntityId, ...]
) -> tuple[Course, ...]:
    ids = course_ids(ids)
    if ids:
        return tuple([await provider.get_course(ctx, course_id) for course_id in ids])
    found: dict[EntityId, Course] = {}
    page = PageRequest(100)
    while True:
        batch = await provider.list_courses(ctx, page, True)
        found.update((item.id, item) for item in batch.items)
        if len(found) > 50:
            raise BudgetExceededError()
        if batch.next_cursor is None:
            if not batch.complete:
                raise BudgetExceededError()
            break
        page = PageRequest(100, batch.next_cursor)
    return tuple(found[key] for key in sorted(found))


def window(
    ctx: RequestContext,
    start_at: datetime | None,
    end_at: datetime | None,
    days: int,
    overdue: bool,
) -> tuple[datetime, datetime]:
    if type(days) is not int or not 1 <= days <= 90 or (start_at is None) != (end_at is None):
        raise ValidationError()
    now = instant(ctx.as_of)
    if start_at is not None and end_at is not None:
        return date_range(start_at, end_at)
    failed = False
    try:
        start, end = (
            (now - timedelta(days=days), now) if overdue else (now, now + timedelta(days=days))
        )
    except OverflowError:
        failed = True
    if failed:
        raise ValidationError()
    return date_range(start, end)


async def scan(
    provider: LmsAcademicQueries,
    ctx: RequestContext,
    *,
    courses: tuple[EntityId, ...] = (),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    days: int = 7,
    overdue: bool = False,
    include_overdue: bool = False,
    include_submitted: bool = False,
    planning: bool = False,
) -> tuple[Workload, tuple[Warning, ...]]:
    for value in (overdue, include_overdue, include_submitted):
        if type(value) is not bool:
            raise ValidationError()
    if overdue and (include_overdue or include_submitted):
        raise ValidationError()
    start, end = window(ctx, start_at, end_at, days, overdue)
    now = instant(ctx.as_of)
    # Mixed view uses one bounded window. With default 7 days it covers 7 days
    # behind + 7 ahead; explicit windows must already include desired history.
    if include_overdue and start_at is None:
        if days > 45:
            raise ValidationError()
        start = now - timedelta(days=days)
    ids = course_ids(courses)
    # Discovery itself must succeed exhaustively; never silently drop its pages.
    discovered = (
        {} if ids else {course.id: course for course in await discover_courses(provider, ctx, ())}
    )
    ids = ids or tuple(discovered)
    items: dict[tuple[EntityId, EntityId], WorkItem] = {}
    warnings: list[Warning] = []
    scanned: list[EntityId] = []
    failed: list[EntityId] = []
    first_failure: ApplicationError | None = None
    for index, course_id in enumerate(ids):
        try:
            course = discovered.get(course_id)
            if course is None:
                course = await provider.get_course(ctx, course_id)
            course_items, course_warnings = await _scan_course(
                provider,
                ctx,
                course,
                start,
                end,
                now,
                overdue=overdue,
                include_overdue=include_overdue,
                include_submitted=include_submitted,
                planning=planning,
            )
        except COURSE_FAILURES as error:
            # Discard all pages of this course: only an exhausted scan is an
            # authoritative source. No aggregate retry or per-assignment fetch.
            first_failure = first_failure or error.sanitized()
            failed.append(course_id)
            warnings.append(Warning("workload", error.diagnostic_code, course_id))
            continue
        except RequestBudgetExceededError:
            # No work can continue on this shared ledger. Preserve only already
            # completed courses, never convert content/invariant limits to partial.
            if not scanned:
                raise
            failed.extend(ids[index:])
            warnings.append(Warning("workload", "request_budget_exceeded"))
            break
        items.update(course_items)
        warnings.extend(course_warnings)
        scanned.append(course_id)
    if ids and not scanned:
        assert first_failure is not None
        raise first_failure
    ordered = tuple(
        sorted(
            items.values(),
            key=lambda item: (
                item.assignment.due_at.value or now,
                item.course.id,
                item.assignment.id,
            ),
        )
    )
    coverage = CourseCoverage(ids, tuple(scanned), tuple(failed), True)
    if planning:
        kept: list[WorkItem] = []
        counts = {"upcoming": 0, "overdue": 0, "no_due_date": 0, "unknown": 0}
        limits = {
            "upcoming": MAX_UPCOMING,
            "overdue": MAX_OVERDUE,
            "no_due_date": MAX_UNDATED,
            "unknown": MAX_UNDATED,
        }
        # Closest overdue first; returned order is chronological evidence, not priority.
        ordered = tuple(
            sorted(
                ordered,
                key=lambda x: (
                    abs((x.assignment.due_at.value - now).total_seconds())
                    if x.assignment.due_at.value
                    else float("inf"),
                    x.course.id,
                    x.assignment.id,
                ),
            )
        )
        for item in ordered:
            group = "no_due_date" if item.due_state == "unknown" else item.due_state
            if counts[group] >= limits[group]:
                warnings.append(Warning("workload", "item_limit_reached"))
                continue
            counts[group] += 1
            kept.append(item)
        ordered = tuple(kept)
    return Workload(
        Page(ordered, None, not failed),
        Observed(Availability.NOT_REQUESTED, None),
        Observed(Availability.NOT_REQUESTED, None),
        coverage,
    ), tuple(dict.fromkeys(warnings))


async def _scan_course(
    provider: LmsAcademicQueries,
    ctx: RequestContext,
    course: Course,
    start: datetime,
    end: datetime,
    now: datetime,
    *,
    overdue: bool,
    include_overdue: bool,
    include_submitted: bool,
    planning: bool = False,
) -> tuple[dict[tuple[EntityId, EntityId], WorkItem], list[Warning]]:
    items: dict[tuple[EntityId, EntityId], WorkItem] = {}
    warnings: list[Warning] = []
    seen: set[EntityId] = set()
    page = PageRequest(100)
    while True:
        batch = (
            await provider.list_assignments(ctx, course.id, page, workload_context=True)
            if planning
            else await provider.list_assignments(ctx, course.id, page)
        )
        for assignment in batch.items:
            if assignment.id in seen:
                continue
            seen.add(assignment.id)
            if len(seen) > 2000:
                raise BudgetExceededError()
            due = assignment.due_at
            if planning:
                sub = assignment.submission.value
                if (
                    sub
                    and (
                        sub.state is SubmissionState.SUBMITTED
                        or sub.excused is True
                        or sub.required is False
                    )
                ) or assignment.required is False:
                    continue
                if due.value is not None and due.value >= end:
                    continue
                past = due.value is not None and due.value < now
                context_features = features(assignment, now)
                for flag in context_features.warning_flags:
                    warnings.append(Warning("workload", flag, course.id))
                compact = replace(
                    assignment,
                    description=Observed(Availability.NOT_REQUESTED, None),
                    rubric=Observed(Availability.NOT_REQUESTED, None),
                    references=Observed(Availability.NOT_REQUESTED, None),
                    attachments=Observed(Availability.NOT_REQUESTED, None),
                    submission=Observed(Availability.NOT_REQUESTED, None),
                )
                own = assignment.submission
                if own.value is not None:
                    own = replace(
                        own,
                        value=replace(
                            own.value, attachments=Observed(Availability.NOT_REQUESTED, None)
                        ),
                    )
                due_state: Literal["upcoming", "overdue", "no_due_date", "unknown"] = (
                    "unknown"
                    if due.state is not Availability.AVAILABLE
                    else "no_due_date"
                    if due.value is None
                    else "overdue"
                    if past
                    else "upcoming"
                )
                items[(course.id, assignment.id)] = WorkItem(
                    course, compact, own, due_state, context_features
                )
                continue
            if due.state is not Availability.AVAILABLE:
                warnings.append(Warning("workload", "unknown_deadline_skipped", course.id))
                continue
            if due.value is None or not start <= due.value < end:
                continue
            sub = assignment.submission.value
            state = sub.state if sub else SubmissionState.UNKNOWN
            required = sub.required if sub and sub.required is not None else assignment.required
            excused = sub.excused if sub else None
            graded = sub.graded if sub else None
            past = due.value < now
            if past:
                if not (overdue or include_overdue):
                    continue
                if (
                    state is not SubmissionState.NOT_SUBMITTED
                    or required is not True
                    or excused is not False
                    or graded is not False
                ):
                    if (
                        state is SubmissionState.UNKNOWN
                        or required is None
                        or excused is None
                        or graded is None
                    ):
                        warnings.append(Warning("workload", "unknown_status_skipped", course.id))
                    continue
            else:
                if overdue or excused is True or required is False:
                    continue
                if not include_submitted and state is SubmissionState.SUBMITTED:
                    continue
                if state is SubmissionState.UNKNOWN:
                    warnings.append(Warning("workload", "submission_unknown", course.id))
            compact = replace(
                assignment,
                description=Observed(Availability.NOT_REQUESTED, None),
                rubric=Observed(Availability.NOT_REQUESTED, None),
                references=Observed(Availability.NOT_REQUESTED, None),
                attachments=Observed(Availability.NOT_REQUESTED, None),
                submission=Observed(Availability.NOT_REQUESTED, None),
            )
            items[(course.id, assignment.id)] = WorkItem(
                course, compact, assignment.submission, "overdue" if past else "upcoming"
            )
        if batch.next_cursor is None:
            if not batch.complete:
                raise BudgetExceededError()
            break
        page = PageRequest(100, batch.next_cursor)
    return items, warnings
