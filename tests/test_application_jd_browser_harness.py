from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
HARNESS = ROOT / "scripts" / "application-jd-real-ai-browser-harness.ps1"
AUDIT = ROOT / "scripts" / "browser-network-audit.py"
ALLOWLIST_FILE_ENV = "OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE"


def _changed_paths_since(root: Path, baseline: str) -> set[str]:
    changed: set[str] = set()
    for args in (
        [baseline, "--name-only"],
        [f"{baseline}..HEAD", "--name-only"],
    ):
        result = subprocess.run(
            ["git", "diff", *args], cwd=root, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        changed.update(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip())
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert staged.returncode == 0, staged.stderr
    changed.update(line.strip().replace("\\", "/") for line in staged.stdout.splitlines() if line.strip())
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert untracked.returncode == 0, untracked.stderr
    changed.update(line.strip().replace("\\", "/") for line in untracked.stdout.splitlines() if line.strip())
    return changed


def _approved_allowlist_from_environment() -> frozenset[str]:
    allowlist_file = os.environ.get(ALLOWLIST_FILE_ENV)
    if not allowlist_file:
        pytest.fail(f"release gate must supply {ALLOWLIST_FILE_ENV}")
    paths = tuple(
        line.strip().replace("\\", "/")
        for line in Path(allowlist_file).read_text(encoding="ascii").splitlines()
        if line.strip()
    )
    assert paths and len(paths) == len(set(paths)), "recorded implementation allowlist is invalid"
    return frozenset(paths)


def assert_application_jd_implementation_scope(root: Path, baseline: str) -> None:
    changed = _changed_paths_since(root, baseline)
    unexpected = sorted(changed - _approved_allowlist_from_environment())
    assert not unexpected, f"application JD implementation changed paths outside the tracked allowlist: {unexpected}"


def _load_browser_audit():
    spec = importlib.util.spec_from_file_location("browser_network_audit", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_application_jd_harness_parses_and_uses_explicit_stage_contract() -> None:
    command = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{HARNESS}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { exit 1 }; Write-Output 'parse-ok'"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "parse-ok" in result.stdout
    assert result.stdout.isascii()


def test_application_jd_harness_self_starts_and_cleans_temporary_browser() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "Start-TemporaryBrowser 'about:blank'" in script
    assert "--remote-debugging-port=$($script:browserCdpPort)" in script
    assert "--user-data-dir=$($script:browserProfile)" in script
    assert "Label = 'temporary browser'" in script
    assert "APPLICATION_JD_CDP_URL" in script
    assert "browser-diagnostic.json" in script
    assert "RedirectStandardError" in script
    assert "Wait-AuditorExit" in script


def test_application_jd_harness_fails_closed_on_auditor_disconnect() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "Assert-BrowserAuditorHealthy" in script
    assert "failure_category" in script
    assert "Browser auditor did not exit cleanly" in script
    assert "successful Pilot confirmation response" in script
    assert "/api/chat(?:/stream)?$" in script
    assert "/api/chat/confirm/stream$" in script
    assert "Save-FailedProviderAudit" in script
    assert "FAILED_PROVIDER_AUDIT=" in script
    assert "LITELLM_LOCAL_MODEL_COST_MAP" in script
    assert "/job-description/versions(?:\\?.*)?$" in script
    assert "response_source_kinds" in script
    assert "JD history after Pilot confirmation" in script


def test_application_jd_harness_retains_temp_data_when_a_process_survives() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "$allProcessesStopped = $true" in script
    assert "$allProcessesStopped = $false" in script
    assert "if ($allProcessesStopped)" in script
    assert "temporary directory retained because a child process did not exit" in script


def test_application_jd_harness_does_not_treat_unknown_nested_process_exit_as_failure() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "$null -ne $auditorExitCode" in script


def test_application_jd_harness_detects_new_database_snapshot_tables() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "$before.PSObject.Properties.Name" in script
    assert "$after.PSObject.Properties.Name" in script
    assert "Sort-Object -Unique" in script


def test_application_jd_harness_persists_redacted_stage_input_diagnostics() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE" in script
    assert "OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE" in script
    assert "application_jd_stage_diagnostic.py" in script
    assert "--provider-start-index" in script
    assert "--operation-start-index" in script
    assert "--browser-audit" in script
    assert "FAILURE_REASON=" in script
    assert "audit_offsets" in script
    assert "Save-StageDiagnostic 'triage'" in script
    assert "Save-StageDiagnostic 'material_kit'" in script
    assert "Save-StageDiagnostic 'interview_preparation'" in script


def test_application_jd_harness_preserves_redacted_browser_audit_on_failure() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "Save-FailedBrowserAudit" in script
    assert "failed-browser-audit-" in script


def test_application_jd_harness_keeps_private_triage_context_for_one_exact_replay() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "Get-TriageReplayContext" in script
    assert "triage-replay-context.json" in script
    assert "source_snapshot_json" in script
    assert "candidate_assertions" in script
    assert "same_input_verified" in script
    assert "replay_count" in script
    assert "provider_http_5xx" in script
    assert "Invoke-TriageReplayOnce" in script
    assert script.index("Save-StageDiagnostic 'triage'") < script.rindex("Invoke-TriageReplayOnce")


def test_application_jd_harness_replay_reads_private_context_without_powershell_json_redecode() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "Get-TriageReplayPayload" in script
    assert "APPLICATION_JD_HARNESS_TRIAGE_CONTEXT" in script
    assert "json.dumps(private.get(\"payload\"), ensure_ascii=True" in script
    assert "json.dumps(private.get(\"payload\"), ensure_ascii=False" not in script
    assert 'Get-Content -LiteralPath $triageReplayContextPath -Raw | ConvertFrom-Json' not in script


def test_application_jd_harness_records_only_redacted_replay_error_code() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "Get-TriageReplayErrorCode" in script
    assert "response_error_code" in script
    assert "stage_generation" in script
    assert "lease_valid_before_replay" in script
    assert "provider_call_token_present" in script
    assert "response_body" not in script


def test_application_jd_harness_supports_targeted_triage_replay_mode() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "[ValidateSet('all', 'jd-only', 'triage-only')]" in script
    assert "if ($Stage -eq 'triage-only' -and $consumer -eq 'triage')" in script


def test_application_jd_harness_defers_provider_egress_gate_until_consumers_run() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    stage_a = script.index("Assert-StageA $records")
    consumers = script.index("foreach ($consumer", stage_a)
    assert "Assert-ProviderEgress $providers" not in script[stage_a:consumers]
    assert "Assert-NoProviderCalls $script:providerAuditOffset" in script[stage_a:consumers]


def test_application_jd_harness_only_replays_a_triage_provider_500() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert (
        "'triage' {\n"
        "          Save-StageDiagnostic 'triage'\n"
        "          if ($triageProvider500) {\n"
        "            Invoke-TriageReplayOnce"
    ) in script


def test_application_jd_harness_replay_uses_isolated_flash_model_without_changing_formal_config() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "deepseek-v4-flash" in script
    assert "provider.model = 'deepseek-v4-flash'" in script
    assert "same idempotency key" in script
    assert "at most one" in script
    assert "Remove-Item -LiteralPath $triageReplayContextPath" in script


def test_application_jd_harness_supports_ephemeral_provider_override() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "APPLICATION_JD_PROVIDER_BASE_URL" in script
    assert "APPLICATION_JD_PROVIDER_MODEL" in script
    assert "APPLICATION_JD_PROVIDER_API_KEY" in script
    assert "$providerOverrideBaseUrl" in script
    assert "$providerOverrideModel" in script
    assert "$providerOverrideApiKey" in script


def test_application_jd_harness_audits_loopback_controlled_provider_without_proxy_connect() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "Test-LoopbackProviderEndpoints" in script
    assert "Assert-LoopbackProviderRequests" in script
    assert "if (Test-LoopbackProviderEndpoints $allowedEndpoints)" in script


def test_application_jd_harness_diagnostic_report_is_outside_cleaned_fixture() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "application-jd-stage-diagnostics" in script
    assert "stageDiagnosticReport" in script
    assert "Remove-Item -LiteralPath $stageDiagnosticReport" not in script


def test_application_jd_implementation_scope_is_machine_checked() -> None:
    baseline_file = os.environ.get("OFFERPILOT_APPLICATION_JD_BASELINE_FILE")
    if not baseline_file:
        pytest.fail("release gate must supply OFFERPILOT_APPLICATION_JD_BASELINE_FILE")
    baseline = Path(baseline_file).read_text(encoding="ascii").strip()
    assert baseline
    assert_application_jd_implementation_scope(ROOT, baseline)


def test_release_report_uses_all_consumer_harness_stage() -> None:
    report = (ROOT / "docs" / "reports" / "2026-08-05-application-jd-versions-release-verification.md").read_text(
        encoding="utf-8"
    )
    assert "application-jd-real-ai-browser-harness.ps1 -Stage all" in report
    assert "application-jd-real-ai-browser-harness.ps1 -Stage jd-only" not in report


def test_browser_audit_keeps_url_application_id_authoritative() -> None:
    module = _load_browser_audit()
    audit = module.BrowserAudit(None, Path("audit.jsonl"), Path("stop"))
    audit.target_sessions["target"] = "session"
    audit.handle = io.StringIO()
    message = {
        "sessionId": "session",
        "params": {
            "request": {
                "method": "POST",
                "url": "http://127.0.0.1:8000/api/applications/10/job-description/versions",
                "postData": json.dumps({"application_id": 11, "jd_text": "JD"}),
            }
        },
    }

    asyncio.run(audit.record_request(message))

    record = json.loads(audit.handle.getvalue())
    assert record["request_context"]["application_id"] == 10
    assert record["request_context"]["payload_application_id"] == 11


def test_browser_audit_extracts_consumer_jd_version_from_direct_response_payload() -> None:
    module = _load_browser_audit()
    record: dict[str, object] = {}

    module.record_response_payload_metadata(
        record,
        {
            "stage": "triage",
            "jd_version_id": 23,
            "stage_status": "ready",
        },
    )

    assert record["response_jd_version_id"] == 23


def test_browser_audit_extracts_jd_sources_from_history_payload() -> None:
    module = _load_browser_audit()
    record: dict[str, object] = {}

    module.record_response_payload_metadata(
        record,
        [
            {"id": 24, "source_kind": "pilot"},
            {"id": 23, "source_kind": "ui"},
        ],
    )

    assert record["response_jd_version_ids"] == [24, 23]
    assert record["response_source_kinds"] == ["pilot", "ui"]
