"""Small GET-only Canvas client. Raw JSON and pagination targets stay here/adapter."""

import asyncio
import json
import math
import random
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpcore

from canvas_mcp.domain.errors import (
    ApplicationError,
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    ConfigurationError,
    MalformedUpstreamError,
    NotFoundError,
    RateLimitError,
    RequestBudgetExceededError,
    UpstreamReason,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import AssignmentFilter, EntityId, RequestContext, FileFilter
from canvas_mcp.domain.file_validation import file_filter
from canvas_mcp.domain.validation import assignment_filter, canvas_id, course_ids, date_range
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.canvas.pagination import next_target, validate_target
from canvas_mcp.infrastructure.canvas.text import inert_text
from canvas_mcp.infrastructure.config.origin import normalize_origin, normalize_trusted_private_ips
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.logging.events import (
    Event,
    EventLogger,
    SENSITIVE_HTTP,
    protect_http_logging,
)
from canvas_mcp.ports.credentials import AccessToken, CredentialSource

PROFILE_PATH = "/api/v1/users/self"
COURSES_PATH = "/api/v1/courses"


def operation_for_path(path: str) -> str:
    """Project an already-allowlisted route to a fixed label; never return IDs."""
    if path == PROFILE_PATH:
        return "profile"
    if path == COURSES_PATH:
        return "courses"
    if path == "/api/v1/calendar_events":
        return "calendar.list"
    if path == "/api/v1/announcements":
        return "announcements.list"
    if re.fullmatch(r"/api/v1/files/[0-9]+", path):
        return "files.get"
    if re.fullmatch(r"/api/v1/files/[0-9]+/public_url", path):
        return "files.public_url"
    for suffix, label in (
        (r"/assignments/[0-9]+/submissions/self", "submission.get"),
        (r"/assignments/[0-9]+", "assignments.get"),
        (r"/assignments", "assignments.list"),
        (r"/modules/[0-9]+/items", "modules.items"),
        (r"/modules/[0-9]+/items/[0-9]+", "modules.item"),
        (r"/files", "files.list"),
        (r"/files/[0-9]+", "files.get"),
        (r"/modules", "modules.list"),
        (r"/module_item_sequence", "module_sequence.get"),
        (r"/enrollments", "grades.get"),
        (r"", "courses.get"),
    ):
        if re.fullmatch(r"/api/v1/courses/[0-9]+" + suffix, path):
            return label
    raise ValidationError()


@dataclass(repr=False)
class JsonPage:
    payload: object = field(repr=False)
    next_url: str | None = field(repr=False)
    target: str = field(repr=False)


def _reject_constant(value: str) -> None:
    raise ValueError()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _check_data(value: object, secret: str) -> None:
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 32:
            raise MalformedUpstreamError()
        if isinstance(node, str):
            plain = inert_text(node)
            # Preserve readable block boundaries in display, but reject reflected
            # secrets even if hostile HTML divides them across those boundaries.
            if (
                secret in node
                or secret in plain
                or secret in plain.replace("\n", "").replace("\t", "")
            ):
                raise MalformedUpstreamError()
        if type(node) is int and secret in str(node):
            raise MalformedUpstreamError()
        if isinstance(node, dict):
            stack.extend((key, depth + 1) for key in node)
            stack.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)
        elif isinstance(node, float) and not math.isfinite(node):
            raise MalformedUpstreamError()


