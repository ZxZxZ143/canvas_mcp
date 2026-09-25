"""Test-only stdio process with fake application results and no Canvas access."""

import asyncio
from datetime import datetime, timezone

from canvas_mcp.application.contracts import Result
from canvas_mcp.domain.models import Availability, Course, ExternalText, Observed, Page, Profile
from canvas_mcp.mcp_server import create_server


class FakeConnection:
    async def get_profile(self):
        return Result(
            Profile(
                "7", ExternalText("Synthetic Student"), Observed(Availability.AVAILABLE, "UTC")
            ),
            "fake-request",
            datetime(2026, 9, 25, tzinfo=timezone.utc),
            True,
            (),
        )

    async def list_courses(self, page, active_only):
        return Result(
            Page(
                (
                    Course(
                        "8",
                        ExternalText("Synthetic Course"),
                        ExternalText("SYN101"),
                        Observed(Availability.AVAILABLE, ExternalText("Fall")),
                    ),
                ),
                None,
                True,
            ),
            "fake-request",
            datetime(2026, 9, 25, tzinfo=timezone.utc),
            True,
            (),
        )


if __name__ == "__main__":
    asyncio.run(create_server(FakeConnection(), None).run_stdio_async())
