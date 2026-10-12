from pathlib import Path
from types import SimpleNamespace

import pytest

from offerpilot.evaluation.core_subject import RunLedger


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
