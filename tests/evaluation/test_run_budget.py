from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from offerpilot.evaluation.core_subject import RunLedger
from offerpilot.evaluation.core_runner import claim_grant


def test_fee_reservation_blocks_before_ack_and_unknown_usage_is_not_refunded(tmp_path: Path):
    profile = SimpleNamespace(input_cny_per_million=10, output_cny_per_million=10)
    manifest = SimpleNamespace(mode="real", provider_profile=profile, run_max_requests=100)
    grant = SimpleNamespace(max_requests=2, max_cny=0.02)
    ledger = RunLedger(manifest, grant, tmp_path)
    event = dict(
        purpose="provider_send", input_bytes=1000, output_cap=1000, trial_key={"id": "trial"}, seq=1
    )
    ledger.reserve(event)
    with pytest.raises(TimeoutError):
        ledger.reserve(event | {"seq": 2})
    assert ledger.requests == 1 and str(ledger.reserved_cny) == "0.02"
    assert ledger.rows[-1]["allowed"] is False
    assert len((tmp_path / "run-budget.jsonl").read_text().splitlines()) == 2


def test_copied_grant_cannot_be_consumed_from_a_second_directory(tmp_path, monkeypatch):
    from offerpilot.evaluation import core_runner

    monkeypatch.setattr(core_runner, "authority_home", lambda: tmp_path / "native-account")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "global-state"))
    paths = []
    for folder in ("first", "copy"):
        p = tmp_path / folder / "grant.json"
        p.parent.mkdir()
        p.write_text("synthetic grant")
        paths.append(p)
    grant = SimpleNamespace(grant_id=uuid4())
    claim_grant(grant, paths[0], "run-one")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "different-codex-home"))
    with pytest.raises(FileExistsError):
        claim_grant(grant, paths[1], "run-two")


def test_native_authority_does_not_change_with_caller_home_variables(tmp_path, monkeypatch):
    from offerpilot.evaluation.core_runner import authority_home

    original = authority_home()
    for name in ("CODEX_HOME", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.setenv(name, str(tmp_path / "untrusted-home"))
    assert authority_home() == original


def test_grant_expiration_is_checked_before_every_send(tmp_path):
    profile = SimpleNamespace(input_cny_per_million=2, output_cny_per_million=8)
    manifest = SimpleNamespace(mode="real", provider_profile=profile, run_max_requests=100)
    grant = SimpleNamespace(
        max_requests=100, max_cny=10, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    ledger = RunLedger(manifest, grant, tmp_path)
    with pytest.raises(TimeoutError):
        ledger.reserve(
            dict(purpose="provider_send", input_bytes=1000, output_cap=1024, trial_key={}, seq=1)
        )
    assert ledger.requests == 0 and ledger.reserved_cny == 0
