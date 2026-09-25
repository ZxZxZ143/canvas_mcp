# Opt-in secure token handoff for local MCP smoke or an interactive Codex CLI.
[CmdletBinding()]
param(
    [ValidateSet('Smoke', 'Codex')]
    [string]$Mode = 'Smoke',
    [switch]$Live,
    [string]$CanvasOrigin = 'https://canvas.narxoz.kz',
    [string]$TrustedPrivateIps = '192.168.4.200'
)

if (-not $Live) {
    Write-Output 'Requires -Live.'
    exit 2
}

$canvasMcpPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
$canvasMcpSmoke = Join-Path $PSScriptRoot 'mcp_live_smoke.py'
$canvasMcpPointer = [IntPtr]::Zero
$canvasMcpSecure = $null
$canvasMcpExit = 1
$canvasMcpCodexCommand = Get-Command codex -ErrorAction SilentlyContinue
$canvasMcpCodexPath = if ($null -ne $canvasMcpCodexCommand) { $canvasMcpCodexCommand.Source } else { $null }
if ($null -eq $canvasMcpCodexPath -and $Mode -eq 'Codex') {
    $canvasMcpBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
    $canvasMcpCandidates = @(
        Get-ChildItem -LiteralPath $canvasMcpBin -Filter 'codex.exe' -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    if ($canvasMcpCandidates.Count -gt 0) {
        $canvasMcpCodexPath = $canvasMcpCandidates[0].FullName
    }
}
$canvasMcpSettings = @(
    'CANVAS_BASE_URL', 'CANVAS_TRUSTED_PRIVATE_IPS', 'CANVAS_DOWNLOAD_ORIGINS',
    'DOWNLOAD_DIRECTORY', 'REQUEST_TIMEOUT', 'DOWNLOAD_TIMEOUT', 'TERM'
)
$canvasMcpPrevious = @{}
foreach ($canvasMcpName in $canvasMcpSettings) {
    $canvasMcpPrevious[$canvasMcpName] = [Environment]::GetEnvironmentVariable($canvasMcpName, 'Process')
}

try {
    $env:CANVAS_BASE_URL = $CanvasOrigin
    $env:CANVAS_TRUSTED_PRIVATE_IPS = $TrustedPrivateIps
    $env:CANVAS_DOWNLOAD_ORIGINS = ''
    $env:DOWNLOAD_DIRECTORY = 'C:\canvas_mcp_runtime\downloads'
    $env:REQUEST_TIMEOUT = '20'
    $env:DOWNLOAD_TIMEOUT = '120'
    $canvasMcpSecure = Read-Host 'Canvas personal access token' -AsSecureString
    $canvasMcpPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($canvasMcpSecure)
    $env:CANVAS_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($canvasMcpPointer)

    & $canvasMcpPython -I $canvasMcpSmoke
    $canvasMcpExit = $LASTEXITCODE
    if ($canvasMcpExit -eq 0 -and $Mode -eq 'Codex') {
        if ($null -eq $canvasMcpCodexPath) {
            Write-Output 'Codex CLI executable was not found.'
            $canvasMcpExit = 1
        } else {
            Write-Output 'MCP smoke passed. Ask the coursework questions in this Codex session.'
            if (-not $env:TERM -or $env:TERM -eq 'dumb') {
                $env:TERM = 'xterm-256color'
            }
            & $canvasMcpCodexPath --no-alt-screen
            $canvasMcpExit = $LASTEXITCODE
        }
    }
} catch {
    Write-Output "Canvas MCP live helper failed: $($_.Exception.GetType().Name)."
    $canvasMcpExit = 1
} finally {
    Remove-Item Env:CANVAS_ACCESS_TOKEN -ErrorAction SilentlyContinue
    if ($canvasMcpPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($canvasMcpPointer)
    }
    if ($null -ne $canvasMcpSecure) { $canvasMcpSecure.Dispose() }
    foreach ($canvasMcpName in $canvasMcpSettings) {
        [Environment]::SetEnvironmentVariable($canvasMcpName, $canvasMcpPrevious[$canvasMcpName], 'Process')
    }
}
exit $canvasMcpExit
