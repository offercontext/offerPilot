param([switch]$KeepArtifacts)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("offerpilot-install-gate-" + [System.Guid]::NewGuid().ToString("N"))
$ToolDir = Join-Path $TempRoot "uv-tools"
$BinDir = Join-Path $TempRoot "bin"
$Succeeded = $false

New-Item -ItemType Directory -Force -Path $ToolDir, $BinDir | Out-Null
$previousToolDir = $env:UV_TOOL_DIR
$previousBinDir = $env:UV_TOOL_BIN_DIR

try {
    Push-Location $Repo
    try {
        uv run oc --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Source CLI check failed (exit $LASTEXITCODE)." }

        $env:UV_TOOL_DIR = $ToolDir
        $env:UV_TOOL_BIN_DIR = $BinDir
        uv tool install --force .
        if ($LASTEXITCODE -ne 0) { throw "Tool installation failed (exit $LASTEXITCODE)." }

        $oc = Get-ChildItem -Path $BinDir -Filter "oc*" | Select-Object -First 1
        if (-not $oc) {
            throw "uv tool install did not create an oc executable in $BinDir"
        }
        & $oc.FullName --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Installed CLI check failed (exit $LASTEXITCODE)." }

        $installedPython = Join-Path $ToolDir "offerpilot\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $installedPython -PathType Leaf)) {
            $installedPython = Join-Path $ToolDir "offerpilot\Scripts\python.cmd"
        }
        if (-not (Test-Path -LiteralPath $installedPython -PathType Leaf)) {
            throw "Installed tool environment has no Python at $installedPython"
        }
        & $installedPython -c "import importlib.util; assert importlib.util.find_spec('mcp') is None, 'base install unexpectedly requires MCP'"
        if ($LASTEXITCODE -ne 0) { throw "Base optional-dependency check failed (exit $LASTEXITCODE)." }

        uv tool install --force ".[mcp]"
        if ($LASTEXITCODE -ne 0) { throw "MCP extra installation failed (exit $LASTEXITCODE)." }
        & $oc.FullName mcp --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Installed MCP CLI check failed (exit $LASTEXITCODE)." }
        $mcpData = Join-Path $TempRoot "mcp-acceptance"
        & $installedPython scripts/external-agent-acceptance.py prepare --data-dir $mcpData
        if ($LASTEXITCODE -ne 0) { throw "Installed MCP preparation failed (exit $LASTEXITCODE)." }
        & $installedPython scripts/external-agent-acceptance.py verify --data-dir $mcpData --expected pending
        if ($LASTEXITCODE -ne 0) { throw "Installed MCP pending readback failed (exit $LASTEXITCODE)." }
    }
    finally {
        Pop-Location
    }

    $Succeeded = $true
    Write-Host "Install gate passed (base + MCP extra, real stdio; no automated approval)"
}
finally {
    $env:UV_TOOL_DIR = $previousToolDir
    $env:UV_TOOL_BIN_DIR = $previousBinDir
    if ($Succeeded -and -not $KeepArtifacts -and (Test-Path -LiteralPath $TempRoot)) {
        $resolvedInstallRoot = [System.IO.Path]::GetFullPath($TempRoot)
        $expectedParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $resolvedInstallRoot.StartsWith($expectedParent, [System.StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path -Leaf $resolvedInstallRoot) -notmatch '^offerpilot-install-gate-[0-9a-f]{32}$') {
            throw "Refusing cleanup outside the verified install-gate temporary directory."
        }
        Remove-Item -LiteralPath $resolvedInstallRoot -Recurse -Force
    } else {
        Write-Host "Install gate artifacts retained at $TempRoot"
    }
}
