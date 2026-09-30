"""Synthetic persistence benchmark and restart probe; never connects to Canvas."""

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

from canvas_mcp.application.grade_changes import GradeChangeService
from canvas_mcp.domain.grade_state import GradeFact, NamedGrade
from canvas_mcp.domain.models import (
    AccessScope,
    BudgetLimits,
    Course,
    ExternalText,
    Observed,
    Availability,
    Page,
    Profile,
    RequestBudget,
    RequestContext,
)
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import SQLiteStateRepository, PostgreSQLStateRepository
from canvas_mcp.mcp.projection import envelope, grade_changes

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


class SyntheticGrades:
    def __init__(self, count: int = 200):
        self.courses = tuple(
            Course(
                str(x),
                ExternalText("Synthetic course"),
                ExternalText("SYN"),
                Observed(Availability.AVAILABLE, None),
            )
            for x in range(1, 6)
        )
        self.rows = {
            course.id: tuple(
                NamedGrade(
                    GradeFact(
                        course.id, str(x + 1), 1, 80, "B", 100, NOW, NOW, "graded", "visible"
                    ),
                    course.name,
                    ExternalText("Synthetic assignment"),
                )
                for x in range(count)
            )
            for course in self.courses
        }
        self.calls = 0

    async def get_profile(self, ctx):
        self.calls += 1
        return Profile("7", ExternalText("Synthetic"), Observed(Availability.AVAILABLE, "UTC"))

    async def list_courses(self, ctx, page, active_only):
        self.calls += 1
        return Page(self.courses, None, True)

    async def list_grade_facts(self, ctx, course_id, page):
        self.calls += 1
        start = int(page.cursor or "0")
        values = self.rows[course_id]
        end = start + page.limit
        return Page(values[start:end], str(end) if end < len(values) else None, end >= len(values))


async def run(mode: str) -> None:
    settings = StateSettings.load(os.environ)
    repository = (
        SQLiteStateRepository(settings)
        if settings.backend == "sqlite"
        else PostgreSQLStateRepository(settings)
    )
    await repository.migrate()
    source = SyntheticGrades(1 if mode.startswith("restart") else 200)
    scope = AccessScope(
        "synthetic_restart_probe" if mode.startswith("restart") else uuid4().hex, "personal_canvas"
    )
    ctx = RequestContext(
        scope, uuid4().hex, NOW, RequestBudget(BudgetLimits(100, 20, 131072), time.monotonic() + 60)
    )
    service = GradeChangeService(source, repository, "https://synthetic.invalid")
    if mode.startswith("restart"):
        response = await service.check(ctx, lambda x: envelope(x, grade_changes))
        expected = mode == "restart-write"
        assert response.data.baseline_created is expected
        assert not response.data.new_grades and not response.data.changed_grades
        print(json.dumps({"mode": mode, "persistent_baseline_verified": True}))
        return
    timings = {}
    for label in ("first_baseline", "no_changes", "changed_grade_batch"):
        if label == "changed_grade_batch":
            source.rows = {
                course: tuple(
                    replace(x, fact=replace(x.fact, score=85)) if i < 10 else x
                    for i, x in enumerate(rows)
                )
                for course, rows in source.rows.items()
            }
        source.calls = 0
        started = time.perf_counter()
        result = await service.check(ctx, lambda x: envelope(x, grade_changes))
        timings[label] = {
            "milliseconds": round((time.perf_counter() - started) * 1000, 2),
            "provider_calls": source.calls,
            "reported_events": len(result.data.new_grades) + len(result.data.changed_grades),
            "complete": result.complete,
        }
    print(
        json.dumps(
            {"backend": settings.backend, "courses": 5, "assignments": 1000, "timings": timings}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("benchmark", "restart-write", "restart-read"))
    asyncio.run(run(parser.parse_args().mode))
