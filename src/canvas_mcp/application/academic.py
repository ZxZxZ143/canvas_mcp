"""Academic use cases depend only on normalized LMS ports, not Canvas HTTP."""

from datetime import datetime
from dataclasses import replace
from typing import Literal

from canvas_mcp.application.academic_results import result
from canvas_mcp.application.contracts import (
    AssignmentContext,
    Result,
    Warning,
    Workload,
    StudyPlanContext,
)
from canvas_mcp.application.planner import planning_window
from canvas_mcp.application.workload import discover_courses, scan
from canvas_mcp.domain.errors import (
    NotFoundError,
    BudgetExceededError,
    RateLimitError,
    UnsupportedCapabilityError,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    Announcement,
    Assignment,
    AssignmentFilter,
    Availability,
    CalendarEvent,
    Course,
    CourseGrade,
    EntityId,
    Module,
    ModuleItem,
    Observed,
    Page,
    PageRequest,
    RequestContext,
    Submission,
)
from canvas_mcp.domain.validation import (
    assignment_filter,
    canvas_id,
    course_ids,
    date_range,
    page_request,
)
from canvas_mcp.ports.lms import LmsAcademicQueries

OPTIONAL_FAILURES = (
    NotFoundError,
    UpstreamUnavailableError,
    UnsupportedCapabilityError,
    RateLimitError,
)


