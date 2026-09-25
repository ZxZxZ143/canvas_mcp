import asyncio
import sys
from contextlib import asynccontextmanager

import pytest

from canvas_mcp import smoke
from canvas_mcp.composition import CanvasConnection, open_canvas_connection
from canvas_mcp.domain.errors import ConfigurationError
from conftest import COURSE, ORIGIN, PROFILE, TOKEN, response


def test_composition_loads_and_closes_without_network():
    async def run():
        async with open_canvas_connection(
            {"CANVAS_BASE_URL": ORIGIN, "CANVAS_ACCESS_TOKEN": TOKEN}
        ) as connection:
            assert TOKEN not in repr(connection)
            provider = connection.service._provider
            assert not provider._client._closed
        assert provider._client._closed

    asyncio.run(run())


def test_missing_credentials_fail_safely():
    async def run():
        try:
            async with open_canvas_connection({"CANVAS_BASE_URL": ORIGIN}):
                raise AssertionError("missing credentials accepted")
        except ConfigurationError as error:
            assert str(error) == "configuration_error"

    asyncio.run(run())


def test_smoke_uses_only_profile_courses_and_safe_summary(stack, monkeypatch, capsys):
    async def run():
        @asynccontextmanager
        async def opened():
            async with stack([response(PROFILE), response([COURSE])]) as s:
                yield CanvasConnection(s.app, s.scope, s.settings)

        monkeypatch.setattr(smoke, "open_canvas_connection", opened)
        assert await smoke.run() == 0

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "Connected as: Student Name" in output and "Courses found: 1" in output
    assert TOKEN not in output and "login_id" not in output and "Authorization" not in output


def test_smoke_requires_explicit_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["canvas_mcp.smoke"])
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
    assert "requires --live" in capsys.readouterr().err
