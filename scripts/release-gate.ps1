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
    uv run ruff check .
    uv run mypy src

    Push-Location (Join-Path $Repo "web")
    try {
        npm.cmd test
        npm.cmd run build
    }
    finally {
        Pop-Location
    }

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -Port $Port
    uv run oc verify --profile local --static-dir web/dist

    if ($RealAi) {
        uv run oc verify --profile real-ai --static-dir web/dist
    }

    if ($Install) {
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-gate.ps1
    }

    if ($Docker) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker was requested but the docker command is not available."
        }
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker-smoke.ps1
    }

    Write-Host "Release gate passed"
}
finally {
    Pop-Location
}
