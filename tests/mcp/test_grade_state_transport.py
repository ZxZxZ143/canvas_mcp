import asyncio
import io
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from canvas_mcp.application.grade_changes import GradeChangeService
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.infrastructure.state.sql import UnavailableStateRepository
from canvas_mcp.infrastructure.logging.events import EventLogger
from canvas_mcp.mcp.tools import create_server
from tests.unit.test_grade_changes import Grades
from tests.conftest import response, PROFILE, COURSE
from tests.contract.test_grade_mapping import raw


def test_real_provider_and_mcp_change_round_trip(state_repo, stack, academic_payloads):
    async def run():
        payload = raw()
        newer = raw(score=85)
        replies = [
            response(PROFILE),
            response([COURSE]),
            response(academic_payloads["course"]),
            response([payload]),
            response(PROFILE),
            response([COURSE]),
            response(academic_payloads["course"]),
            response([newer]),
        ]
        async with stack(replies) as s:
            connection = CanvasConnection(
                s.app,
                s.scope,
                s.settings,
                s.academic,
                grade_changes=GradeChangeService(
                    s.provider, state_repo, s.settings.canvas_origin, audit=s.logger.state_check
                ),
            )
            server = create_server(connection, None)
            first = await server._tool_manager.call_tool(
                "canvas_get_grade_changes", {}, convert_result=True
            )
            assert first[1]["data"]["baseline_created"]
            second = await server._tool_manager.call_tool(
                "canvas_get_grade_changes", {}, convert_result=True
            )
            data = second[1]["data"]
            assert data["changed_grades"][0]["previous"]["score"] == 80
            assert data["changed_grades"][0]["current"]["score"] == 85
            wire = json.dumps(second[1])
            assert (
                "user_id" not in wire
                and "state_hash" not in wire
                and str(s.scope.connection_id) not in wire
            )
            logs = s.logs.getvalue()
            assert '"event": "baseline_created"' in logs
            assert '"event": "grade_diff_completed"' in logs
            assert "Homework" not in logs and "score" not in logs

    asyncio.run(run())


def test_mcp_state_unavailable_is_safe_and_other_reads_still_work(stack):
    async def run():
        async with stack([response(PROFILE)]) as s:
            connection = CanvasConnection(
                s.app,
                s.scope,
                s.settings,
                s.academic,
                grade_changes=GradeChangeService(Grades(), UnavailableStateRepository(), "origin"),
            )
            server = create_server(connection, None)
            with pytest.raises(ToolError) as error:
                await server._tool_manager.call_tool("canvas_get_grade_changes", {})
            assert "state_store_unavailable" in str(error.value)
            profile = await server._tool_manager.call_tool(
                "canvas_get_profile", {}, convert_result=True
            )
            assert profile[1]["data"]["name"]["text"] == "Student Name"

    asyncio.run(run())


def test_state_audit_allowlist_has_counts_only():
    stream = io.StringIO()
    logger = EventLogger("INFO", stream)
    logger.state_check("PRIVATE_SCORE_80", 1, 0, 1, 0)
    logger.state_check("grade_diff_completed", 1, 0, 1, 0)
    event = json.loads(stream.getvalue())
    assert event == {
        "event": "grade_diff_completed",
        "scanned_count": 1,
        "failed_count": 0,
        "new_count": 1,
        "changed_count": 0,
    }


def test_driver_debug_logging_cannot_disclose_connection_or_sql():
    import logging
    from canvas_mcp.infrastructure.state.sql import joined

    logger = logging.getLogger("psycopg")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    before = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        asyncio.run(joined(lambda: logger.warning("SECRET_DSN_AND_GRADE")))
        assert not stream.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(before)
