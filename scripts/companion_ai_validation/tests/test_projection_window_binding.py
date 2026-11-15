"""Every profile must use its own immutable clock anchor, including equal deadlines."""
import pytest
from scripts.companion_ai_validation import broker as gate

PROFILES = ({}, {"shadow_review": True}, {"shadow_quote_v2": True}, {"shadow_projection": True})

@pytest.mark.parametrize("requested", PROFILES)
@pytest.mark.parametrize("anchored", PROFILES)
def test_clock_anchor_profile_matches_exactly(tmp_path, requested, anchored):
    if requested == anchored:
        assert gate.RunWindow(**anchored).price_valid_until > 0
        return
    with pytest.raises(gate.GateError, match="continuation_contract_mismatch"):
        gate.Broker(tmp_path / "must-not-exist.jsonl", lambda _: pytest.fail("send"), "a" * 40,
                    window=gate.RunWindow(**anchored), **requested)
    assert not (tmp_path / "must-not-exist.jsonl").exists()
