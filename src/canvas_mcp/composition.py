"""Explicit local connection composition. No server, global auth or import-time I/O."""

import time
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TextIO, Literal
from uuid import uuid4

from canvas_mcp.application.connection import ConnectionService
from canvas_mcp.application.files import FileService
from canvas_mcp.infrastructure.files.download import CanvasDownloadClient
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.storage import ManagedStore
from canvas_mcp.application.academic import AcademicService
from canvas_mcp.application.contracts import Result, AssignmentContext, Workload
from canvas_mcp.domain.errors import UnsupportedCapabilityError
from canvas_mcp.domain.models import (
    AccessScope,
    Announcement,
    Assignment,
    AssignmentFilter,
    CalendarEvent,
    CourseGrade,
    EntityId,
    Module,
    ModuleItem,
    Submission,
    BudgetLimits,
    ConnectionId,
    Course,
    Page,
    PageRequest,
    PrincipalId,
    Profile,
    RequestBudget,
    RequestContext,
    FileFilter,
    FileReference,
    FileMetadata,
    DownloadedFile,
    ArtifactId,
)
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient
from canvas_mcp.infrastructure.canvas.provider import CanvasProvider
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource, load_settings
from canvas_mcp.domain.errors import ConfigurationError
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.logging.events import EventLogger
from canvas_mcp.ports.credentials import CredentialSource


@dataclass(repr=False)
class CanvasConnection:
    service: ConnectionService = field(repr=False)
    _scope: AccessScope
    _settings: DeploymentSettings = field(repr=False)
    academic: AcademicService | None = field(default=None, repr=False)
    files: FileService | None = field(default=None, repr=False)

    def _files(self) -> FileService:
        if self.files is None:
            raise UnsupportedCapabilityError()
        return self.files

    def _academic(self) -> AcademicService:
        if self.academic is None:
            raise UnsupportedCapabilityError()
        return self.academic

    def _context(self, *, download: bool = False) -> RequestContext:
        return RequestContext(
            self._scope,
            str(uuid4()),
            datetime.now(timezone.utc),
            RequestBudget(
                BudgetLimits(
                    self._settings.max_aggregate_requests,
                    self._settings.max_pages,
                    self._settings.max_tool_response_bytes,
                ),
                time.monotonic()
                + (
                    self._settings.download_timeout_seconds
                    if download
                    else self._settings.aggregate_timeout_seconds
                ),
            ),
        )

    async def get_profile(self) -> Result[Profile]:
        return await self.service.get_profile(self._context())

    async def list_course_files(
        self,
        course_id: EntityId,
        page: PageRequest = PageRequest(),
        query: FileFilter = FileFilter(),
    ) -> Result[Page[FileMetadata]]:
        return await self._files().list_course_files(self._context(), course_id, page, query)

    async def get_file_metadata(self, reference: FileReference) -> Result[FileMetadata]:
        return await self._files().get_file_metadata(self._context(), reference)

    async def download_file(self, reference: FileReference) -> Result[DownloadedFile]:
        return await self._files().download_file(self._context(download=True), reference)

    async def resolve_download(self, artifact_id: ArtifactId) -> Result[DownloadedFile]:
        return await self._files().resolve_download(self._context(), artifact_id)

    async def cleanup_download(self, artifact_id: ArtifactId) -> None:
        await self._files().cleanup_download(self._context(), artifact_id)

    async def list_courses(
        self,
        page: PageRequest | None = None,
        active_only: bool = True,
    ) -> Result[Page[Course]]:
        return await self.service.list_courses(self._context(), page, active_only)

    async def get_course(self, course_id: EntityId) -> Result[Course]:
        return await self._academic().get_course(self._context(), course_id)

    async def list_assignments(
        self,
        course_id: EntityId,
        page: PageRequest = PageRequest(),
        query: AssignmentFilter = AssignmentFilter(),
    ) -> Result[Page[Assignment]]:
        return await self._academic().list_assignments(self._context(), course_id, page, query)

    async def get_assignment(
        self, course_id: EntityId, assignment_id: EntityId
    ) -> Result[Assignment]:
        return await self._academic().get_assignment(self._context(), course_id, assignment_id)

    async def get_assignment_context(
        self, course_id: EntityId, assignment_id: EntityId
    ) -> Result[AssignmentContext]:
        return await self._academic().get_assignment_context(
            self._context(), course_id, assignment_id
        )

    async def get_submission(
        self, course_id: EntityId, assignment_id: EntityId
    ) -> Result[Submission]:
        return await self._academic().get_submission(self._context(), course_id, assignment_id)

    async def list_modules(
        self, course_id: EntityId, page: PageRequest = PageRequest()
    ) -> Result[Page[Module]]:
        return await self._academic().list_modules(self._context(), course_id, page)

    async def list_module_items(
        self, course_id: EntityId, module_id: EntityId, page: PageRequest = PageRequest()
    ) -> Result[Page[ModuleItem]]:
        return await self._academic().list_module_items(self._context(), course_id, module_id, page)

    async def get_course_grade(self, course_id: EntityId) -> Result[CourseGrade]:
        return await self._academic().get_course_grade(self._context(), course_id)

    async def get_upcoming(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        days: int = 7,
        include_overdue: bool = False,
        include_submitted: bool = False,
        courses: tuple[EntityId, ...] = (),
    ) -> Result[Workload]:
        return await self._academic().get_upcoming(
            self._context(),
            start_at=start_at,
            end_at=end_at,
            days=days,
            include_overdue=include_overdue,
            include_submitted=include_submitted,
            courses=courses,
        )

    async def get_overdue(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        days: int = 7,
        courses: tuple[EntityId, ...] = (),
    ) -> Result[Workload]:
        return await self._academic().get_overdue(
            self._context(), start_at=start_at, end_at=end_at, days=days, courses=courses
        )

    async def list_calendar_events(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        courses: tuple[EntityId, ...],
        page: PageRequest = PageRequest(),
        event_type: Literal["event", "assignment"] = "event",
    ) -> Result[Page[CalendarEvent]]:
        return await self._academic().list_calendar_events(
            self._context(), start_at, end_at, courses=courses, page=page, event_type=event_type
        )

    async def list_announcements(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        courses: tuple[EntityId, ...] = (),
        page: PageRequest = PageRequest(),
    ) -> Result[Page[Announcement]]:
        return await self._academic().list_announcements(
            self._context(), start_at, end_at, courses=courses, page=page
        )


