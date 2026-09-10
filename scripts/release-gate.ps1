param(
    [switch]$RealAi,
    [switch]$Docker,
    [switch]$Install,
    [int]$Port = 18765
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
Push-Location $Repo
try {
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed (exit $LASTEXITCODE)." }
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff failed (exit $LASTEXITCODE)." }
    uv run mypy src
    if ($LASTEXITCODE -ne 0) { throw "mypy failed (exit $LASTEXITCODE)." }

    Push-Location (Join-Path $Repo "web")
    try {
        npm.cmd test
        if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed (exit $LASTEXITCODE)." }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)." }
    }
    finally {
        Pop-Location
    }

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -Port $Port -SkipBuild
    if ($LASTEXITCODE -ne 0) { throw "Local smoke failed (exit $LASTEXITCODE)." }
    uv run oc verify --profile local --static-dir web/dist
    if ($LASTEXITCODE -ne 0) { throw "Local verification failed (exit $LASTEXITCODE)." }

    if ($RealAi) {
        uv run oc verify --profile real-ai --static-dir web/dist
        if ($LASTEXITCODE -ne 0) { throw "Real AI verification failed (exit $LASTEXITCODE)." }
    }

    if ($Install) {
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-gate.ps1
        if ($LASTEXITCODE -ne 0) { throw "Install gate failed (exit $LASTEXITCODE)." }
    }

    if ($Docker) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker was requested but the docker command is not available."
        }
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker-smoke.ps1
        if ($LASTEXITCODE -ne 0) { throw "Docker smoke failed (exit $LASTEXITCODE)." }
    }

    Write-Host "Release gate passed"
}
finally {
    Pop-Location
}