class CanvasHttpClient:
    def __init__(
        self,
        settings: DeploymentSettings,
        credentials: CredentialSource,
        logger: EventLogger,
        *,
        _pool: httpcore.AsyncConnectionPool | None = None,
        _gate: asyncio.Semaphore | None = None,
    ) -> None:
        self._origin = normalize_origin(settings.canvas_origin)
        trusted_private_ips = normalize_trusted_private_ips(settings.trusted_private_ips)
        if (
            type(settings.allow_private_origin) is not bool
            or settings.allow_private_origin
            and not trusted_private_ips
            or not 1 <= settings.max_get_attempts <= 3
            or not 1 <= settings.max_pages <= 20
            or not 1 <= settings.max_concurrency <= 4
            or not 0 < settings.request_timeout_seconds <= 60
            or not 1 <= settings.max_api_response_bytes <= 2_097_152
        ):
            raise ConfigurationError()
        self._settings = settings
        self._credentials = credentials
        self._log = logger
        self._gate = _gate or asyncio.Semaphore(settings.max_concurrency)
        protect_http_logging()
        self._pool = _pool or httpcore.AsyncConnectionPool(
            network_backend=PublicOriginBackend(
                self._origin, trusted_private_ips=trusted_private_ips
            ),
            max_connections=settings.max_concurrency,
            max_keepalive_connections=settings.max_concurrency,
            retries=0,
        )
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        guard = SENSITIVE_HTTP.set(True)
        try:
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                await self._pool.aclose()
        except Exception:  # noqa: BLE001 - close failures must not expose transport messages
            failed = True
        finally:
            SENSITIVE_HTTP.reset(guard)
        if failed:
            raise UpstreamUnavailableError()

    async def get_profile(self, ctx: RequestContext) -> object:
        return (await self._get(ctx, PROFILE_PATH, ())).payload

    async def _access_token(self, ctx: RequestContext, timeout: float) -> AccessToken:
        error: ApplicationError
        try:
            async with asyncio.timeout(timeout):
                return await self._credentials.get_access_token(ctx.scope)
        except (AuthenticationError, AuthorizationError, ConfigurationError) as exc:
            error = exc.sanitized()
        except Exception:
            # A failing credential source is never a recoverable Canvas outage,
            # even if its exception happens to be an OSError or a timeout.
            error = ConfigurationError()
        raise error

    async def get_courses_page(
        self,
        ctx: RequestContext,
        limit: int,
        active_only: bool,
        next_url: str | None = None,
    ) -> JsonPage:
        if type(limit) is not int or not 1 <= limit <= self._settings.max_page_size:
            raise ValidationError()
        if type(active_only) is not bool:
            raise ValidationError()
        parameters: tuple[tuple[str, str], ...] = (
            ("enrollment_type", "student"),
            ("include[]", "term"),
            ("per_page", str(limit)),
        )
        if active_only:
            parameters += (("enrollment_state", "active"),)
        return await self._page_request(ctx, COURSES_PATH, parameters, next_url)

    async def _page_request(
        self,
        ctx: RequestContext,
        path: str,
        parameters: tuple[tuple[str, str], ...],
        next_url: str | None = None,
    ) -> JsonPage:
        if ctx.budget.pages_used >= min(ctx.budget.limits.max_pages, self._settings.max_pages):
            raise RequestBudgetExceededError()
        ctx.budget.pages_used += 1
        return await self._get(ctx, path, parameters, next_url)

    def _course_path(self, course_id: EntityId) -> str:
        return COURSES_PATH + "/" + canvas_id(course_id)

    def _limit(self, limit: int) -> tuple[str, str]:
        if type(limit) is not int or not 1 <= limit <= self._settings.max_page_size:
            raise ValidationError()
        return "per_page", str(limit)

    async def get_course(self, ctx: RequestContext, course_id: EntityId) -> object:
        return (
            await self._get(ctx, self._course_path(course_id), (("include[]", "term"),))
        ).payload

    async def get_files_page(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        limit: int,
        query: FileFilter,
        next_url: str | None = None,
    ) -> JsonPage:
        file_filter(query)
        params: tuple[tuple[str, str], ...] = (
            self._limit(limit),
            ("sort", query.sort),
            ("order", query.order),
        )
        if query.search_term is not None:
            params += (("search_term", query.search_term),)
        if query.content_type is not None:
            params += (("content_types[]", query.content_type),)
        return await self._page_request(
            ctx, self._course_path(course_id) + "/files", params, next_url
        )

    async def get_file(
        self, ctx: RequestContext, course_id: EntityId | None, file_id: EntityId
    ) -> object:
        prefix = self._course_path(course_id) if course_id is not None else "/api/v1"
        return (await self._get(ctx, prefix + "/files/" + canvas_id(file_id), ())).payload

    async def get_file_public_url(
        self, ctx: RequestContext, file_id: EntityId, submission_id: EntityId | None = None
    ) -> object:
        params = (("submission_id", canvas_id(submission_id)),) if submission_id is not None else ()
        return (
            await self._get(ctx, "/api/v1/files/" + canvas_id(file_id) + "/public_url", params)
        ).payload

    async def get_module_item(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        module_id: EntityId,
        item_id: EntityId,
    ) -> object:
        path = (
            self._course_path(course_id)
            + "/modules/"
            + canvas_id(module_id)
            + "/items/"
            + canvas_id(item_id)
        )
        return (await self._get(ctx, path, ())).payload

    async def get_assignments_page(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        limit: int,
        query: AssignmentFilter,
        next_url: str | None = None,
    ) -> JsonPage:
        assignment_filter(query)
        params: tuple[tuple[str, str], ...] = (
            self._limit(limit),
            ("include[]", "submission"),
            ("override_assignment_dates", "true"),
            ("order_by", query.order_by),
        )
        if query.bucket is not None:
            params += (("bucket", query.bucket),)
        if query.search_term is not None:
            params += (("search_term", query.search_term),)
        return await self._page_request(
            ctx, self._course_path(course_id) + "/assignments", params, next_url
        )

    async def get_assignment(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> object:
        return (
            await self._get(
                ctx,
                self._course_path(course_id) + "/assignments/" + canvas_id(assignment_id),
                (("include[]", "submission"), ("override_assignment_dates", "true")),
            )
        ).payload

    async def get_submission(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> object:
        return (
            await self._get(
                ctx,
                self._course_path(course_id)
                + "/assignments/"
                + canvas_id(assignment_id)
                + "/submissions/self",
                (),
            )
        ).payload

    async def get_modules_page(
        self, ctx: RequestContext, course_id: EntityId, limit: int, next_url: str | None = None
    ) -> JsonPage:
        return await self._page_request(
            ctx, self._course_path(course_id) + "/modules", (self._limit(limit),), next_url
        )

    async def get_module_items_page(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        module_id: EntityId,
        limit: int,
        next_url: str | None = None,
    ) -> JsonPage:
        return await self._page_request(
            ctx,
            self._course_path(course_id) + "/modules/" + canvas_id(module_id) + "/items",
            (self._limit(limit),),
            next_url,
        )

    async def get_assignment_sequence(
        self, ctx: RequestContext, course_id: EntityId, assignment_id: EntityId
    ) -> object:
        return (
            await self._get(
                ctx,
                self._course_path(course_id) + "/module_item_sequence",
                (("asset_type", "Assignment"), ("asset_id", canvas_id(assignment_id))),
            )
        ).payload

    async def get_enrollments_page(
        self,
        ctx: RequestContext,
        course_id: EntityId,
        subject: EntityId,
        limit: int,
        next_url: str | None = None,
    ) -> JsonPage:
        return await self._page_request(
            ctx,
            self._course_path(course_id) + "/enrollments",
            (
                self._limit(limit),
                ("user_id", canvas_id(subject)),
                ("type[]", "StudentEnrollment"),
                ("include[]", "current_points"),
            ),
            next_url,
        )

    async def get_calendar_page(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        event_type: str,
        limit: int,
        next_url: str | None = None,
    ) -> JsonPage:
        ids = course_ids(courses, 10)
        if not ids or event_type not in ("event", "assignment"):
            raise ValidationError()
        start, end = date_range(start, end)
        params: tuple[tuple[str, str], ...] = (
            self._limit(limit),
            ("type", event_type),
            ("start_date", start.isoformat()),
            ("end_date", end.isoformat()),
            ("excludes[]", "child_events"),
            ("excludes[]", "assignment"),
        )
        params += tuple(("context_codes[]", "course_" + value) for value in ids)
        return await self._page_request(ctx, "/api/v1/calendar_events", params, next_url)

    async def get_announcements_page(
        self,
        ctx: RequestContext,
        courses: tuple[EntityId, ...],
        start: datetime,
        end: datetime,
        limit: int,
        next_url: str | None = None,
    ) -> JsonPage:
        ids = course_ids(courses)
        if not ids:
            raise ValidationError()
        start, end = date_range(start, end)
        params: tuple[tuple[str, str], ...] = (
            self._limit(limit),
            ("start_date", start.isoformat()),
            ("end_date", end.isoformat()),
            ("active_only", "true"),
        )
        params += tuple(("context_codes[]", "course_" + value) for value in ids)
        return await self._page_request(ctx, "/api/v1/announcements", params, next_url)

    async def _get(
        self,
        ctx: RequestContext,
        path: str,
        parameters: tuple[tuple[str, str], ...],
        continuation: str | None = None,
    ) -> JsonPage:
        academic = re.fullmatch(
            r"/api/v1/(?:files/[1-9][0-9]{0,18}(?:/public_url)?|calendar_events|announcements|courses/[1-9][0-9]{0,18}(?:/files(?:/[1-9][0-9]{0,18})?|/assignments(?:/[1-9][0-9]{0,18}(?:/submissions/self)?)?|/modules(?:/[1-9][0-9]{0,18}/items(?:/[1-9][0-9]{0,18})?)?|/module_item_sequence|/enrollments)?)",
            path,
        )
        if self._closed or (path not in (PROFILE_PATH, COURSES_PATH) and academic is None):
            raise ValidationError()
        target = self._origin + path
        if parameters:
            target += "?" + urlencode(sorted(parameters))
        if continuation is not None:
            target = validate_target(continuation, self._origin, path, parameters)
        operation = operation_for_path(path)
        for attempt in range(1, self._settings.max_get_attempts + 1):
            remaining = ctx.budget.monotonic_deadline - time.monotonic()
            if remaining <= 0 or ctx.budget.http_attempts_used >= min(
                ctx.budget.limits.max_http_attempts,
                self._settings.max_aggregate_requests,
            ):
                raise RequestBudgetExceededError()
            ctx.budget.http_attempts_used += 1
            timeout = min(remaining, self._settings.request_timeout_seconds)
            error: ApplicationError | None = None
            retry_after = 0.0
            guard = SENSITIVE_HTTP.set(True)
            try:
                self._log.emit(Event.STARTED, ctx.request_id, operation, attempt=attempt)
                token = await self._access_token(ctx, timeout)
                remaining = ctx.budget.monotonic_deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestBudgetExceededError()
                timeout = min(remaining, self._settings.request_timeout_seconds)
                async with asyncio.timeout(timeout), self._gate:
                    async with self._pool.stream(
                        "GET",
                        target,
                        headers={
                            "Authorization": "Bearer " + token.value,
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                        extensions={
                            "timeout": {
                                key: timeout for key in ("connect", "read", "write", "pool")
                            }
                        },
                    ) as response:
                        error = self._status_error(response.status)
                        if error is None:
                            headers = list(response.headers)
                            media = self._header(headers, b"content-type").split(b";", 1)[0].lower()
                            encoding = self._header(headers, b"content-encoding").lower()
                            if media != b"application/json" or encoding not in (b"", b"identity"):
                                raise MalformedUpstreamError()
                            body = bytearray()
                            async for chunk in response.aiter_stream():
                                if len(body) + len(chunk) > self._settings.max_api_response_bytes:
                                    raise BudgetExceededError()
                                body.extend(chunk)
                            payload = json.loads(
                                body,
                                parse_constant=_reject_constant,
                                object_pairs_hook=_unique_object,
                            )
                            _check_data(payload, token.value)
                            following = next_target(headers, self._origin, path, parameters, target)
                            if path == PROFILE_PATH and following is not None:
                                raise MalformedUpstreamError()
                            if following and token.value in following:
                                raise MalformedUpstreamError()
                            self._log.emit(
                                Event.COMPLETED, ctx.request_id, operation, attempt=attempt
                            )
                            return JsonPage(payload, following, target)
                        if isinstance(error, RateLimitError):
                            raw_delay = self._header(list(response.headers), b"retry-after")
                            try:
                                delay = float(raw_delay)
                                retry_after = (
                                    min(delay, 5.0) if math.isfinite(delay) and delay > 0 else 0
                                )
                            except ValueError:
                                pass
            except ApplicationError as exc:
                error = exc.sanitized()
            except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError):
                error = MalformedUpstreamError()
            except (TimeoutError, httpcore.TimeoutException):
                error = UpstreamUnavailableError(reason=UpstreamReason.TIMEOUT)
            except (httpcore.NetworkError, OSError):
                error = UpstreamUnavailableError(reason=UpstreamReason.CONNECTION)
            except httpcore.ProtocolError:
                error = MalformedUpstreamError()
            except Exception:  # noqa: BLE001 - the security boundary must not leak unknown failures
                error = ApplicationError()
            finally:
                SENSITIVE_HTTP.reset(guard)
            # Raise outside the except block: no secret-bearing upstream context retained.
            assert error is not None
            retryable = isinstance(error, (RateLimitError, UpstreamUnavailableError))
            error.retry_exhausted = retryable and attempt == self._settings.max_get_attempts
            delay = max(retry_after, min(0.1 * 2 ** (attempt - 1) + random.random() * 0.1, 1.0))
            budget_stops_retry = (
                delay >= ctx.budget.monotonic_deadline - time.monotonic()
                or ctx.budget.http_attempts_used
                >= min(ctx.budget.limits.max_http_attempts, self._settings.max_aggregate_requests)
            )
            self._log.emit(
                Event.FAILED,
                ctx.request_id,
                operation,
                attempt=attempt,
                reason=error.diagnostic_code,
                retry_exhausted=error.retry_exhausted,
                retry_stop=(
                    "not_retryable"
                    if not retryable
                    else "attempt_limit"
                    if error.retry_exhausted
                    else "request_budget"
                    if budget_stops_retry
                    else "retry_scheduled"
                ),
            )
            if not retryable or error.retry_exhausted or budget_stops_retry:
                raise error
            await asyncio.sleep(delay)
        raise UpstreamUnavailableError()

    @staticmethod
    def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes:
        values = [value.strip() for key, value in headers if key.lower() == name]
        if len(values) > 1:
            raise MalformedUpstreamError()
        return values[0] if values else b""

    @staticmethod
    def _status_error(status: int) -> ApplicationError | None:
        if status == 200:
            return None
        if status == 401:
            return AuthenticationError()
        if status == 403:
            return AuthorizationError()
        if status == 404:
            return NotFoundError()
        if status == 429:
            return RateLimitError()
        if 500 <= status <= 599:
            return UpstreamUnavailableError(reason=UpstreamReason.HTTP, http_status=status)
        # Redirects are never followed, even on the configured origin.
        return MalformedUpstreamError()
