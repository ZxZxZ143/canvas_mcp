# One credential handoff for the local CLIs. Use a dedicated, non-transcribed shell.
[CmdletBinding()]
param(
    [ValidateSet('Profile', 'Academic', 'Files', 'FileSample', 'MimeDiagnostic', 'MimeTopology', 'RouteParity', 'PublicUrlParity', 'PublicUrlOrigin', 'Transport')]
    [string]$Mode = 'Profile',
    [switch]$Live,
    [switch]$RememberIfValidated,
    [switch]$UserAgentMatrix,
    [switch]$PrivateCanvasOrigin,
    [string]$TrustedPrivateIps = ''
)

if (-not $Live -or ($RememberIfValidated -and $Mode -ne 'MimeDiagnostic')) {
    Write-Output 'Requires -Live; remembering is allowed only with MimeDiagnostic.'
    exit 2
}
if ($UserAgentMatrix -and $Mode -ne 'Transport') {
    Write-Output 'User-Agent matrix requires Transport mode.'
    exit 2
}

$canvasLivePython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
$canvasLivePresence = Join-Path $PSScriptRoot 'credential_presence.py'
$canvasLivePointer = [IntPtr]::Zero
$canvasLiveSecure = $null
$canvasLiveExit = 1
$canvasLiveSettings = @(
    'CANVAS_BASE_URL', 'CANVAS_DOWNLOAD_ORIGINS', 'DOWNLOAD_DIRECTORY',
    'CANVAS_MIME_RUNTIME_DIRECTORY', 'CANVAS_APPLICATION_PROFILE',
    'REQUEST_TIMEOUT', 'DOWNLOAD_TIMEOUT', 'LOG_LEVEL',
    'CANVAS_ALLOW_PRIVATE_ORIGIN', 'CANVAS_TRUSTED_PRIVATE_IPS'
)
$canvasLivePrevious = @{}
foreach ($canvasLiveName in $canvasLiveSettings) {
    $canvasLivePrevious[$canvasLiveName] = [Environment]::GetEnvironmentVariable($canvasLiveName, 'Process')
}
try {
    $env:CANVAS_BASE_URL = Read-Host 'Canvas HTTPS origin'
    if ($PrivateCanvasOrigin -and -not $TrustedPrivateIps.Trim()) {
        $TrustedPrivateIps = Read-Host 'Exact trusted private Canvas IPs, comma-separated'
    }
    $env:CANVAS_TRUSTED_PRIVATE_IPS = $TrustedPrivateIps
    $env:CANVAS_ALLOW_PRIVATE_ORIGIN = if ($PrivateCanvasOrigin) { 'true' } else { 'false' }
    if ($TrustedPrivateIps.Trim()) {
        Write-Output 'Exact trusted private Canvas IP policy configured.'
    }
    $env:CANVAS_DOWNLOAD_ORIGINS = ''
    if ($Mode -notin @('Profile', 'Academic', 'Transport')) {
        $env:CANVAS_DOWNLOAD_ORIGINS = Read-Host 'Previously approved HTTPS CDN origins; Enter for none'
    }
    $env:DOWNLOAD_DIRECTORY = 'C:\canvas_mcp_runtime\downloads'
    $env:CANVAS_MIME_RUNTIME_DIRECTORY = 'C:\canvas_mcp_runtime\diagnostics'
    $env:CANVAS_APPLICATION_PROFILE = 'personal'
    $env:REQUEST_TIMEOUT = '20'
    $env:DOWNLOAD_TIMEOUT = '120'
    $env:LOG_LEVEL = 'INFO'
    $canvasLiveSecure = Read-Host 'Canvas personal access token' -AsSecureString
    $canvasLivePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($canvasLiveSecure)
    $env:CANVAS_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($canvasLivePointer)

    & $canvasLivePython -I $canvasLivePresence
    if ($LASTEXITCODE -eq 0) {
        if ($Mode -eq 'Transport') {
            # Diagnose a failing profile itself; never gate on that live request
            # or dispatch courses/files/MIME as part of this mode.
            if ($UserAgentMatrix) {
                & $canvasLivePython -I -m canvas_mcp.transport_probe --live --details --user-agent-matrix
            } else {
                & $canvasLivePython -I -m canvas_mcp.transport_probe --live --details
            }
            $canvasLiveExit = $LASTEXITCODE
        } else {
            # Same process environment, token and installed package for both children.
            # A failed profile/course smoke blocks every downstream diagnostic.
            & $canvasLivePython -I -m canvas_mcp.smoke --live
            $canvasLiveExit = $LASTEXITCODE
            if ($canvasLiveExit -eq 0 -and $Mode -ne 'Profile') {
                if ($Mode -notin @('Academic', 'Files')) {
                    New-Item -ItemType Directory -Path 'C:\canvas_mcp_runtime' -Force -ErrorAction Stop | Out-Null
                }
                switch ($Mode) {
                    'Academic' { & $canvasLivePython -I -m canvas_mcp.academic_smoke --live --debug }
                    'Files' { & $canvasLivePython -I -m canvas_mcp.file_smoke --live --debug }
                    'FileSample' { & $canvasLivePython -I -m canvas_mcp.file_smoke --live --download-sample --debug }
                    'MimeTopology' { & $canvasLivePython -I -m canvas_mcp.mime_probe --live --allow-mismatch-diagnostic --redirect-topology }
                    'RouteParity' { & $canvasLivePython -I -m canvas_mcp.route_probe --live --route-parity }
                    'PublicUrlParity' { & $canvasLivePython -I -m canvas_mcp.public_url_probe --live --public-url-parity }
                    'PublicUrlOrigin' { & $canvasLivePython -I -m canvas_mcp.public_url_probe --live --public-url-origin }
                    'MimeDiagnostic' {
                        if ($RememberIfValidated) {
                            & $canvasLivePython -I -m canvas_mcp.mime_probe --live --allow-mismatch-diagnostic --remember-if-validated
                        } else {
                            & $canvasLivePython -I -m canvas_mcp.mime_probe --live --allow-mismatch-diagnostic
                        }
                    }
                }
                $canvasLiveExit = $LASTEXITCODE
            }
        }
    }
} catch {
    Write-Output 'Canvas live helper failed; no error details displayed.'
    $canvasLiveExit = 1
} finally {
    Remove-Item Env:CANVAS_ACCESS_TOKEN -ErrorAction SilentlyContinue
    if ($canvasLivePointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($canvasLivePointer)
    }
    if ($null -ne $canvasLiveSecure) { $canvasLiveSecure.Dispose() }
    foreach ($canvasLiveName in $canvasLiveSettings) {
        [Environment]::SetEnvironmentVariable($canvasLiveName, $canvasLivePrevious[$canvasLiveName], 'Process')
    }
}
exit $canvasLiveExit
