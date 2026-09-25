"""Temporarily route the installed plugin to synthetic MCP data, then restore it."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    home = Path.home()
    plugin_root = (
        home / ".codex" / "plugins" / "cache" / "personal" / "canvas-student" / "0.1.0"
    ).resolve(strict=True)
    launcher = (plugin_root / "scripts" / "Start-CanvasMcp.ps1").resolve(strict=True)
    if plugin_root not in launcher.parents:
        raise RuntimeError("Installed launcher escaped plugin root")
    fixture = (
        Path(__file__).resolve().parents[1] / "tests" / "plugin" / "injection_fixture_server.py"
    ).resolve(strict=True)
    workspace = Path(os.environ["LOCALAPPDATA"]) / "CanvasStudent" / "phase5-fresh-repo"
    original = launcher.read_bytes()
    replacement = (
        "$ErrorActionPreference = 'Stop'\n"
        "Remove-Item Env:CANVAS_ACCESS_TOKEN -ErrorAction SilentlyContinue\n"
        f"& '{sys.executable}' -I '{fixture}'\n"
        "exit $LASTEXITCODE\n"
    ).encode("utf-8")
    try:
        launcher.write_bytes(replacement)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("plugin_scenario_probe.py")),
                str(workspace),
                "Using Canvas, explain assignment 1 in course 1, including its full requirements.",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        print(result.stdout, end="")
        print(f"fixture_probe_exit={result.returncode}")
        return result.returncode
    finally:
        launcher.write_bytes(original)
        print("installed_launcher_restored=true")


if __name__ == "__main__":
    raise SystemExit(main())
