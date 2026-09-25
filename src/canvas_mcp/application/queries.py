"""Future application entry points. Protocol only: no orchestrator exists yet.

Implement this one service using LmsQueries and CourseArtifacts. The aggregations
belong here, not in MCP handlers or a provider-specific REST endpoint wrapper.
"""

from typing import Protocol

from canvas_mcp.application.contracts import (
    AssignmentContext,
    AssignmentQuery,
    CourseQuery,
    DateWindow,
    FileQuery,
    ModuleQuery,
    Result,
    UpcomingQuery,
    Workload,
)
from canvas_mcp.domain.models import (
    Announcement,
    Artifact,
    Assignment,
    Course,
    FileMetadata,
    Grade,
    Module,
    ModuleItem,
    Page,
    PageRequest,
    Profile,
    RequestContext,
    Submission,
)


class CourseworkQueries(Protocol):
    async def get_profile(self, ctx: RequestContext) -> Result[Profile]: ...

    async def list_courses(
        self,
        ctx: RequestContext,
        page: PageRequest,
        active_only: bool,
    ) -> Result[Page[Course]]: ...

    async def get_course(self, ctx: RequestContext, query: CourseQuery) -> Result[Course]: ...

    async def list_assignments(
        self,
        ctx: RequestContext,
        query: CourseQuery,
        page: PageRequest,
    ) -> Result[Page[Assignment]]: ...

    async def get_assignment(
        self,
        ctx: RequestContext,
        query: AssignmentQuery,
    ) -> Result[Assignment]: ...

    async def get_assignment_context(
        self,
        ctx: RequestContext,
        query: AssignmentQuery,
    ) -> Result[AssignmentContext]: ...

    async def list_modules(
        self,
        ctx: RequestContext,
        query: CourseQuery,
        page: PageRequest,
    ) -> Result[Page[Module]]: ...

    async def list_module_items(
        self,
        ctx: RequestContext,
        query: ModuleQuery,
        page: PageRequest,
    ) -> Result[Page[ModuleItem]]: ...

    async def list_files(
        self,
        ctx: RequestContext,
        query: CourseQuery,
        page: PageRequest,
    ) -> Result[Page[FileMetadata]]: ...

    async def get_file_metadata(
        self,
        ctx: RequestContext,
        query: FileQuery,
    ) -> Result[FileMetadata]: ...

    async def download_file(
        self,
        ctx: RequestContext,
        query: FileQuery,
    ) -> Result[Artifact]: ...

    async def get_submission(
        self,
        ctx: RequestContext,
        query: AssignmentQuery,
    ) -> Result[Submission]: ...

    async def get_grades(
        self,
        ctx: RequestContext,
        query: CourseQuery,
        page: PageRequest,
    ) -> Result[Page[Grade]]: ...

    async def get_upcoming(
        self,
        ctx: RequestContext,
        query: UpcomingQuery,
    ) -> Result[Workload]: ...

    async def list_announcements(
        self,
        ctx: RequestContext,
        query: CourseQuery,
        window: DateWindow,
        page: PageRequest,
    ) -> Result[Page[Announcement]]: ...
