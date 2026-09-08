param()

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("offerpilot-install-gate-" + [System.Guid]::NewGuid().ToString("N"))
$ToolDir = Join-Path $TempRoot "uv-tools"
$BinDir = Join-Path $TempRoot "bin"

New-Item -ItemType Directory -Force -Path $ToolDir, $BinDir | Out-Null
$previousToolDir = $env:UV_TOOL_DIR
$previousBinDir = $env:UV_TOOL_BIN_DIR

try {
    Push-Location $Repo
    try {
        uv run oc --help | Out-Null

        $env:UV_TOOL_DIR = $ToolDir
        $env:UV_TOOL_BIN_DIR = $BinDir
        uv tool install --force .

        $oc = Get-ChildItem -Path $BinDir -Filter "oc*" | Select-Object -First 1
        if (-not $oc) {
            throw "uv tool install did not create an oc executable in $BinDir"
        }
        & $oc.FullName --help | Out-Null
    }
    finally {
        Pop-Location
    }

    Write-Host "Install gate passed"
}
finally {
    $env:UV_TOOL_DIR = $previousToolDir
    $env:UV_TOOL_BIN_DIR = $previousBinDir
    if (Test-Path $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
