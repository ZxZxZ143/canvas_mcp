"""Canvas mapping and bounded in-memory continuation capabilities; no raw output."""

import asyncio
import json
import secrets
import time
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TypeVar, Literal

from canvas_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    DownloadPermissionUnavailableError,
    MalformedUpstreamError,
    NotFoundError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    AccessScope,
    Announcement,
    Assignment,
    AssignmentFilter,
    CalendarEvent,
    CourseGrade,
    Course,
    EntityId,
    Page,
    PageRequest,
    Profile,
    RequestContext,
    Module,
    ModuleItem,
    ModuleSequence,
    Observed,
    Submission,
    FileFilter,
    FileReference,
    FileMetadata,
)
from canvas_mcp.domain.validation import (
    canvas_id,
    page_request,
    assignment_filter,
    course_ids,
    date_range,
)
from canvas_mcp.infrastructure.canvas import mapping
from canvas_mcp.infrastructure.canvas import academic_mapping as academic
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.infrastructure.files.capability import (
    DownloadCapability,
    parse_public_url,
    public_url_origin,
)
from canvas_mcp.infrastructure.files.policy import metadata_policy
from canvas_mcp.domain.file_validation import file_filter, file_reference
from canvas_mcp.infrastructure.canvas.client import CanvasHttpClient, JsonPage
from canvas_mcp.infrastructure.canvas.pagination import fingerprint
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.logging.events import Event, EventLogger

T = TypeVar("T")


@dataclass(frozen=True, repr=False)
class _Cursor:
    target: str
    visited: tuple[str, ...]
    limit: int
    binding: str
    expires: float


