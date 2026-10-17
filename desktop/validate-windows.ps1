param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Run this validation on Windows x64." }
$Repo = Split-Path -Parent $PSScriptRoot
Push-Location $Repo
$oldLocalCosts = $env:LITELLM_LOCAL_MODEL_COST_MAP
$oldSigning = $env:CSC_IDENTITY_AUTO_DISCOVERY
$oldOnnx = $env:ONNXRUNTIME_NODE_INSTALL
$oldUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = "1"
    $env:LITELLM_LOCAL_MODEL_COST_MAP = "True"
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    # Browser WASM is used by the SPA; do not download an unused CUDA runtime.
    $env:ONNXRUNTIME_NODE_INSTALL = "skip"
    if (-not $SkipInstall) {
        uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw "Frozen Python install failed." }
        npm.cmd ci --prefix web
        if ($LASTEXITCODE -ne 0) { throw "Frontend install failed." }
        npm.cmd ci --prefix desktop
        if ($LASTEXITCODE -ne 0) { throw "Desktop install failed." }
    }
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release-gate.ps1 -Install
    if ($LASTEXITCODE -ne 0) { throw "Repository release gate failed." }
    npm.cmd test --prefix desktop
    if ($LASTEXITCODE -ne 0) { throw "Desktop lifecycle tests failed." }
    uv run --frozen --with pyinstaller==6.16.0 --with pyinstaller-hooks-contrib==2025.9 python desktop/build-backend.py
    if ($LASTEXITCODE -ne 0) { throw "Frozen backend build failed." }
    uv run --frozen python desktop/smoke-backend.py --backend desktop/backend-dist/offerpilot-backend/offerpilot-backend.exe --static-dir web/dist
    if ($LASTEXITCODE -ne 0) { throw "Frozen backend smoke failed." }
    npm.cmd run build:win --prefix desktop
    if ($LASTEXITCODE -ne 0) { throw "NSIS packaging failed." }
    uv run --frozen python desktop/smoke-backend.py --backend desktop/dist/win-unpacked/resources/backend/offerpilot-backend.exe --static-dir desktop/dist/win-unpacked/resources/web
    if ($LASTEXITCODE -ne 0) { throw "Packaged resource smoke failed." }
    $installers = @(Get-ChildItem -Path desktop/dist -Filter '*-setup.exe')
    if ($installers.Count -eq 0) { throw "No Windows installer produced." }
    foreach ($installer in $installers) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer.FullName).Hash.ToLowerInvariant()
        "$hash  $($installer.Name)" | Set-Content -Encoding ascii -LiteralPath "$($installer.FullName).sha256"
    }
    Write-Host "Unsigned installer and SHA256 files: desktop/dist"
    Write-Host "Installer execution and Electron UI acceptance are still required; see docs/architecture/desktop-validation.md."
}
finally {
    $env:LITELLM_LOCAL_MODEL_COST_MAP = $oldLocalCosts
    $env:CSC_IDENTITY_AUTO_DISCOVERY = $oldSigning
    $env:ONNXRUNTIME_NODE_INSTALL = $oldOnnx
    $env:PYTHONUTF8 = $oldUtf8
    Pop-Location
}
