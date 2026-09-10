param(
    [int]$Port = 18765,
    [string]$DataDir = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
if (-not $SkipBuild) {
    Push-Location (Join-Path $Repo "web")
    try {
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)." }
    }
    finally {
        Pop-Location
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $Repo "web/dist/index.html") -PathType Leaf)) {
    throw "Missing web/dist/index.html. Run a successful frontend build before using -SkipBuild."
}

if (-not $DataDir) {
    $DataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("offerpilot-local-smoke-" + [System.Guid]::NewGuid().ToString("N"))
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$previousData = $env:OFFERPILOT_DATA
$env:OFFERPILOT_DATA = $DataDir
$server = $null
try {
    $server = Start-Process `
        -FilePath "powershell" `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Set-Location '$Repo'; `$env:OFFERPILOT_DATA = '$DataDir'; uv run oc start --port $Port"
        ) `
        -WorkingDirectory $Repo `
        -WindowStyle Hidden `
        -PassThru

    $healthUri = "http://127.0.0.1:$Port/api/health"
    $spaUri = "http://127.0.0.1:$Port/applications/smoke"
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri $healthUri -TimeoutSec 2
            if ($health.Content -match '"status"\s*:\s*"ok"') {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "OfferPilot did not become healthy at $healthUri"
    }

    $spa = Invoke-WebRequest -UseBasicParsing -Uri $spaUri -TimeoutSec 5
    if ($spa.Content -notmatch "root") {
        throw "SPA fallback did not serve index.html at $spaUri"
    }

    Push-Location $Repo
    try {
        uv run oc smoke --static-dir web/dist
        if ($LASTEXITCODE -ne 0) { throw "Core smoke failed (exit $LASTEXITCODE)." }
    }
    finally {
        Pop-Location
    }

}
finally {
    try {
        if ($server -and -not $server.HasExited) {
            # The launcher owns uv/oc/Python descendants; stopping only it leaks the server.
            taskkill.exe /PID $server.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Server cleanup failed (exit $LASTEXITCODE)." }
            if (-not $server.WaitForExit(5000)) { throw "Server cleanup did not finish." }
        }
    }
    finally {
        $env:OFFERPILOT_DATA = $previousData
    }
}

Write-Host "Local smoke passed at http://127.0.0.1:$Port"
