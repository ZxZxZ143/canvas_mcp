"""Provider query contract; all methods are abstract declarations, not handlers.

Scope and course/object authorization apply to every method. Provider pagination
is bounded, opaque, scope-bound, and never exposes Canvas pagination URLs.
Unsupported capabilities raise UnsupportedCapabilityError, not empty success.
"""

from datetime import datetime
from typing import Protocol, Literal
from canvas_mcp.domain.grade_state import NamedGrade

from canvas_mcp.domain.models import (
    Announcement,
    Assignment,
    AssignmentFilter,
    CalendarEvent,
    Course,
    CourseGrade,
    EntityId,
    FileMetadata,
    FileFilter,
    FileReference,
    Grade,
    Module,
    ModuleItem,
    Observed,
    Page,
    PageRequest,
    Profile,
    RequestContext,
    RubricCriterion,
    Submission,
    ModuleSequence,
)


class LmsConnectionQueries(Protocol):
    """Implemented connection slice; larger providers extend this same boundary."""

    async def get_profile(self, ctx: RequestContext) -> Profile: ...

    async def list_courses(
        self,
        ctx: RequestContext,
        page: PageRequest,
        active_only: bool,
    ) -> Page[Course]: ...


class LmsAcademicQueries(LmsConnectionQueries, Protocol):
    async def list_grade_facts(
        self, ctx: RequestContext, course_id: EntityId, page: PageRequest
    ) -> Page[NamedGrade]: ...

    async def get_course(self, ctx: RequestContext, course_id: EntityId) -> Course: ...

    async def list_assignments(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest,
        query: AssignmentFilter = AssignmentFilter(),
        *,
        workload_context: bool = False,
    ) -> Page[Assignment]: ...

    async def get_assignment(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        assignment_id: EntityId,
    ) -> Assignment: ...

    async def list_modules(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest,
    ) -> Page[Module]: ...

    async def list_module_items(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        module_id: EntityId,
        page: PageRequest,
    ) -> Page[ModuleItem]: ...

    async def get_submission(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        assignment_id: EntityId,
    ) -> Submission: ...

    async def get_course_grade(self, ctx: RequestContext, course_id: EntityId) -> CourseGrade: ...

    async def get_assignment_module_context(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        assignment_id: EntityId,
    ) -> Observed[tuple[ModuleSequence, ...]]: ...

    async def list_calendar_events(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        page: PageRequest,
        event_type: Literal["event", "assignment"] = "event",
    ) -> Page[CalendarEvent]: ...

    async def list_announcements(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        page: PageRequest,
    ) -> Page[Announcement]: ...


class LmsFileQueries(Protocol):
    async def list_course_files(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest,
        query: FileFilter,
    ) -> Page[FileMetadata]: ...

    async def get_file_metadata(
        self, ctx: RequestContext, reference: FileReference
    ) -> FileMetadata: ...


class LmsQueries(LmsAcademicQueries, Protocol):
    """Future file/individual-grade declarations; not implemented in Phase 2."""

    async def get_rubric(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Observed[tuple[RubricCriterion, ...]]: ...

    async def list_assignment_files(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId, page: PageRequest
    ) -> Page[FileMetadata]: ...

    async def list_files(
        self, ctx: RequestContext, course_id: EntityId, page: PageRequest
    ) -> Page[FileMetadata]: ...

    async def get_file_metadata(
        self, ctx: RequestContext, course_id: EntityId, file_id: EntityId
    ) -> FileMetadata: ...

    async def get_grades(
        self, ctx: RequestContext, course_id: EntityId, page: PageRequest
    ) -> Page[Grade]: ...
