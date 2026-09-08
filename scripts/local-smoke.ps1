param(
    [int]$Port = 18765,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
if (-not $DataDir) {
    $DataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("offerpilot-local-smoke-" + [System.Guid]::NewGuid().ToString("N"))
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Push-Location (Join-Path $Repo "web")
try {
    npm.cmd run build
}
finally {
    Pop-Location
}

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
    }
    finally {
        Pop-Location
    }

    Write-Host "Local smoke passed at http://127.0.0.1:$Port"
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
        $server.WaitForExit()
    }
    $env:OFFERPILOT_DATA = $previousData
}
