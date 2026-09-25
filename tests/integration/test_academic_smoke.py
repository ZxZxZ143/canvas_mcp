import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from canvas_mcp import academic_smoke, smoke
from canvas_mcp.application.academic import AcademicService
from canvas_mcp.composition import CanvasConnection, open_canvas_connection
from conftest import ORIGIN, PROFILE, TOKEN, response


def test_composition_exposes_academic_without_import_time_network():
    async def run():
        async with open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}
        ) as connection:
            assert isinstance(connection.academic, AcademicService)
            assert TOKEN not in repr(connection)

    asyncio.run(run())


def test_academic_smoke_full_stubbed_flow_is_private(stack, academic_payloads, monkeypatch, capsys):
    async def run():
        d = academic_payloads
        d["assignment"]["description"] = "PRIVATE_DESCRIPTION"
        d["assignment"]["rubric"][0]["description"] = "PRIVATE_RUBRIC"
        d["assignment"]["due_at"] = datetime.now(timezone.utc).isoformat()
        replies = [
            response(PROFILE),
            response([d["course"]]),
            response(d["course"]),
            response([d["assignment"]]),
            response(d["course"]),
            response([d["module"]]),
            response(d["course"]),
            response(d["assignment"]),
            response(d["sequence"]),
            response([d["course"]]),
            response(d["course"]),
            response([d["assignment"]]),
            response(d["course"]),
            response([]),
            response(d["course"]),
            response([]),
            response(d["course"]),
            response([d["enrollment"]]),
        ]

        @asynccontextmanager
        async def opened():
            async with stack(replies) as s:
                yield CanvasConnection(s.app, s.scope, s.settings, s.academic)
                assert len([w for w in s.backend.writes if w.startswith(b"GET")]) == 18

        monkeypatch.setattr(academic_smoke, "open_canvas_connection", opened)
        assert await academic_smoke.run(debug=True) == 0

    asyncio.run(run())
    output = capsys.readouterr().out
    for expected in (
        "Courses: 1",
        "Assignments accessible: yes",
        "Modules accessible: yes",
        "Submission data accessible: yes",
        "Calendar API: yes",
        "Announcements API: yes",
        "Grades API: available",
        "Metadata warning:",
    ):
        assert expected in output
    for forbidden in (
        TOKEN,
        "Authorization",
        "PRIVATE_DESCRIPTION",
        "PRIVATE_RUBRIC",
        "PRIVATE_GRADE",
        "PRIVATE_COMMENT",
        "current_score",
        "Synthetic assignment",
        "Synthetic module",
        "Student Name",
        "Synthetic Course",
    ):
        assert forbidden not in output


def test_smoke_opt_in_and_safe_unknown_errors(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["academic_smoke"])
    with pytest.raises(SystemExit) as error:
        academic_smoke.main()
    assert error.value.code == 2 and "requires --live" in capsys.readouterr().err

    @asynccontextmanager
    async def unsafe():
        raise RuntimeError(TOKEN)
        yield

    monkeypatch.setattr(academic_smoke, "open_canvas_connection", unsafe)
    assert asyncio.run(academic_smoke.run()) == 1
    assert TOKEN not in capsys.readouterr().out


def test_original_smoke_debug_warning_is_fixed_code_only(
    stack, academic_payloads, monkeypatch, capsys
):
    async def run():
        @asynccontextmanager
        async def opened():
            async with stack(
                [response({"id": 7, "name": "Student"}), response([academic_payloads["course"]])]
            ) as s:
                yield CanvasConnection(s.app, s.scope, s.settings)

        monkeypatch.setattr(smoke, "open_canvas_connection", opened)
        assert await smoke.run(debug=True) == 0

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "Optional metadata warnings: 1" in output
    assert "Metadata warning: timezone/unavailable" in output
    assert TOKEN not in output and "Synthetic Course" not in output
