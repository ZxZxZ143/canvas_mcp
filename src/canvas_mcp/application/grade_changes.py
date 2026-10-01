"""On-demand fresh grade scan and transactional reporting, independent of other reads."""

from collections.abc import Callable, Awaitable
from dataclasses import dataclass, replace
import asyncio
import hashlib
import json
import time
from typing import TypeVar

from canvas_mcp.application.connection import ConnectionService
from canvas_mcp.application.contracts import Result, Warning, CourseCoverage
from canvas_mcp.application.workload import discover_courses, COURSE_FAILURES
from canvas_mcp.domain.errors import (
    BudgetExceededError,
    MalformedUpstreamError,
    RequestBudgetExceededError,
    StateStoreUnavailableError,
)
from canvas_mcp.domain.grade_state import GradeChange, GradeFact, NamedGrade, compare
from canvas_mcp.domain.models import AccessScope, EntityId, PageRequest, RequestContext
from canvas_mcp.ports.lms import LmsAcademicQueries
from canvas_mcp.ports.state import StateRepository

T = TypeVar("T")


def state_owner(scope: AccessScope, origin: str, subject: EntityId, *, local: bool = False) -> str:
    """Stable verified binding, unaffected by tokens, chat IDs or local session UUIDs."""
    return hashlib.sha256(
        json.dumps(
            [
                str(scope.principal_id),
                "local_canvas" if local else str(scope.connection_id),
                origin,
                subject,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class GradeChanges:
    baseline_created: bool
    baselined_courses: tuple[EntityId, ...]
    new_grades: tuple[GradeChange, ...]
    changed_grades: tuple[GradeChange, ...]
    coverage: CourseCoverage


class GradeChangeService:
    def __init__(
        self,
        provider: LmsAcademicQueries,
        repository: StateRepository,
        origin: str,
        *,
        local: bool = False,
        audit: Callable[[str, int, int, int, int], None] | None = None,
        timing: Callable[[float, float, float], None] | None = None,
    ) -> None:
        self.provider, self.repository, self.origin, self.local = (
            provider,
            repository,
            origin,
            local,
        )
        self.audit = audit
        self.timing = timing

    async def check(
        self,
        ctx: RequestContext,
        validate: Callable[[Result[GradeChanges]], None] | None = None,
    ) -> Result[GradeChanges]:
        started = time.monotonic()
        canvas_time = [0.0]
        try:
            async with asyncio.timeout(90):
                response = await self._check(ctx, validate, canvas_time)
        except (StateStoreUnavailableError, TimeoutError):
            self._audit("state_sync_failed", 0, 0, 0, 0)
            raise StateStoreUnavailableError() from None
        total = time.monotonic() - started
        if self.timing is not None:
            try:
                self.timing(canvas_time[0], max(0.0, total - canvas_time[0]), total)
            except Exception:
                # Observability cannot discard a response after its durable commit.
                # Never log callback exception text, which could contain secrets.
                pass
        self._audit(
            "baseline_created" if response.data.baseline_created else "grade_diff_completed",
            len(response.data.coverage.scanned),
            len(response.data.coverage.failed),
            len(response.data.new_grades),
            len(response.data.changed_grades),
        )
        return response

    def _audit(self, event: str, scanned: int, failed: int, new: int, changed: int) -> None:
        if self.audit is not None:
            try:
                self.audit(event, scanned, failed, new, changed)
            except Exception:
                pass

    async def _check(
        self,
        ctx: RequestContext,
        validate: Callable[[Result[GradeChanges]], None] | None,
        canvas_time: list[float],
    ) -> Result[GradeChanges]:
        profile = await self._measure(self.provider.get_profile(ctx), canvas_time)
        owner = state_owner(ctx.scope, self.origin, profile.id, local=self.local)
        # Lock BEFORE collecting; overlapping checks cannot commit stale Canvas batches.
        async with self.repository.transaction(owner) as tx:
            first = not await tx.initialized()
            courses = await self._measure(
                discover_courses(self.provider, ctx, (), active_only=False), canvas_time
            )
            scanned: list[EntityId] = []
            failed: list[EntityId] = []
            baselined: list[EntityId] = []
            warnings: list[Warning] = []
            new: list[GradeChange] = []
            changed: list[GradeChange] = []
            staged: list[tuple[EntityId, tuple[GradeFact, ...], tuple[GradeFact, ...]]] = []
            old_facts: dict[tuple[EntityId, EntityId], GradeFact] = {}
            total = 0
            for index, course in enumerate(courses):
                try:
                    items = await self._measure(self._collect_course(ctx, course.id), canvas_time)
                except (*COURSE_FAILURES, MalformedUpstreamError, BudgetExceededError) as error:
                    failed.append(course.id)
                    warnings.append(Warning("grade_changes", error.diagnostic_code, course.id))
                    if isinstance(error, RequestBudgetExceededError):
                        failed.extend(x.id for x in courses[index + 1 :])
                        break
                    continue
                total += len(items)
                if total > 5000:
                    failed.extend(x.id for x in courses[index:])
                    warnings.append(Warning("grade_changes", "item_limit_reached", course.id))
                    break
                previous = await tx.baseline(course.id)
                prior = {x.assignment_id: x for x in previous or ()}
                old_facts.update(((course.id, x.assignment_id), x) for x in previous or ())
                if previous is None:
                    baselined.append(course.id)
                else:
                    for item in items:
                        event = compare(prior.get(item.fact.assignment_id), item)
                        if event is not None:
                            (changed if event.reason == "grade_changed" else new).append(event)
                current = tuple(x.fact for x in items)
                # A resubmission may retain a grade for the earlier attempt. It is
                # not a grade for the new attempt; preserve the earlier baseline.
                baseline = tuple(
                    prior.get(x.assignment_id, x) if x.visibility == "previous_attempt" else x
                    for x in current
                )
                staged.append((course.id, current, baseline))
                scanned.append(course.id)
            events = sorted(
                new + changed,
                key=lambda x: (x.current.fact.course_id, x.current.fact.assignment_id),
            )
            limit = min(100, len(events))
            while True:
                kept = events[:limit]
                omitted = len(kept) < len(events)
                response_warnings = tuple(warnings) + (
                    (Warning("grade_changes", "response_limit_reached"),) if omitted else ()
                )
                data = GradeChanges(
                    first,
                    tuple(baselined),
                    tuple(x for x in kept if x.reason != "grade_changed"),
                    tuple(x for x in kept if x.reason == "grade_changed"),
                    CourseCoverage(
                        tuple(x.id for x in courses), tuple(scanned), tuple(failed), True
                    ),
                )
                response = Result(
                    data, ctx.request_id, ctx.as_of, not failed and not omitted, response_warnings
                )
                try:
                    ConnectionService._bound_result(response, ctx)
                    # Exact wire budgeting happens before any writes. Omitted events
                    # keep their previous baseline, so another check can report them.
                    if validate is not None:
                        validate(response)
                    break
                except BudgetExceededError:
                    if not limit:
                        raise
                    limit //= 2
            pending = {
                (x.current.fact.course_id, x.current.fact.assignment_id) for x in events[limit:]
            }
            for course_id, facts, baseline in staged:
                baseline = tuple(
                    old_facts.get(
                        (course_id, x.assignment_id),
                        replace(x, score=None, grade=None, visibility="ungraded"),
                    )
                    if (course_id, x.assignment_id) in pending
                    else x
                    for x in baseline
                )
                await tx.replace_course(course_id, facts, ctx.as_of, baseline=baseline)
            if scanned or not courses:
                await tx.mark_checked()
            # A completely failed first check did not create a baseline.
            if first and courses and not scanned:
                raise BudgetExceededError()
            return response

    @staticmethod
    async def _measure(call: Awaitable[T], duration: list[float]) -> T:
        started = time.monotonic()
        try:
            return await call
        finally:
            duration[0] += time.monotonic() - started

    async def _collect_course(
        self, ctx: RequestContext, course_id: EntityId
    ) -> tuple[NamedGrade, ...]:
        found: dict[EntityId, NamedGrade] = {}
        page = PageRequest(100)
        while True:
            batch = await self.provider.list_grade_facts(ctx, course_id, page)
            for item in batch.items:
                key = item.fact.assignment_id
                if key in found and item != found[key]:
                    raise MalformedUpstreamError()
                found[key] = item
            if len(found) > 2000:
                raise BudgetExceededError()
            if batch.next_cursor is None:
                if not batch.complete:
                    raise BudgetExceededError()
                return tuple(found[key] for key in sorted(found))
            page = PageRequest(100, batch.next_cursor)