@asynccontextmanager
async def open_canvas_connection(
    environ: Mapping[str, str] | None = None,
    *,
    log_stream: TextIO | None = None,
) -> AsyncIterator[CanvasConnection]:
    settings = load_settings(environ)
    scope = AccessScope(PrincipalId("local"), ConnectionId(str(uuid4())))
    env = os.environ if environ is None else environ
    provider_kind = env.get("CANVAS_CREDENTIAL_PROVIDER", "environment")
    credentials: CredentialSource
    if provider_kind == "windows":
        from canvas_mcp.infrastructure.config.windows_credentials import WindowsCredentialSource

        credentials = WindowsCredentialSource(scope)
    elif provider_kind == "environment":
        credentials = EnvironmentCredentialSource.from_environment(scope, env)
    else:
        raise ConfigurationError()
    async with open_scoped_connection(
        settings, scope, credentials, log_stream=log_stream
    ) as connection:
        yield connection


@asynccontextmanager
async def open_scoped_connection(
    settings: DeploymentSettings,
    scope: AccessScope,
    credentials: CredentialSource,
    *,
    log_stream: TextIO | None = None,
    local_downloads: bool = True,
) -> AsyncIterator[CanvasConnection]:
    """Compose the existing services with an explicit scope and credential port; no I/O."""
    logger = EventLogger(settings.log_level, log_stream)
    if settings.allow_private_origin:
        logger.configured_private_origin_enabled()
    client = CanvasHttpClient(settings, credentials, logger)
    provider = CanvasProvider(client, scope, settings, logger)
    downloads = (
        FileDownloadManager(
            provider,
            CanvasDownloadClient(settings, client, logger),
            ManagedStore(settings, scope),
            settings,
            logger,
        )
        if local_downloads
        else None
    )
    try:
        yield CanvasConnection(
            ConnectionService(provider, max_page_size=settings.max_page_size),
            scope,
            settings,
            AcademicService(provider),
            FileService(provider, downloads if downloads is not None else _UnavailableDownloads()),
        )
    finally:
        try:
            if downloads is not None:
                await downloads.aclose()
        finally:
            await provider.aclose()


class _UnavailableDownloads:
    """Remote reads retain file metadata without constructing local storage."""

    async def download_file(self, ctx: RequestContext, reference: FileReference) -> DownloadedFile:
        raise UnsupportedCapabilityError()

    async def resolve_download(
        self, ctx: RequestContext, artifact_id: ArtifactId
    ) -> DownloadedFile:
        raise UnsupportedCapabilityError()

    async def cleanup_download(self, ctx: RequestContext, artifact_id: ArtifactId) -> None:
        raise UnsupportedCapabilityError()