class CanvasProvider:
    def __init__(
        self,
        client: CanvasHttpClient,
        scope: AccessScope,
        settings: DeploymentSettings,
        logger: EventLogger,
    ) -> None:
        self._client = client
        self._scope = scope
        self._settings = settings
        self._log = logger
        self._subject: EntityId | None = None
        self._invalidated = False
        self._auth_lock = asyncio.Lock()
        self._cursors: dict[str, _Cursor] = {}

    def _authorize(self, ctx: RequestContext) -> None:
        if ctx.scope != self._scope:
            raise AuthorizationError()
        if self._invalidated:
            raise AuthenticationError()

    def _invalidate(self) -> None:
        self._invalidated = True
        self._cursors.clear()

    async def get_profile(self, ctx: RequestContext) -> Profile:
        self._authorize(ctx)
        remaining = ctx.budget.monotonic_deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExceededError()
        try:
            async with asyncio.timeout(remaining):
                async with self._auth_lock:
                    self._authorize(ctx)
                    try:
                        result = mapping.profile(await self._client.get_profile(ctx))
                    except AuthenticationError:
                        self._invalidate()
                        raise
                    if self._subject is not None and result.id != self._subject:
                        self._invalidate()
                        raise AuthenticationError()
                    self._subject = result.id
                    return result
        except TimeoutError:
            pass
        raise BudgetExceededError()

    async def list_courses(
        self,
        ctx: RequestContext,
        page: PageRequest,
        active_only: bool,
    ) -> Page[Course]:
        if type(active_only) is not bool:
            raise ValidationError()
        return await self._page(
            ctx,
            page,
            "courses:" + str(active_only),
            lambda target: self._client.get_courses_page(ctx, page.limit, active_only, target),
            mapping.course,
            "courses",
        )

    async def _ready(self, ctx: RequestContext) -> EntityId:
        self._authorize(ctx)
        if self._subject is None:
            await self.get_profile(ctx)
        assert self._subject is not None
        return self._subject

    async def _call(self, ctx: RequestContext, call: Callable[[], Awaitable[T]]) -> T:
        await self._ready(ctx)
        try:
            result = await call()
        except AuthenticationError:
            self._invalidate()
            raise
        self._authorize(ctx)
        return result

    async def _page(
        self,
        ctx: RequestContext,
        page: PageRequest,
        binding: str,
        fetch: Callable[[str | None], Awaitable[JsonPage]],
        mapper: Callable[[object], T],
        operation: str,
    ) -> Page[T]:
        page_request(page, self._settings.max_page_size)
        await self._ready(ctx)
        now = time.monotonic()
        self._cursors = {key: value for key, value in self._cursors.items() if value.expires > now}
        state: _Cursor | None = None
        if page.cursor is not None:
            if not isinstance(page.cursor, str) or len(page.cursor) > 256:
                raise ValidationError()
            state = self._cursors.get(page.cursor)
            if state is None or state.limit != page.limit or state.binding != binding:
                raise ValidationError()
        visited = state.visited if state else ()
        if len(visited) >= self._settings.max_pages:
            raise BudgetExceededError()
        # Consume before yielding: concurrent resumes cannot both use the same
        # capability. A failed resume requires a fresh scan, not token replay.
        if page.cursor is not None:
            self._cursors.pop(page.cursor, None)
        raw = await self._call(ctx, lambda: fetch(state.target if state else None))
        self._authorize(ctx)
        if not isinstance(raw.payload, list) or len(raw.payload) > page.limit:
            raise MalformedUpstreamError()
        items = tuple(mapper(item) for item in raw.payload)
        current_hash = fingerprint(raw.target)
        if current_hash in visited:
            raise MalformedUpstreamError()
        visited = (*visited, current_hash)
        cursor: str | None = None
        if raw.next_url is not None:
            if fingerprint(raw.next_url) in visited:
                raise MalformedUpstreamError()
            if len(visited) >= self._settings.max_pages:
                raise BudgetExceededError()
            candidate = _Cursor(
                raw.next_url,
                visited,
                page.limit,
                binding,
                now + self._settings.cursor_ttl_seconds,
            )
            size = self._state_size(candidate)
            used = sum(
                self._state_size(value)
                for key, value in self._cursors.items()
                if key != page.cursor
            )
            count = len(self._cursors) - (1 if page.cursor in self._cursors else 0)
            if (
                size > self._settings.max_cursor_state_bytes
                or used + size > self._settings.max_session_cursor_bytes
                or count >= self._settings.max_session_cursors
            ):
                raise BudgetExceededError()
            cursor = secrets.token_urlsafe(32)
            self._cursors[cursor] = candidate
        if page.cursor is not None:
            self._cursors.pop(page.cursor, None)
        self._log.emit(Event.PAGE, ctx.request_id, operation)
        return Page(items, cursor, raw.next_url is None)

    @staticmethod
    def _binding(*values: object) -> str:
        return fingerprint(json.dumps(values, sort_keys=True, default=str, ensure_ascii=True))

    async def get_course(self, ctx: RequestContext, course_id: EntityId) -> Course:
        canvas_id(course_id)
        subject = await self._ready(ctx)
        if course_id not in ctx.courses:
            if len(ctx.courses) >= self._settings.max_courses:
                raise BudgetExceededError()
            raw = await self._call(ctx, lambda: self._client.get_course(ctx, course_id))
            ctx.courses[course_id] = academic.student_course(raw, course_id, subject)
        return ctx.courses[course_id]

    async def list_assignments(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest,
        query: AssignmentFilter = AssignmentFilter(),
    ) -> Page[Assignment]:
        page_request(page)
        assignment_filter(query)
        await self.get_course(ctx, course_id)
        assert self._subject is not None
        return await self._page(
            ctx,
            page,
            self._binding("assignments", course_id, asdict(query)),
            lambda target: self._client.get_assignments_page(
                ctx, course_id, page.limit, query, target
            ),
            lambda raw: academic.assignment(
                raw, course_id, self._subject_id(), self._settings.canvas_origin, detail=False
            ),
            "assignments.list",
        )

    def _subject_id(self) -> EntityId:
        assert self._subject is not None
        return self._subject

    async def get_assignment(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Assignment:
        canvas_id(assignment_id)
        await self.get_course(ctx, course_id)
        raw = await self._call(
            ctx, lambda: self._client.get_assignment(ctx, course_id, assignment_id)
        )
        result = academic.assignment(
            raw, course_id, self._subject_id(), self._settings.canvas_origin
        )
        if result.id != assignment_id:
            raise MalformedUpstreamError()
        return result

    async def get_submission(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Submission:
        canvas_id(assignment_id)
        await self.get_course(ctx, course_id)
        raw = await self._call(
            ctx, lambda: self._client.get_submission(ctx, course_id, assignment_id)
        )
        return academic.submission(raw, course_id, assignment_id, self._subject_id())

    async def list_modules(
        self, ctx: RequestContext, course_id: EntityId, page: PageRequest
    ) -> Page[Module]:
        page_request(page)
        await self.get_course(ctx, course_id)
        return await self._page(
            ctx,
            page,
            self._binding("modules", course_id),
            lambda target: self._client.get_modules_page(ctx, course_id, page.limit, target),
            lambda raw: academic.module(raw, course_id),
            "modules.list",
        )

    async def list_module_items(
        self, ctx: RequestContext, course_id: EntityId, module_id: EntityId, page: PageRequest
    ) -> Page[ModuleItem]:
        canvas_id(module_id)
        page_request(page)
        await self.get_course(ctx, course_id)
        return await self._page(
            ctx,
            page,
            self._binding("module_items", course_id, module_id),
            lambda target: self._client.get_module_items_page(
                ctx, course_id, module_id, page.limit, target
            ),
            lambda raw: academic.module_item(raw, course_id, module_id),
            "modules.items",
        )

    async def get_assignment_module_context(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> Observed[tuple[ModuleSequence, ...]]:
        canvas_id(assignment_id)
        await self.get_course(ctx, course_id)
        raw = await self._call(
            ctx, lambda: self._client.get_assignment_sequence(ctx, course_id, assignment_id)
        )
        return academic.module_sequence(raw, course_id, assignment_id)

    async def list_calendar_events(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        page: PageRequest,
        event_type: Literal["event", "assignment"] = "event",
    ) -> Page[CalendarEvent]:
        ids = course_ids(courses, 10)
        start, end = date_range(start, end)
        page_request(page)
        if not ids or event_type not in ("event", "assignment"):
            raise ValidationError()
        for course_id in ids:
            await self.get_course(ctx, course_id)
        return await self._page(
            ctx,
            page,
            self._binding("calendar", ids, start, end, event_type),
            lambda target: self._client.get_calendar_page(
                ctx, ids, start, end, event_type, page.limit, target
            ),
            lambda raw: academic.calendar_event(raw, ids, event_type),
            "calendar.list",
        )

    async def list_announcements(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        page: PageRequest,
    ) -> Page[Announcement]:
        ids = course_ids(courses)
        start, end = date_range(start, end)
        page_request(page)
        if not ids:
            raise ValidationError()
        for course_id in ids:
            await self.get_course(ctx, course_id)
        return await self._page(
            ctx,
            page,
            self._binding("announcements", ids, start, end),
            lambda target: self._client.get_announcements_page(
                ctx, ids, start, end, page.limit, target
            ),
            lambda raw: academic.announcement(raw, ids),
            "announcements.list",
        )

    async def get_course_grade(self, ctx: RequestContext, course_id: EntityId) -> CourseGrade:
        course = await self.get_course(ctx, course_id)
        if course.grades_hidden is True:
            return academic.course_grade((), course_id, self._subject_id())
        values: list[object] = []
        page = PageRequest(100)
        while True:
            result = await self._page(
                ctx,
                page,
                self._binding("grades", course_id),
                lambda target: self._client.get_enrollments_page(
                    ctx, course_id, self._subject_id(), page.limit, target
                ),
                lambda raw: raw,
                "grades.get",
            )
            values.extend(result.items)
            if len(values) > 100:
                raise BudgetExceededError()
            if result.next_cursor is None:
                break
            page = PageRequest(100, result.next_cursor)
        return academic.course_grade(tuple(values), course_id, self._subject_id())

    async def list_course_files(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        page: PageRequest,
        query: FileFilter = FileFilter(),
    ) -> Page[FileMetadata]:
        page_request(page)
        file_filter(query)
        await self.get_course(ctx, course_id)
        return await self._page(
            ctx,
            page,
            self._binding("files", course_id, asdict(query)),
            lambda target: self._client.get_files_page(ctx, course_id, page.limit, query, target),
            lambda raw: file_metadata(
                raw, FileReference(mapping.entity_id(mapping._object(raw).get("id")), course_id)
            ),
            "files.list",
        )

    async def get_file_metadata(
        self, ctx: RequestContext, reference: FileReference
    ) -> FileMetadata:
        file_reference(reference)
        # Recheck course and source association; numeric IDs are not permission.
        await self.get_course(ctx, reference.course_id)
        if reference.source_kind == "assignment_attachment":
            assert reference.source_id is not None
            assignment = await self.get_assignment(ctx, reference.course_id, reference.source_id)
            if reference.file_id not in {item.id for item in assignment.attachments.value or ()}:
                raise NotFoundError()
        elif reference.source_kind == "submission_attachment":
            assert reference.source_id is not None
            submission = await self.get_submission(ctx, reference.course_id, reference.source_id)
            if reference.file_id not in {item.id for item in submission.attachments.value or ()}:
                raise NotFoundError()
        elif reference.source_kind == "module_file":
            assert reference.source_id is not None and reference.module_id is not None
            module_id, item_id = reference.module_id, reference.source_id
            raw_item = await self._call(
                ctx,
                lambda: self._client.get_module_item(
                    ctx,
                    reference.course_id,
                    module_id,
                    item_id,
                ),
            )
            item = academic.module_item(raw_item, reference.course_id, reference.module_id)
            if (
                item.id != reference.source_id
                or item.kind != "file"
                or item.target_id != reference.file_id
            ):
                raise NotFoundError()
        # Only independently verified own submission attachments may use global
        # metadata: such files belong to the student's user folder, not a course.
        course_id = (
            None if reference.source_kind == "submission_attachment" else reference.course_id
        )
        raw = await self._call(
            ctx, lambda: self._client.get_file(ctx, course_id, reference.file_id)
        )
        return file_metadata(raw, reference)

    async def resolve_file_capability(
        self, ctx: RequestContext, reference: FileReference
    ) -> tuple[FileMetadata, DownloadCapability]:
        """Freshly authorize the source before requesting an ephemeral capability."""
        metadata, payload, secret = await self._public_url_response(ctx, reference)
        return metadata, parse_public_url(
            payload,
            secret,
            (self._settings.canvas_origin, *self._settings.download_origins),
        )

    async def inspect_file_capability_origin(
        self, ctx: RequestContext, reference: FileReference
    ) -> str:
        """Reveal only the validated origin in the explicit local operator CLI."""
        _, payload, secret = await self._public_url_response(ctx, reference)
        return public_url_origin(payload, secret)

    async def _public_url_response(
        self, ctx: RequestContext, reference: FileReference
    ) -> tuple[FileMetadata, object, str]:
        file_reference(reference)
        metadata = await self.get_file_metadata(ctx, reference)
        metadata_policy(metadata, self._settings.max_download_bytes)
        submission_id = None
        if reference.source_kind == "submission_attachment":
            assert reference.source_id is not None
            assignment_id = reference.source_id
            raw = await self._call(
                ctx,
                lambda: self._client.get_submission(ctx, reference.course_id, assignment_id),
            )
            own = academic.submission(raw, reference.course_id, assignment_id, self._subject_id())
            if reference.file_id not in {item.id for item in own.attachments.value or ()}:
                raise NotFoundError()
            submission_id = mapping.entity_id(mapping._object(raw).get("id"))
        try:
            payload = await self._call(
                ctx,
                lambda: self._client.get_file_public_url(ctx, reference.file_id, submission_id),
            )
        except (AuthorizationError, NotFoundError):
            raise DownloadPermissionUnavailableError() from None
        token = await self._client._access_token(ctx, self._settings.request_timeout_seconds)
        return metadata, payload, token.value

    @staticmethod
    def _state_size(state: _Cursor) -> int:
        return len(json.dumps(asdict(state), separators=(",", ":")).encode("ascii")) + 64

    async def aclose(self) -> None:
        self._invalidate()
        await self._client.aclose()
