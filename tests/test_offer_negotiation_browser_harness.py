from __future__ import annotations

import re
from pathlib import Path


HARNESS = Path(__file__).parents[1] / "scripts" / "offer-negotiation-real-ai-browser-harness.ps1"


def test_offer_negotiation_harness_isolated_and_fail_closed() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "OFFER_NEGOTIATION_CDP_URL" in script
    assert "browser-network-audit.py" in script
    assert "provider-egress-proxy.py" in script
    assert "finally" in script
    assert "offer_negotiation_provider_error" in script
    assert "offer_negotiation_unverifiable" in script
    assert "offer_negotiation_proposal" in script
    assert "offer-negotiation/proposals" in script
    assert "Chat" in script
    assert "throw" in script


def test_harness_output_is_ascii_and_checks_the_complete_browser_sequence() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    output_lines = [
        line
        for line in script.splitlines()
        if re.search(r"\b(?:Write-Host|throw)\b", line)
    ]
    assert all(line.isascii() for line in output_lines)
    for marker in (
        "comparison",
        "negotiation/proposals",
        "/confirm",
        "history",
        "provider_request_id",
        "source_changed",
    ):
        assert marker in script
    assert "response_proposal_id" in script
    assert "response_confirmed_proposal_id" in script


def test_harness_does_not_classify_502_by_status_alone() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "offer_negotiation_provider_error" in script
    assert "offer_negotiation_unverifiable" in script
    assert "status -eq 502" not in script
    assert "error_code" in script


def test_harness_uses_real_chat_and_cross_domain_tables() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert '"chat_messages"' in script
    assert '"messages"' not in script
    for table in (
        '"application_events"',
        '"application_evidence_bundles"',
        '"opportunity_fit_review_sessions"',
        '"opportunity_fit_review_stages"',
        '"interview_notes"',
        '"interview_preparation_proposals"',
        '"mock_interview_review_drafts"',
        '"wakeups"',
    ):
        assert table in script
    assert '"sha256"' in script


def test_browser_audit_records_pending_response_payloads() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "browser-network-audit.py").read_text(encoding="utf-8")
    assert '"response_attempt_status"' in script
    assert '"response_retry_after_ms"' in script
    assert '"payload_sha256"' in script
    assert "if int(status or 0) >= 400" not in script
    assert "asyncio.create_task(self.record_response(message))" in script
    assert "Network.loadingFinished" in script
    assert "wait_for" in script


def test_harness_verifies_browser_history_and_same_payload_provider_retry() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert 'response_status -eq 200' in script
    assert 'payload_sha256' in script
    assert 'original key' in script
    assert 'negotiation/proposals"' in script
    assert 'proposal $proposalId history' not in script


def test_harness_uses_nullable_diagnostic_presence_and_active_provider_order() -> None:
    script = HARNESS.read_text(encoding="utf-8")
    assert "PSObject.Properties.Name" in script
    assert "active_provider_id" in script
    assert "fallback_provider_ids" in script
    assert "providersById.Count -eq 0" in script
    assert "expectedProposalIds" in script
    assert "Get-ProviderEndpoints" in script
    assert "expected-endpoints-file" in script
    proxy = (Path(__file__).parents[1] / "scripts" / "provider-egress-proxy.py").read_text(encoding="utf-8")
    assert "utf-8-sig" in proxy


def test_offer_real_ai_smoke_previews_before_generation() -> None:
    smoke = (Path(__file__).parents[1] / "src" / "offerpilot" / "smoke.py").read_text(encoding="utf-8")
    start = smoke.index("def run_offer_negotiation_real_ai_smoke")
    section = smoke[start:]
    assert "/negotiation/preview" in section
    assert 'payload["source_fingerprint"]' in section
