import asyncio
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_initialize_tools_and_fake_calls():
    async def run():
        env = dict(os.environ)
        env.pop("CANVAS_ACCESS_TOKEN", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("fake_server.py"))],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.instructions and "untrusted" in initialized.instructions
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert len(names) == 16
                assert "canvas_get_profile" in names and "canvas_list_courses" in names
                profile = await session.call_tool("canvas_get_profile", {})
                assert not profile.isError
                assert profile.structuredContent["data"]["name"]["text"] == "Synthetic Student"
                courses = await session.call_tool("canvas_list_courses", {})
                assert not courses.isError
                assert courses.structuredContent["data"]["items"][0]["course_id"] == 8
                bad = await session.call_tool("canvas_get_profile", {"extra": "SECRET_SENTINEL"})
                assert bad.isError
                assert "SECRET_SENTINEL" not in str(bad)
                malformed = await session.call_tool(
                    "canvas_list_assignments", {"course_id": True, "extra": "SECRET_SENTINEL"}
                )
                assert malformed.isError
                assert "SECRET_SENTINEL" not in str(malformed)

    asyncio.run(run())


def test_real_entrypoint_invalid_configuration_keeps_stdout_clean():
    env = dict(os.environ)
    env.pop("CANVAS_ACCESS_TOKEN", None)
    env["CANVAS_BASE_URL"] = "https://canvas.example.edu"
    process = subprocess.run(
        [sys.executable, "-m", "canvas_mcp.mcp_server"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1
    assert process.stdout == b""
    assert b"configuration_error" in process.stderr
    assert b"Traceback" not in process.stderr


def test_real_entrypoint_initializes_without_canvas_network():
    async def run():
        env = dict(os.environ)
        env["CANVAS_BASE_URL"] = "https://canvas.example.edu"
        env["CANVAS_ACCESS_TOKEN"] = "SYNTHETIC_TOKEN_123456"
        env.pop("DOWNLOAD_DIRECTORY", None)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-I", "-m", "canvas_mcp.mcp_server"],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "canvas_student"
                assert len((await session.list_tools()).tools) == 16

    asyncio.run(run())
