"""Synthetic-only parity through real CLI composition and the real HTTP encoder."""

import asyncio
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PARITY = runpy.run_path(str(SCRIPTS / "auth_parity.py"))


@pytest.mark.parametrize("module", PARITY["CLIS"], ids=lambda module: module.__name__)
@pytest.mark.parametrize("origin", [PARITY["ORIGIN"], PARITY["ORIGIN"] + "/"])
@pytest.mark.parametrize("profile", ["personal", "different-profile"])
@pytest.mark.parametrize("status", [200, 401, 403])
def test_four_real_cli_paths_have_identical_auth(module, origin, profile, status):
    results = asyncio.run(PARITY["exercise"](module, origin=origin, profile=profile, status=status))
    assert all(results.values()), results  # Values are booleans, never headers.


@pytest.mark.parametrize("module", PARITY["CLIS"], ids=lambda module: module.__name__)
@pytest.mark.parametrize("token", [None, ""])
def test_missing_empty_tokens_fail_before_http(module, token):
    results = asyncio.run(PARITY["exercise"](module, token=token))
    assert all(results.values()), results


@pytest.mark.parametrize(
    "token,expected",
    [
        (None, (False, True)),
        ("", (False, True)),
        (PARITY["FAKE_TOKEN"], (True, False)),
        ("invalid token", (False, False)),
    ],
)
def test_presence_diagnostic_only_returns_two_booleans(monkeypatch, capsys, token, expected):
    from unittest.mock import patch

    env = {"CANVAS_BASE_URL": PARITY["ORIGIN"]}
    if token is not None:
        env["CANVAS_ACCESS_TOKEN"] = token
    probe = runpy.run_path(str(SCRIPTS / "credential_presence.py"))
    with patch.dict(os.environ, env, clear=True):
        assert probe["main"]() == (0 if expected[0] else 1)
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out == (
        f"credential_present = {str(expected[0]).lower()}\n"
        f"credential_empty = {str(expected[1]).lower()}\n"
    )


@pytest.mark.parametrize("isolated", [False, True])
def test_isolated_mode_ignores_pythonpath_not_canvas_environment(tmp_path, isolated):
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT")
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        CANVAS_BASE_URL=PARITY["ORIGIN"],
        CANVAS_ACCESS_TOKEN=PARITY["FAKE_TOKEN"],
    )
    command = [sys.executable, *(["-I"] if isolated else [])]
    result = subprocess.run(
        [*command, str(SCRIPTS / "auth_parity.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.splitlines() == [
        "package_source = " + ("installed_wheel" if isolated else "project_source"),
        "authentication_parity = true",
    ]
    presence = subprocess.run(
        [*command, str(SCRIPTS / "credential_presence.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert presence.returncode == 0 and presence.stderr == ""
    assert presence.stdout.splitlines() == ["credential_present = true", "credential_empty = false"]


@pytest.mark.skipif(sys.platform != "win32", reason="native PowerShell comparison")
@pytest.mark.parametrize(
    "synthetic", ["FAKE_CANVAS_TOKEN_123456", "a+/._~-==", "", "nonascii-\u00e9"]
)
def test_securestring_conversions_match_without_leaking(synthetic):
    # Synthetic input only; never reads the real token from parent environment.
    code = (
        "$s = [System.Security.SecureString]::new(); "
        f"'{synthetic}'.ToCharArray() | ForEach-Object {{ $s.AppendChar($_) }}; "
        "try { & './scripts/Compare-CanvasSecureString.ps1' -Value $s | ConvertTo-Json -Compress } "
        "finally { $s.Dispose() }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout) == {
        "length_equal": True,
        "content_equal": True,
        "empty_a": not bool(synthetic),
        "empty_b": not bool(synthetic),
    }


@pytest.mark.skipif(sys.platform != "win32", reason="native PowerShell helper gate")
@pytest.mark.parametrize("args", [[], ["-Live", "-Mode", "MimeTopology", "-RememberIfValidated"]])
def test_canonical_helper_requires_safe_optins(args):
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPTS / "Invoke-CanvasLive.ps1"),
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2 and result.stderr == ""
    assert (
        result.stdout.strip() == "Requires -Live; remembering is allowed only with MimeDiagnostic."
    )
