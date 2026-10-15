param(
    [switch]$RealAi,
    [switch]$Docker,
    [switch]$Install,
    [int]$Port = 18765,
    [string]$EvalSuite,
    [string]$EvalPolicy,
    [string]$EvalVariants,
    [string]$EvalOutput,
    [string]$EvalBaselineId,
    [string]$EvalBaselineStore,
    [string]$EvalReview,
    [string]$EvalExperiment,
    [string]$EvalProviderProfile,
    [string]$EvalGrant,
    [string[]]$EvalReuse,
    [double]$EvalTimeoutSeconds = 0,
    [switch]$IsolateBackendTests,
    [string]$BackendOutput
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
Push-Location $Repo
try {
    if ($IsolateBackendTests -and -not $BackendOutput) { throw '-BackendOutput is required with -IsolateBackendTests.' }
    if ($BackendOutput -and -not $IsolateBackendTests) { throw '-BackendOutput requires -IsolateBackendTests.' }
    $evalValues = @($EvalSuite, $EvalPolicy, $EvalVariants, $EvalOutput, $EvalBaselineId, $EvalBaselineStore, $EvalReview, $EvalExperiment)
    if (($evalValues | Where-Object { $_ }).Count -or $EvalProviderProfile -or $EvalGrant -or $EvalReuse -or $EvalTimeoutSeconds) {
        if ($EvalTimeoutSeconds -lt 0 -or [double]::IsNaN($EvalTimeoutSeconds) -or [double]::IsInfinity($EvalTimeoutSeconds)) { throw "Positive finite eval timeout required." }
        if (($evalValues | Where-Object { -not $_ }).Count) { throw "Complete evaluation parameters are required before release checks." }
        $evalArgs = @('--suite', $EvalSuite, '--policy', $EvalPolicy, '--variants', $EvalVariants, '--output', $EvalOutput, '--baseline-id', $EvalBaselineId, '--baseline-store', $EvalBaselineStore, '--review', $EvalReview, '--experiment', $EvalExperiment)
        if ($EvalProviderProfile) { $evalArgs += @('--provider-profile', $EvalProviderProfile) }
        if ($EvalGrant) { $evalArgs += @('--grant', $EvalGrant) }
        foreach ($run in $EvalReuse) { $evalArgs += @('--reuse', $run) }
        uv run oc eval release-preflight @evalArgs
        if ($LASTEXITCODE -ne 0) { throw "Evaluation preflight failed (exit $LASTEXITCODE)." }
        if (-not (Test-Path -LiteralPath (Join-Path $EvalOutput 'release-preflight.json') -PathType Leaf)) { throw "Evaluation preflight artifact missing." }
    }
    if ($IsolateBackendTests) {
        uv run python -m scripts.test_selection.backend_gate --output $BackendOutput
        if ($LASTEXITCODE -ne 0) { throw "Full isolated backend tests failed (exit $LASTEXITCODE)." }
        $backendReceipt = uv run python -m scripts.test_selection.backend_gate --output $BackendOutput --verify
        if ($LASTEXITCODE -ne 0 -or (($backendReceipt -join "`n").Trim() -notmatch '^BACKEND_FULL_VERIFIED [a-f0-9]{64}$')) { throw 'Full backend execution receipt missing or invalid.' }
    } else {
        uv run pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed (exit $LASTEXITCODE)." }
    }
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

    if ($EvalSuite) {
        $preflight = Join-Path $EvalOutput 'release-preflight.json'
        $executeArgs = @('--preflight', $preflight)
        if ($EvalTimeoutSeconds -ne 0) { $executeArgs += @('--timeout-seconds', $EvalTimeoutSeconds) }
        uv run oc eval release-execute @executeArgs
        if ($LASTEXITCODE -ne 0) { throw "Evaluation gate failed (exit $LASTEXITCODE)." }
        if (-not (Test-Path -LiteralPath (Join-Path $EvalOutput 'release-evaluation.json') -PathType Leaf)) { throw "Evaluation receipt missing." }
        $receipt = uv run oc eval release-receipt --preflight $preflight
        if ($LASTEXITCODE -ne 0 -or (($receipt -join "`n").Trim() -notmatch '^EVAL_ENFORCE_VERIFIED [a-f0-9]{64}$')) { throw "Evaluation enforce verification missing." }
    }
    Write-Host "Release gate passed"
}
finally {
    Pop-Location
}
