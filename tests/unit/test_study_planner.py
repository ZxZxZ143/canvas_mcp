"""Planner evidence, bounded scanning, timezone and omission contracts. No live calls."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.application.planner import features, planning_window
from canvas_mcp.domain.errors import UpstreamUnavailableError, ValidationError
from canvas_mcp.domain.models import Availability, ExternalText, Observed, SubmissionState
from canvas_mcp.mcp.projection import envelope, study_plan_context
from tests.unit.test_workload_resilience import IndependentCourses

NOW = datetime(2026, 9, 30, 19, 1, tzinfo=timezone.utc)
KNOWN_NONE = Observed(Availability.AVAILABLE, None)


class PlannerCourses(IndependentCourses):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = {}
        self.assignment = replace(
            self.assignment,
            description=Observed(
                Availability.AVAILABLE, ExternalText("Answer five quiz questions.")
            ),
            due_at=Observed(Availability.AVAILABLE, NOW + timedelta(hours=2)),
            can_submit=True,
            published=True,
        )

    async def list_assignments(self, ctx, course_id, page, query=None, *, workload_context=False):
        assert workload_context is True
        batch = await super().list_assignments(ctx, course_id, page, query)
        return replace(batch, items=self.rows.get(course_id, batch.items))


def item(fake, identity, due, *, state=SubmissionState.NOT_SUBMITTED, **kwargs):
    sub = replace(fake.assignment.submission.value, assignment_id=str(identity), state=state)
    return replace(
        fake.assignment,
        id=str(identity),
        due_at=Observed(Availability.AVAILABLE, due),
        submission=Observed(Availability.AVAILABLE, sub),
        **kwargs,
    )


def fetch(stack, fake, *, days=7, zone="Asia/Qyzylorda", maximum=None):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, as_of=NOW)
            if maximum:
                ctx = replace(
                    ctx,
                    budget=replace(
                        ctx.budget, limits=replace(ctx.budget.limits, max_response_bytes=maximum)
                    ),
                )
            result = await AcademicService(fake).get_workload(ctx, days=days, timezone=zone)
            return envelope(
                result, study_plan_context, **({"maximum_wire_bytes": maximum} if maximum else {})
            )

    return asyncio.run(run())


def test_lightweight_cross_course_single_scan_and_no_estimates(stack):
    fake = PlannerCourses()
    output = fetch(stack, fake)
    assert len(output["data"]["items"]) == 5
    assert fake.calls == [("discovery", None)] + [("assignments", str(i)) for i in range(1, 6)]
    row = output["data"]["items"][0]
    assert row["task_type_signals"] == ["quiz"]
    assert row["effort"]["state"] == "not_estimated"
    assert row["module_context"]["state"] == "not_requested"
    assert row["description_available"] is True
    assert "user_id" not in json.dumps(output)
    assert "description_verbatim_text" not in json.dumps(output)


def test_old_overdue_submitted_closed_and_unknown_status(stack):
    fake = PlannerCourses()
    fake.empty = {"2", "3", "4", "5"}
    fake.rows["1"] = (
        item(fake, 1, NOW - timedelta(days=90)),
        item(fake, 2, NOW - timedelta(days=1), state=SubmissionState.SUBMITTED),
        item(fake, 3, NOW - timedelta(days=1), lock_at=Observed(Availability.AVAILABLE, NOW)),
        item(fake, 4, NOW - timedelta(days=1), state=SubmissionState.UNKNOWN),
        item(fake, 5, None),
        item(fake, 6, NOW + timedelta(days=8)),
    )
    output = fetch(stack, fake)
    rows = {r["assignment_id"]: r for r in output["data"]["items"]}
    assert set(rows) == {1, 3, 4, 5}
    assert rows[1]["overdue"] is True
    assert rows[3]["availability"] == "closed"
    assert rows[4]["submitted"] is None and "submission_unknown" in rows[4]["warning_flags"]
    assert rows[5]["due_state"] == "no_due_date" and rows[5]["hours_until_due"] is None


def test_partial_course_failure_discards_all_failed_pages(stack):
    fake = PlannerCourses(failures={"3": UpstreamUnavailableError()})
    fake.paginated_failure = True
    output = fetch(stack, fake)
    assert output["complete"] is False
    assert output["data"]["coverage"]["failed"] == [3]
    assert {x["course_id"] for x in output["data"]["items"]} == {1, 2, 4, 5}
    assert any(x["code"] == "upstream_unavailable" for x in output["warnings"])


def test_graded_missing_zero_does_not_hide_unfinished_work(stack):
    fake = PlannerCourses()
    fake.empty = {"2", "3", "4", "5"}
    sub = replace(fake.assignment.submission.value, graded=True, missing=True)
    fake.rows["1"] = (replace(fake.assignment, submission=Observed(Availability.AVAILABLE, sub)),)
    output = fetch(stack, fake)
    row = output["data"]["items"][0]
    assert row["submitted"] is False and row["graded"] is True and row["missing"] is True
    assert "graded_without_submission" in row["warning_flags"]


def test_planning_today_keeps_tomorrow_and_large_two_day_tasks_with_default_lookahead(stack):
    fake = PlannerCourses()
    fake.empty = {"2", "3", "4", "5"}
    fake.rows["1"] = (
        item(fake, 1, NOW + timedelta(days=1)),
        item(fake, 2, NOW + timedelta(days=2)),
    )
    assert {x["assignment_id"] for x in fetch(stack, fake)["data"]["items"]} == {1, 2}


def test_overdue_and_undated_have_independent_limits(stack):
    fake = PlannerCourses()
    fake.empty = {"2", "3", "4", "5"}
    fake.rows["1"] = tuple(item(fake, i, NOW - timedelta(days=i)) for i in range(1, 36)) + tuple(
        item(fake, i, None) for i in range(36, 51)
    )
    rows = fetch(stack, fake)["data"]["items"]
    assert sum(x["overdue"] is True for x in rows) <= 30
    assert sum(x["due_state"] == "no_due_date" for x in rows) <= 10


@pytest.mark.parametrize("days", [1, 7, 30])
def test_local_calendar_window_midnight_and_source_timestamps(stack, days):
    fake = PlannerCourses()
    _, _, end = planning_window(NOW, days, "Asia/Qyzylorda")
    fake.empty = {"2", "3", "4", "5"}
    fake.rows["1"] = (item(fake, 1, end - timedelta(seconds=1)), item(fake, 2, end))
    output = fetch(stack, fake, days=days)
    rows = output["data"]["items"]
    assert len(rows) == 1
    assert rows[0]["due_at_local"].endswith("23:59:59+05:00")
    assert rows[0]["due_at"]["value"] == (end - timedelta(seconds=1)).isoformat()
    assert rows[0]["days_until_due"] == days - 1
    assert output["data"]["planning_window"]["end_at_exclusive"] == end.isoformat()


@pytest.mark.parametrize(
    "now,zone,expected",
    [
        ("2026-09-30T18:59:59+00:00", "Asia/Qyzylorda", "2026-09-30T19:00:00+00:00"),
        ("2026-09-30T19:00:00+00:00", "Asia/Qyzylorda", "2026-10-01T19:00:00+00:00"),
        ("2026-03-08T05:00:00+00:00", "America/New_York", "2026-03-09T04:00:00+00:00"),
        ("2026-11-01T04:00:00+00:00", "America/New_York", "2026-11-02T05:00:00+00:00"),
    ],
)
def test_timezone_offset_and_dst_calendar_days(now, zone, expected):
    assert planning_window(datetime.fromisoformat(now), 1, zone)[2].isoformat() == expected


@pytest.mark.parametrize(
    "days,zone",
    [(0, "UTC"), (31, "UTC"), (True, "UTC"), (1, "Invalid/Zone"), (1, "../UTC"), (1, "")],
)
def test_invalid_planner_input_rejected_before_fetch(stack, days, zone):
    fake = PlannerCourses()
    with pytest.raises(ValidationError):
        fetch(stack, fake, days=days, zone=zone)
    assert fake.calls == []


def test_default_timezone_is_explicit_and_unknown_requirements_not_quick(stack):
    fake = PlannerCourses()
    fake.assignment = replace(
        fake.assignment,
        title=ExternalText("Tiny quick quiz"),
        description=Observed(Availability.UNAVAILABLE, None),
    )
    output = fetch(stack, fake, zone=None)
    assert output["complete"] is False
    assert any(x["code"] == "timezone_defaulted_to_utc" for x in output["warnings"])
    assert all(
        x["task_type_signals"] == [] and x["description_available"] is None
        for x in output["data"]["items"]
    )


def test_counts_truncation_and_evidence_are_not_effort():
    fake = PlannerCourses()
    record = replace(
        fake.assignment, description=Observed(Availability.AVAILABLE, ExternalText("report " * 400))
    )
    evidence = features(record, NOW)
    assert evidence.description_excerpt.truncated
    assert evidence.description_length_band == "long"
    assert evidence.task_type_signals == ("report",)
    assert evidence.rubric_criteria_count == 0
    assert "description_excerpt_truncated" in evidence.warning_flags
    assert (
        features(
            replace(record, rubric=Observed(Availability.UNAVAILABLE, None)), NOW
        ).rubric_criteria_count
        is None
    )


def test_item_and_exact_wire_limits_disclose_omissions(stack):
    fake = PlannerCourses()
    fake.empty = {"2", "3", "4", "5"}
    fake.rows["1"] = tuple(item(fake, i, NOW + timedelta(minutes=i)) for i in range(1, 60))
    output = fetch(stack, fake)
    assert output["complete"] is False
    assert len(output["data"]["items"]) <= 40
    assert any(x["code"] == "item_limit_reached" for x in output["warnings"])
    bounded = fetch(stack, PlannerCourses(), maximum=14000)
    assert bounded["complete"] is False
    assert len(bounded["data"]["items"]) < 5
    assert any(x["code"] == "response_limit_reached" for x in bounded["warnings"])


def test_synthetic_eval_cases_cover_requested_scenarios():
    path = Path(__file__).resolve().parents[2] / "skills/study-planner/references/eval-cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {x["id"] for x in data["cases"]} == set("ABCDEFGHIJKLMNOP")
    assert all(x["expected"] for x in data["cases"])
    for case in data["cases"]:
        assert set(case["tasks"]) <= data["tasks"].keys()
    assert {x.get("budget_minutes") for x in data["cases"]} >= {30, 120}
