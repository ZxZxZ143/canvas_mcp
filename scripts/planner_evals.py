"""Replay reviewer tool plans through real aggregate filtering, entirely synthetic."""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.domain.errors import UpstreamUnavailableError
from canvas_mcp.domain.models import (
    AccessScope,
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
)
from canvas_mcp.infrastructure.canvas.academic_mapping import assignment

ROOT = Path(__file__).resolve().parents[1]


class FixtureCourses:
    def __init__(self, fixture, case):
        self.rows = []
        self.failed = case.get("failed_course")
        for key in case["tasks"]:
            task = fixture["tasks"][key]
            raw = {
                "id": task["assignment_id"],
                "course_id": task["course_id"],
                "name": task["name"],
                "description": task["description"],
                "due_at": task["due_at"],
                "points_possible": None,
                "submission_types": ["online_upload"],
                "unlock_at": None,
                "lock_at": task["due_at"] if task["availability"] == "closed" else None,
                "allowed_attempts": -1,
                "rubric": [],
                "attachments": [],
                "published": True,
                "can_submit": True
                if task["availability"] == "open"
                else False
                if task["availability"] == "closed"
                else None,
                "submission": {
                    "assignment_id": task["assignment_id"],
                    "user_id": 7,
                    "workflow_state": "submitted" if task["submitted"] else "unsubmitted",
                    "submitted_at": task["due_at"] if task["submitted"] else None,
                    "graded_at": None,
                    "excused": False,
                    "missing": False,
                    "late": task.get("late", False),
                    "attachments": [],
                },
            }
            self.rows.append(
                assignment(
                    raw,
                    EntityId(str(task["course_id"])),
                    EntityId("7"),
                    "https://canvas.example.edu",
                    rubric_absence_known=False,
                )
            )
        self.courses = tuple(
            Course(
                EntityId(str(i)),
                ExternalText(name),
                ExternalText("SYN"),
                Observed(Availability.AVAILABLE, None),
            )
            for i, name in [(1, "AI"), (2, "Physics"), (3, "Theory")]
        )

    async def list_courses(self, ctx, page, active_only):
        return Page(self.courses, None, True)

    async def list_assignments(self, ctx, course_id, page, *, workload_context=False):
        if next(c.name.text for c in self.courses if c.id == course_id) == self.failed:
            raise UpstreamUnavailableError()
        return Page(tuple(x for x in self.rows if x.course_id == course_id), None, True)


async def compatible(fixture, case, response):
    provider = FixtureCourses(fixture, case)
    now = datetime.fromisoformat(case.get("clock", fixture["clock"]).replace("Z", "+00:00"))
    ctx = RequestContext(
        AccessScope(PrincipalId("synthetic"), ConnectionId("synthetic")),
        "synthetic-eval",
        now,
        RequestBudget(BudgetLimits(100, 20, 131072), time.monotonic() + 60),
    )
    service = AcademicService(provider)
    expected = {
        t["assignment_id"] for key in case["tasks"] if not (t := fixture["tasks"][key])["submitted"]
    }
    returned = set()
    aggregation = False
    for call in response["calls"]:
        args = call["arguments"]
        if call["tool"] == "canvas_get_workload":
            data = (await service.get_workload(ctx, **args)).data.workload
            returned.update(int(x.assignment.id) for x in data.items.items)
            aggregation = True
        elif call["tool"] == "canvas_get_overdue":
            data = (await service.get_overdue(ctx, **args)).data
            returned.update(int(x.assignment.id) for x in data.items.items)
            aggregation = True
        elif call["tool"] == "canvas_get_assignment_context":
            assert any(
                int(x.id) == args["assignment_id"] and int(x.course_id) == args["course_id"]
                for x in provider.rows
            )
        else:
            raise AssertionError("Unexpected eval tool")
    return not aggregation or expected <= returned


async def run():
    fixture = json.loads(
        (ROOT / "skills/study-planner/references/eval-cases.json").read_text(encoding="utf-8")
    )
    outputs = json.loads(
        (ROOT / "docs/phase7-1-planner-eval-responses.json").read_text(encoding="utf-8")
    )
    initial = {x["id"]: x for x in outputs["responses"]}
    final = {**initial, **{x["id"]: x for x in outputs["final_replacements"]}}
    first_failures, final_failures = [], []
    for case in fixture["cases"]:
        if not await compatible(fixture, case, initial[case["id"]]):
            first_failures.append(case["id"])
        if not await compatible(fixture, case, final[case["id"]]):
            final_failures.append(case["id"])
    print(
        json.dumps(
            {
                "source": "synthetic production aggregation replay",
                "cases": len(fixture["cases"]),
                "initial_incompatible": first_failures,
                "final_incompatible": final_failures,
                "final_compatible": len(fixture["cases"]) - len(final_failures),
                "prose": "independently reviewed; retrieval replay does not grade prose",
            }
        )
    )
    assert first_failures == ["A", "D", "I", "K"]
    assert not final_failures


if __name__ == "__main__":
    asyncio.run(run())
