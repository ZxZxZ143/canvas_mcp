"""Portable package and local launcher contract, without Canvas credentials."""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "canvas-student"


def test_portable_manifests_and_skills_are_self_contained():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "canvas-student"
    assert manifest["version"] == "0.1.0"
    assert set(mcp["mcpServers"]) == {"canvas_student"}
    assert mcp["mcpServers"]["canvas_student"]["type"] == "stdio"
    assert mcp["mcpServers"]["canvas_student"]["cwd"] == "./"
    assert "E:\\canvas_mcp" not in json.dumps(mcp)
    assert {
        item.relative_to(PLUGIN).as_posix() for item in PLUGIN.rglob("*") if item.is_file()
    } == {
        "plugin.json",
        "mcp.json",
        "scripts/Start-CanvasMcp.ps1",
        "skills/canvas-navigation/SKILL.md",
        "skills/assignment-workflow/SKILL.md",
        "skills/assignment-workflow/references/eval-cases.json",
        "skills/course-materials/SKILL.md",
        "skills/study-overview/SKILL.md",
        "skills/study-planner/SKILL.md",
        "skills/study-planner/references/eval-cases.json",
    }
    skills = {item.name for item in (PLUGIN / "skills").iterdir() if item.is_dir()}
    assert skills == {
        "canvas-navigation",
        "assignment-workflow",
        "course-materials",
        "study-overview",
        "study-planner",
    }
    for skill in skills:
        content = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "AGENTS.md" not in content
        assert "CANVAS_ACCESS_TOKEN=" not in content


def test_portable_launcher_initializes_in_a_clean_environment(tmp_path):
    binding_dir = tmp_path / "CanvasStudent"
    binding_dir.mkdir()
    (binding_dir / "binding.json").write_text(
        json.dumps({"python": sys.executable}), encoding="utf-8"
    )
    entry = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "canvas_student"
    ]
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path)
    env.pop("CANVAS_ACCESS_TOKEN", None)
    params = StdioServerParameters(
        command=entry["command"],
        args=entry["args"],
        cwd=str(PLUGIN),
        env=env,
    )

    async def run():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "canvas_student"
                tool_names = {tool.name for tool in (await session.list_tools()).tools}
                assert len(tool_names) == 17
                referenced = set()
                for skill in (PLUGIN / "skills").glob("*/SKILL.md"):
                    referenced.update(
                        re.findall(r"\bcanvas_[a-z_]+\b", skill.read_text(encoding="utf-8"))
                    )
                # Skills describe the optional HTTP content route conditionally;
                # this local launcher still exposes exactly the original tools.
                assert referenced <= tool_names | {"canvas_get_file_content"}
                assert "canvas_get_file_content" not in tool_names

    asyncio.run(run())
