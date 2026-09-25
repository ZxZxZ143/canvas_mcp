import asyncio
from dataclasses import replace

import pytest

from canvas_mcp.application.connection import ConnectionService
from canvas_mcp.domain.errors import BudgetExceededError, ValidationError
from canvas_mcp.domain.models import (
    Availability,
    Course,
    EntityId,
    ExternalText,
    Observed,
    Page,
    PageRequest,
    Profile,
)


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def get_profile(self, ctx):
        self.calls.append(("profile", ctx))
        return Profile(
            EntityId("1"), ExternalText("Student"), Observed(Availability.AVAILABLE, "UTC")
        )

    async def list_courses(self, ctx, page, active_only):
        self.calls.append(("courses", ctx, page, active_only))
        return Page(
            (
                Course(
                    EntityId("2"),
                    ExternalText("Class"),
                    ExternalText("C1"),
                    Observed(Availability.AVAILABLE, None),
                ),
            ),
            None,
            True,
        )


def test_application_delegates_without_http(stack):
    async def run():
        async with stack([]) as s:
            fake = FakeProvider()
            service = ConnectionService(fake)
            profile = await service.get_profile(s.ctx)
            courses = await service.list_courses(s.ctx, PageRequest(10), False)
            assert profile.data.id == "1" and courses.data.items[0].id == "2"
            assert profile.complete and courses.complete
            assert fake.calls == [("profile", s.ctx), ("courses", s.ctx, PageRequest(10), False)]
            assert not s.backend.writes

    asyncio.run(run())


@pytest.mark.parametrize("limit", [0, -1, 101, True, 2.0, "25"])
def test_input_rejected_before_provider(stack, limit):
    async def run():
        async with stack([]) as s:
            fake = FakeProvider()
            with pytest.raises(ValidationError):
                await ConnectionService(fake).list_courses(s.ctx, PageRequest(limit))
            assert not fake.calls

    asyncio.run(run())


def test_truncation_and_result_budget(stack):
    async def run():
        async with stack([]) as s:

            class Truncated(FakeProvider):
                async def get_profile(self, ctx):
                    result = await super().get_profile(ctx)
                    return replace(result, display_name=ExternalText("clip", True))

            service = ConnectionService(Truncated())
            result = await service.get_profile(s.ctx)
            assert not result.complete and result.warnings[0].code == "content_truncated"
            s.ctx.budget.limits = replace(s.ctx.budget.limits, max_response_bytes=1)
            with pytest.raises(BudgetExceededError):
                await service.get_profile(s.ctx)

    asyncio.run(run())