class AcademicService:
    def __init__(self, provider: LmsAcademicQueries) -> None:
        self._provider = provider

    async def get_course(self, ctx: RequestContext, course_id: EntityId) -> Result[Course]:
        canvas_id(course_id)
        return result(await self._provider.get_course(ctx, course_id), ctx, "course")

    async def list_assignments(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest = PageRequest(),
        query: AssignmentFilter = AssignmentFilter(),
    ) -> Result[Page[Assignment]]:
        canvas_id(course_id)
        page_request(page)
        assignment_filter(query)
        return result(
            await self._provider.list_assignments(ctx, course_id, page, query), ctx, "assignments"
        )

    async def get_assignment(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Result[Assignment]:
        canvas_id(course_id)
        canvas_id(assignment_id)
        return result(
            await self._provider.get_assignment(ctx, course_id, assignment_id), ctx, "assignment"
        )

    async def get_submission(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Result[Submission]:
        canvas_id(course_id)
        canvas_id(assignment_id)
        return result(
            await self._provider.get_submission(ctx, course_id, assignment_id), ctx, "submission"
        )

    async def list_modules(
        self, ctx: RequestContext, course_id: EntityId, page: PageRequest = PageRequest()
    ) -> Result[Page[Module]]:
        canvas_id(course_id)
        page_request(page)
        return result(await self._provider.list_modules(ctx, course_id, page), ctx, "modules")

    async def list_module_items(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        module_id: EntityId,
        page: PageRequest = PageRequest(),
    ) -> Result[Page[ModuleItem]]:
        canvas_id(course_id)
        canvas_id(module_id)
        page_request(page)
        return result(
            await self._provider.list_module_items(ctx, course_id, module_id, page),
            ctx,
            "module_items",
        )

    async def get_course_grade(
        self, ctx: RequestContext, course_id: EntityId
    ) -> Result[CourseGrade]:
        canvas_id(course_id)
        return result(await self._provider.get_course_grade(ctx, course_id), ctx, "grades")

    async def get_assignment_context(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Result[AssignmentContext]:
        canvas_id(course_id)
        canvas_id(assignment_id)
        course = await self._provider.get_course(ctx, course_id)
        assignment = await self._provider.get_assignment(ctx, course_id, assignment_id)
        warnings: list[Warning] = []
        submission = assignment.submission
        if submission.value is None:
            try:
                submission = Observed(
                    Availability.AVAILABLE,
                    await self._provider.get_submission(ctx, course_id, assignment_id),
                )
            except OPTIONAL_FAILURES as error:
                submission = Observed(Availability.UNAVAILABLE, None)
                warnings.append(Warning("submission", error.diagnostic_code, course_id))
        try:
            modules = await self._provider.get_assignment_module_context(
                ctx, course_id, assignment_id
            )
        except OPTIONAL_FAILURES as error:
            modules = Observed(Availability.UNAVAILABLE, None)
            warnings.append(Warning("module_context", error.diagnostic_code, course_id))
        return result(
            AssignmentContext(
                course, assignment, assignment.rubric, submission, assignment.attachments, modules
            ),
            ctx,
            "assignment_context",
            tuple(warnings),
        )

    async def get_upcoming(
        self,
        ctx: RequestContext,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        days: int = 7,
        include_overdue: bool = False,
        include_submitted: bool = False,
        courses: tuple[EntityId, ...] = (),
    ) -> Result[Workload]:
        data, warnings = await scan(
            self._provider,
            ctx,
            start_at=start_at,
            end_at=end_at,
            days=days,
            include_overdue=include_overdue,
            include_submitted=include_submitted,
            courses=courses,
        )
        return result(data, ctx, "workload", warnings, complete=data.items.complete)

    async def get_overdue(
        self,
        ctx: RequestContext,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        days: int = 7,
        courses: tuple[EntityId, ...] = (),
    ) -> Result[Workload]:
        data, warnings = await scan(
            self._provider,
            ctx,
            start_at=start_at,
            end_at=end_at,
            days=days,
            overdue=True,
            courses=courses,
        )
        return result(data, ctx, "workload", warnings, complete=data.items.complete)

    async def get_workload(
        self, ctx: RequestContext, *, days: int = 7, timezone: str | None = None
    ) -> Result[StudyPlanContext]:
        zone, start, end = planning_window(ctx.as_of, days, timezone)
        data, warnings = await scan(
            self._provider,
            ctx,
            start_at=start,
            end_at=end,
            planning=True,
        )
        if timezone is None:
            warnings = (*warnings, Warning("timezone", "timezone_defaulted_to_utc"))
        while True:
            try:
                return result(
                    StudyPlanContext(data, zone, start, end, days),
                    ctx,
                    "workload",
                    warnings,
                    complete=data.items.complete,
                )
            except BudgetExceededError:
                if not data.items.items:
                    raise
                warnings = tuple(
                    dict.fromkeys((*warnings, Warning("workload", "response_limit_reached")))
                )
                data = replace(
                    data, items=replace(data.items, items=data.items.items[:-1], complete=False)
                )

    async def list_calendar_events(
        self,
        ctx: RequestContext,
        start_at: datetime,
        end_at: datetime,
        *,
        courses: tuple[EntityId, ...],
        page: PageRequest = PageRequest(),
        event_type: Literal["event", "assignment"] = "event",
    ) -> Result[Page[CalendarEvent]]:
        start, end = date_range(start_at, end_at)
        ids = course_ids(courses, 10)
        page_request(page)
        if not ids or event_type not in ("event", "assignment"):
            raise ValidationError()
        data = await self._provider.list_calendar_events(ctx, ids, start, end, page, event_type)
        # Canvas end-date is inclusive; the public contract is [start, end).
        items = tuple(
            item
            for item in data.items
            if item.starts_at.value is None or start <= item.starts_at.value < end
        )
        return result(Page(items, data.next_cursor, data.complete), ctx, "calendar")

    async def list_announcements(
        self,
        ctx: RequestContext,
        start_at: datetime,
        end_at: datetime,
        *,
        courses: tuple[EntityId, ...] = (),
        page: PageRequest = PageRequest(),
    ) -> Result[Page[Announcement]]:
        start, end = date_range(start_at, end_at)
        ids = course_ids(courses)
        page_request(page)
        if not ids:
            ids = tuple(item.id for item in await discover_courses(self._provider, ctx, ()))
        if not ids:
            return result(Page((), None, True), ctx, "announcements")
        data = await self._provider.list_announcements(ctx, ids, start, end, page)
        items = tuple(
            item
            for item in data.items
            if item.published_at.value is None or start <= item.published_at.value < end
        )
        return result(Page(items, data.next_cursor, data.complete), ctx, "announcements")
