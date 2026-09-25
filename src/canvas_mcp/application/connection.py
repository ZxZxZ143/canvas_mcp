"""The implemented application slice: normalized profile and course queries."""

import json
from dataclasses import asdict
from typing import TypeVar

from canvas_mcp.application.contracts import Result, Warning
from canvas_mcp.domain.errors import BudgetExceededError, ValidationError
from canvas_mcp.domain.models import (
    Availability,
    Course,
    Page,
    PageRequest,
    Profile,
    RequestContext,
)
from canvas_mcp.ports.lms import LmsConnectionQueries

T = TypeVar("T")


class ConnectionService:
    def __init__(self, provider: LmsConnectionQueries, *, max_page_size: int = 100) -> None:
        self._provider = provider
        self._max_page_size = max_page_size

    async def get_profile(self, ctx: RequestContext) -> Result[Profile]:
        profile = await self._provider.get_profile(ctx)
        warnings: list[Warning] = []
        if profile.display_name.truncated:
            warnings.append(Warning("profile", "content_truncated"))
        if profile.timezone.state is Availability.UNAVAILABLE:
            warnings.append(Warning("timezone", "unavailable"))
        result = Result(profile, ctx.request_id, ctx.as_of, not warnings, tuple(warnings))
        self._bound_result(result, ctx)
        return result

    async def list_courses(
        self,
        ctx: RequestContext,
        page: PageRequest | None = None,
        active_only: bool = True,
    ) -> Result[Page[Course]]:
        if page is None:
            page = PageRequest()
        if (
            not isinstance(page, PageRequest)
            or type(page.limit) is not int
            or not 1 <= page.limit <= self._max_page_size
            or type(active_only) is not bool
            or (
                page.cursor is not None
                and (not isinstance(page.cursor, str) or not 1 <= len(page.cursor) <= 256)
            )
        ):
            raise ValidationError()
        courses = await self._provider.list_courses(ctx, page, active_only)
        warnings: list[Warning] = []
        for course in courses.items:
            if (
                course.name.truncated
                or course.code.truncated
                or (course.term.value is not None and course.term.value.truncated)
            ):
                warnings.append(Warning("course", "content_truncated", course.id))
            if course.term.state is Availability.UNAVAILABLE:
                warnings.append(Warning("term", "unavailable", course.id))
        result = Result(
            courses, ctx.request_id, ctx.as_of, courses.complete and not warnings, tuple(warnings)
        )
        self._bound_result(result, ctx)
        return result

    @staticmethod
    def _bound_result(result: Result[T], ctx: RequestContext) -> None:
        encoded = json.dumps(asdict(result), default=str, ensure_ascii=True).encode("utf-8")
        if len(encoded) > ctx.budget.limits.max_response_bytes:
            raise BudgetExceededError()
