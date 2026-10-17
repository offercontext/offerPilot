"""Active integration routing and installation-smoke safety contracts.

These source checks complement, not replace, the real Windows installation job.
"""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/desktop-windows.yml").read_text(encoding="utf-8")
HISTORICAL = ROOT / ".github/validation/historical-workflows"


def test_mail_native_lock_job_keeps_the_startup_guard_and_locked_dependencies():
    mail = (ROOT / ".github/workflows/job-mail-assistant.yml").read_text(encoding="utf-8")
    native = mail.split("  windows-native-lock:\n", 1)[1]
    synthetic = mail.split("  windows-native-lock:\n", 1)[0]
    # Match this workflow's existing uv pin, not the independent desktop pin.
    setup_uv = "      - uses: astral-sh/setup-uv@v6\n        with:\n          version: '0.8.22'"
    assert setup_uv in native and setup_uv in synthetic
    assert "run: uv sync --frozen" in native
    command = "uv run --frozen pytest --noconftest -q tests/test_job_mail_process_lock.py"
    assert f"run: {command}\n" in native
    assert native.index("uv sync --frozen") < native.index(command)
    assert 'addopts = "-p tests._offline_network_guard"' in (
        ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    for bypass in ("pip install pytest", "-p no:", "-o addopts", "PYTEST_ADDOPTS", "continue-on-error"):
        assert bypass not in native


def test_one_time_workflows_are_inert_historical_fixtures():
    names = {
        "desktop-installed-ui.yml", "desktop-layout-retry.yml", "desktop-real-ai.yml",
        "desktop-browser-cleanup-diagnostic.yml", "haru-ui-preview.yml",
    }
    for name in names:
        assert not (ROOT / ".github/workflows" / name).exists()
        assert (HISTORICAL / name).is_file()
    assert (HISTORICAL / "desktop-windows.yml").is_file()
    assert not (ROOT / ".github/validation/windows-full-request.json").exists()
    assert (ROOT / ".github/validation/historical-requests/windows-full-request.json").is_file()


def test_prs_use_full_read_only_merge_candidate_validation_without_duplicate_candidate_push():
    trigger = WORKFLOW.split("\npermissions:", 1)[0]
    assert "      - feat/20261016-desktop-master-integration\n" not in trigger
    assert trigger.count("      - feat/") == 1
    assert "  pull_request:\n    branches: [master]\n" in trigger
    assert "  workflow_dispatch:\n" in trigger
    assert "paths:" not in trigger and "paths-ignore:" not in trigger
    assert "permissions:\n  contents: read\n" in WORKFLOW
    for forbidden in ("pull_request_target", "secrets.", "environment:", "contents: write",
                      "actions: write", "workflow_run:", "-RealAi", "cancel-in-progress:"):
        assert forbidden not in WORKFLOW
    checkouts = re.findall(
        r"      - uses: actions/checkout@v4\n(.*?)(?=      - |\Z)", WORKFLOW, re.S,
    )
    assert len(checkouts) == 4
    for checkout in checkouts:
        assert "fetch-depth: 0" in checkout
        assert "persist-credentials: false" in checkout
        # Default checkout binds to github.sha: the PR merge candidate, not head.
        assert not re.search(r"^\s+ref:", checkout, re.M)
    assert "github.event.pull_request.head.sha" not in WORKFLOW
    assert "${{ github.sha }}" in WORKFLOW


def test_package_only_escape_hatch_is_bound_to_old_push_branch():
    expression = ("github.event_name == 'push' && "
                  "github.ref == 'refs/heads/feat/20261005-windows-desktop-validation' && "
                  "startsWith(github.event.head_commit.message, 'build: AI [windows-package-only] ')")
    assert WORKFLOW.count(expression) == 4
    assert f'WINDOWS_PACKAGE_ONLY: "${{{{ {expression} }}}}"' in WORKFLOW
    assert "timeout-minutes: 120" in WORKFLOW and "max-parallel: 2" in WORKFLOW
    assert "scripts/release-gate.ps1 -Install -PytestEvidence pytest-evidence" in WORKFLOW


def test_mail_upgrade_and_socketpair_guards_run_in_early_windows_job():
    focused = WORKFLOW.split("      - name: Required desktop, auth and static regression tests\n", 1)[1].split(
        "      - name:", 1,
    )[0]
    command = next(line for line in focused.splitlines() if "uv run --frozen pytest" in line)
    for test_file in (
        "tests/test_desktop.py", "tests/test_auth_api.py", "tests/test_static_frontend.py",
        "tests/test_desktop_job_mail_integration.py", "tests/test_job_mail_master_upgrade.py",
        "tests/test_offline_socketpair_guard.py",
    ):
        assert test_file in command
    assert "if ($LASTEXITCODE -ne 0)" in focused
    assert "npm.cmd test --prefix web -- src/components/AddApplicationForm.test.tsx" in focused
    assert 'if ($LASTEXITCODE -ne 0) { throw "Application intake lifecycle regression failed" }' in focused
    for bypass in ("-p no:", "-o addopts", "-k ", "continue-on-error"):
        assert bypass not in focused


@pytest.mark.parametrize("command", [
    "npm.cmd test --prefix desktop/installed-ui",
    "npm.cmd test --prefix desktop/real-ai-validation",
    "--config desktop/real-ai-validation/test-ui/vitest.config.mjs",
    "desktop/browser-cleanup-diagnostic/test-routing.mjs",
    "desktop/haru-ui-preview/fixture-api.test.cjs",
    "desktop/haru-ui-preview/visual.test.mjs",
    "pytest -q desktop/browser-cleanup-diagnostic/test_diagnostic.py",
])
def test_useful_helper_regressions_are_required_without_live_entrypoints(command):
    assert command in WORKFLOW
    for forbidden in ("node desktop/real-ai-validation/run.mjs", "preflight.mjs --approval",
                      "node desktop/real-ai-validation/prepare.mjs", "run smoke --prefix desktop/installed-ui"):
        assert forbidden not in WORKFLOW


def test_nsis_smoke_is_owned_disposable_and_failure_closed():
    script = (ROOT / "desktop/smoke-install.ps1").read_text(encoding="utf-8")
    for guard in (
        "$env:GITHUB_ACTIONS -ne 'true'", "$env:RUNNER_OS -ne 'Windows'",
        "$env:RUNNER_ENVIRONMENT -ne 'github-hosted'",
        "nsis-smoke-identity.cjs", "RegistryHive]::CurrentUser", "RegistryHive]::LocalMachine",
        "RegistryView]::Registry32", "RegistryView]::Registry64", "Get-ExistingRegistration",
        "$installers.Count -ne 1", "Get-FileHash -Algorithm SHA256", "$env:GITHUB_SHA",
        "[Guid]::NewGuid()", "Smoke destination is not fresh", "ReparsePoint",
        "'/S /currentuser /D=' + $InstallDir", "'/S /currentuser _?=' + $InstallDir",
        "WaitForExit(180000)", "WaitForExit(15000)", "if ($OwnedNsisUnsettled)",
        "Uninstall left product files, profile or shortcut behind",
        "smoke-backend.py", "$env:TEMP = $ProfileRoot",
        "Uninstall OfferPilot Desktop.exe", "Uninstall left installed payload behind",
        "Remove-Item -LiteralPath $TempRoot -Recurse -Force", "throw $primaryFailure",
        "if ($cleanupFailure) { throw $cleanupFailure }",
    ):
        assert guard in script
    assert script.index("@(Get-ExistingRegistration).Count -ne 0") < script.index("$TempRoot =")
    assert "WaitForExit()" not in script
    assert script.count("foreach ($existing in $ExistingPaths)") == 2
    assert "Stop-Process" not in script and "taskkill" not in script
    assert "Remove-Item -LiteralPath $InstallDir" not in script
    assert "secrets." not in script
    # Backend/installer/cleanup must pass before the current installer is uploaded.
    assert WORKFLOW.index("-File desktop/smoke-install.ps1") < WORKFLOW.index("Upload experimental validation installer")
