"""Native helper orchestration with synthetic prompts and intercepted children."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="native PowerShell helper orchestration")
@pytest.mark.parametrize("private", [False, True, "exact"])
@pytest.mark.parametrize(
    "mode,fail_at,expected_calls",
    [
        ("Transport", "presence", ["presence"]),
        ("Transport", "none", ["presence", "canvas_mcp.transport_probe"]),
        ("MimeTopology", "presence", ["presence"]),
        ("MimeTopology", "profile", ["presence", "canvas_mcp.smoke"]),
        ("MimeTopology", "throw", ["presence", "canvas_mcp.smoke"]),
        ("Profile", "none", ["presence", "canvas_mcp.smoke"]),
        ("Academic", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.academic_smoke"]),
        ("Files", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.file_smoke"]),
        ("FileSample", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.file_smoke"]),
        ("MimeDiagnostic", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.mime_probe"]),
        ("MimeTopology", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.mime_probe"]),
        ("RouteParity", "none", ["presence", "canvas_mcp.smoke", "canvas_mcp.route_probe"]),
        (
            "PublicUrlParity",
            "none",
            ["presence", "canvas_mcp.smoke", "canvas_mcp.public_url_probe"],
        ),
        (
            "PublicUrlOrigin",
            "none",
            ["presence", "canvas_mcp.smoke", "canvas_mcp.public_url_probe"],
        ),
    ],
)
def test_canonical_helper_gates_and_cleans_up(mode, fail_at, expected_calls, private):
    # Alias ONLY in this child shell: no Python child, filesystem write or network.
    # Exercise the actual helper including its finally block with synthetic data.
    code = (
        r"""
$global:canvasTestCalls = [System.Collections.Generic.List[string]]::new()
$global:canvasTestCredentialsEqual = $true
$global:canvasTestIsolated = $true
$global:canvasTestPrivateCorrect = $true
$global:canvasTestSecret = $null
$env:CANVAS_BASE_URL = 'restore-this-synthetic-value'
$env:CANVAS_ALLOW_PRIVATE_ORIGIN = 'restore-this-synthetic-flag'
$env:CANVAS_TRUSTED_PRIVATE_IPS = 'restore-this-synthetic-ip'
$env:CANVAS_ACCESS_TOKEN = 'old-synthetic-token'
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    if ($AsSecureString) {
        $global:canvasTestSecret = [System.Security.SecureString]::new()
        'FAKE_CANVAS_TOKEN_123456'.ToCharArray() | ForEach-Object { $global:canvasTestSecret.AppendChar($_) }
        return $global:canvasTestSecret
    }
    if ($Prompt -eq 'Canvas HTTPS origin') { return 'https://canvas.example.edu' }
    if ($Prompt -eq 'Exact trusted private Canvas IPs, comma-separated') { return '192.168.4.200' }
    return ''
}
function New-Item { } # Do not create a runtime directory.
function Test-CanvasPython {
    $global:canvasTestIsolated = $global:canvasTestIsolated -and ($args[0] -eq '-I')
    $global:canvasTestPrivateCorrect = $global:canvasTestPrivateCorrect -and (
        $env:CANVAS_ALLOW_PRIVATE_ORIGIN -ceq 'EXPECTED_PRIVATE') -and (
        [string]$env:CANVAS_TRUSTED_PRIVATE_IPS -ceq 'EXPECTED_IPS')
    $global:canvasTestCredentialsEqual = $global:canvasTestCredentialsEqual -and (
        $env:CANVAS_ACCESS_TOKEN -ceq 'FAKE_CANVAS_TOKEN_123456')
    $canvasTestLabel = if ($args[1] -eq '-m') { $args[2] } else { 'presence' }
    $global:canvasTestCalls.Add($canvasTestLabel)
    $global:LASTEXITCODE = 0
    if ('FAIL_AT' -eq 'presence' -and $canvasTestLabel -eq 'presence') { $global:LASTEXITCODE = 1 }
    if ($canvasTestLabel -eq 'canvas_mcp.smoke') {
        if ('FAIL_AT' -eq 'profile') { $global:LASTEXITCODE = 1 }
        if ('FAIL_AT' -eq 'throw') { throw 'synthetic exception must not escape' }
    }
}
$canvasTestScript = (Resolve-Path './scripts/Invoke-CanvasLive.ps1').Path
$canvasTestPython = Join-Path (Split-Path $canvasTestScript) '..\.venv\Scripts\python.exe'
Set-Alias -Name $canvasTestPython -Value Test-CanvasPython
$canvasTestOutput = & $canvasTestScript -Mode MODE -Live PRIVATE_SWITCH
$canvasTestExit = $LASTEXITCODE
$canvasTestErrors = @($Error | ForEach-Object { $_.FullyQualifiedErrorId })
$canvasTestDisposed = $false
try { $null = $global:canvasTestSecret.Copy() } catch { $canvasTestDisposed = $true }
[pscustomobject]@{
    calls = @($global:canvasTestCalls)
    credentials_equal = $global:canvasTestCredentialsEqual
    isolated = $global:canvasTestIsolated
    private_correct = $global:canvasTestPrivateCorrect
    token_removed = -not (Test-Path Env:CANVAS_ACCESS_TOKEN)
    settings_restored = ($env:CANVAS_BASE_URL -ceq 'restore-this-synthetic-value') -and
        ($env:CANVAS_ALLOW_PRIVATE_ORIGIN -ceq 'restore-this-synthetic-flag') -and
        ($env:CANVAS_TRUSTED_PRIVATE_IPS -ceq 'restore-this-synthetic-ip')
    secure_disposed = $canvasTestDisposed
    safe_output = -not (($canvasTestOutput -join '') -match 'FAKE_CANVAS_TOKEN|synthetic exception')
    exit_code = $canvasTestExit
    errors = $canvasTestErrors
} | ConvertTo-Json -Compress
""".replace("FAIL_AT", fail_at)
        .replace("MODE", mode)
        .replace(
            "PRIVATE_SWITCH",
            "-PrivateCanvasOrigin"
            if private is True
            else "-TrustedPrivateIps '192.168.4.200'"
            if private == "exact"
            else "",
        )
        .replace("EXPECTED_PRIVATE", "true" if private is True else "false")
        .replace("EXPECTED_IPS", "192.168.4.200" if private else "")
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0 and result.stderr == ""
    data = json.loads(result.stdout)
    errors = data.pop("errors")
    assert data == {
        "calls": expected_calls,
        "credentials_equal": True,
        "isolated": True,
        "private_correct": True,
        "token_removed": True,
        "settings_restored": True,
        "secure_disposed": True,
        "safe_output": True,
        "exit_code": 0 if fail_at == "none" else 1,
    }, errors
