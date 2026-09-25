# Local binding only. Stdout belongs to the child MCP protocol.
$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $env:CANVAS_BASE_URL = 'https://canvas.narxoz.kz'
    $env:CANVAS_TRUSTED_PRIVATE_IPS = '192.168.4.200'
    $env:CANVAS_DOWNLOAD_ORIGINS = ''
    $env:CANVAS_CREDENTIAL_PROVIDER = 'windows'
    $env:DOWNLOAD_DIRECTORY = 'C:\canvas_mcp_runtime\plugin_downloads'
    Remove-Item Env:CANVAS_ACCESS_TOKEN -ErrorAction SilentlyContinue

    $bindingPath = Join-Path $env:LOCALAPPDATA 'CanvasStudent\binding.json'
    $binding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
    $pythonPath = [string]$binding.python
    if (-not [System.IO.Path]::IsPathRooted($pythonPath) -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'invalid local binding'
    }
    & $pythonPath -I -m canvas_mcp.mcp_server
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Canvas Student MCP startup failed: local_binding_or_configuration')
    exit 1
}
