"""Bounded synthetic aggregation/projection benchmark; no network or model calls."""

import asyncio
import json
import statistics
import time
from datetime import datetime, timedelta, timezone

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.domain.models import (
    AccessScope,
    Assignment,
    Availability,
    BudgetLimits,
    ConnectionId,
    Course,
    EntityId,
    ExternalText,
    Observed,
    Page,
    PrincipalId,
    RequestBudget,
    RequestContext,
    Submission,
    SubmissionState,
)
from canvas_mcp.mcp.projection import envelope, study_plan_context


class Synthetic:
    def __init__(self):
        self.calls = 0

    async def list_courses(self, ctx, page, active_only):
        self.calls += 1
        return Page(
            tuple(
                Course(
                    EntityId(str(i)),
                    ExternalText(f"Course {i}"),
                    ExternalText("SYN"),
                    Observed(Availability.AVAILABLE, None),
                )
                for i in range(1, 6)
            ),
            None,
            True,
        )

    async def list_assignments(self, ctx, course_id, page, *, workload_context=False):
        self.calls += 1
        absent = Observed(Availability.AVAILABLE, None)
        rows = []
        for i in range(1, 3):
            identity = EntityId(str(int(course_id) * 100 + i))
            sub = Submission(
                course_id,
                identity,
                SubmissionState.NOT_SUBMITTED,
                absent,
                False,
                False,
                False,
                False,
                True,
                workflow_state=absent,
                submission_type=absent,
                attempt=absent,
                graded_at=absent,
                attachments=absent,
            )
            rows.append(
                Assignment(
                    identity,
                    course_id,
                    ExternalText(f"Task {i}"),
                    Observed(
                        Availability.AVAILABLE,
                        ExternalText("Solve four exercises and show derivations."),
                    ),
                    Observed(Availability.AVAILABLE, ctx.as_of + timedelta(days=i)),
                    absent,
                    (ExternalText("online_upload"),),
                    absent,
                    unlock_at=absent,
                    lock_at=absent,
                    allowed_attempts=absent,
                    rubric=absent,
                    attachments=absent,
                    submission=Observed(Availability.AVAILABLE, sub),
                    can_submit=True,
                    published=True,
                    required=True,
                )
            )
        return Page(tuple(rows), None, True)


async def run():
    samples = []
    wire = 0
    for _ in range(50):
        provider = Synthetic()
        ctx = RequestContext(
            AccessScope(PrincipalId("synthetic"), ConnectionId("synthetic")),
            "synthetic-benchmark",
            datetime(2026, 9, 30, 15, tzinfo=timezone.utc),
            RequestBudget(BudgetLimits(100, 20, 131072), time.monotonic() + 60),
        )
        started = time.perf_counter()
        payload = envelope(
            await AcademicService(provider).get_workload(ctx, timezone="Asia/Qyzylorda"),
            study_plan_context,
        )
        samples.append((time.perf_counter() - started) * 1000)
        assert provider.calls == 6 and len(payload["data"]["items"]) == 10
        wire = len(json.dumps(payload).encode())
    print(
        json.dumps(
            {
                "source": "synthetic in-memory; no network",
                "iterations": len(samples),
                "courses": 5,
                "assignments": 10,
                "provider_calls_per_request": 6,
                "aggregation_projection_median_ms": round(statistics.median(samples), 3),
                "aggregation_projection_p95_ms": round(sorted(samples)[47], 3),
                "structured_json_bytes": wire,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
