"""Production Canvas mapping feeds planner without detail/file endpoints."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from canvas_mcp.mcp.projection import envelope, study_plan_context
from conftest import PROFILE, response

DATA = json.loads((Path(__file__).parents[1] / "fixtures/academic.json").read_text())


def test_assignment_list_retains_planning_evidence_without_n_plus_one(stack):
    raw = copy.deepcopy(DATA["assignment"])
    raw["submission"] = DATA["submission"]
    raw["description"] = "<p>Analyse a dataset in a notebook and write a report.</p>"
    raw["attachments"] = [DATA["attachment"]]

    async def run():
        replies = [
            response(PROFILE),
            response([DATA["course"]]),
            response(DATA["course"]),
            response([raw]),
        ]
        async with stack(replies) as s:
            output = envelope(
                await s.academic.get_workload(
                    replace(s.ctx, as_of=datetime(2026, 9, 30, tzinfo=timezone.utc)),
                    timezone="Asia/Qyzylorda",
                ),
                study_plan_context,
            )
            row = output["data"]["items"][0]
            assert row["description_available"] is True
            assert row["rubric_criteria_count"] == 1
            assert row["direct_attachment_count"] == 1
            assert row["task_type_signals"] == ["report", "programming"]
            assert row["availability"] == "open"
            assert sum(chunk.startswith(b"GET ") for chunk in s.backend.writes) == 4
            wire = b"".join(s.backend.writes)
            assert (
                b"/assignments/10" not in wire
                and b"/files/" not in wire
                and b"/modules" not in wire
            )
            assert "PRIVATE_" not in json.dumps(output) and "DO_NOT_EXPOSE" not in json.dumps(
                output
            )

    asyncio.run(run())


def test_list_omitted_rubric_is_unknown_and_due_equal_now_not_overdue(stack):
    raw = copy.deepcopy(DATA["assignment"])
    raw.pop("rubric")
    raw["submission"] = DATA["submission"]
    now = datetime.fromisoformat(raw["due_at"].replace("Z", "+00:00"))

    async def run():
        async with stack(
            [
                response(PROFILE),
                response([DATA["course"]]),
                response(DATA["course"]),
                response([raw]),
            ]
        ) as s:
            output = envelope(
                await s.academic.get_workload(replace(s.ctx, as_of=now), timezone="UTC"),
                study_plan_context,
            )
            row = output["data"]["items"][0]
            assert row["overdue"] is False and row["hours_until_due"] == 0
            assert row["rubric_available"] is None and row["rubric_criteria_count"] is None
            assert "optional_metadata_unavailable" in row["warning_flags"]
            assert output["complete"] is False

    asyncio.run(run())
