"""Four-case broker negative boundaries, never a real provider."""
import pytest
import time
from types import SimpleNamespace
from copy import deepcopy
from scripts.companion_ai_validation import broker as b
from scripts.companion_ai_validation import experience_fourcase_continuation as exp
@pytest.fixture(scope="module")
def verified_lineage():
    # Full real replay once. Archive-corruption tests remain separate/unpatched.
    return exp.lineage_fields()


@pytest.fixture(autouse=True)
def offline_broker_clock(monkeypatch, verified_lineage):
    monkeypatch.setattr(exp, "lineage_fields", lambda: deepcopy(verified_lineage))
    # Test-only anchor; live deadline and wall clock stay fail-closed.
    monkeypatch.setattr(b, "time", SimpleNamespace(
        time=lambda: exp.PRICE_VALID_UNTIL - 3600, monotonic=time.monotonic,
    ))


def test_exact_five_requests_closes_all_new_allowance(tmp_path):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    upstream = Fake()
    broker = b.Broker(tmp_path / "ledger", upstream, "d" * 40, experience_fourcase=True)
    try:
        assert broker._max_requests == 4
        for operation in exp.OPERATIONS:
            token = broker.begin_stage(operation)
            broker.request(token, b.encode(payload(operation)))
            broker.close_stage()
        with pytest.raises(b.GateError, match="gate_closed"):
            broker.begin_stage("x12")
        state = broker.summary()
        assert len(upstream.calls) == 4 and state["lifetime_request_count"] == 82
        assert state["lifetime_reserved_micro_cny"] == 0
    finally:
        broker.close()


def test_expired_quote_still_rejects_before_fake_send(tmp_path, monkeypatch):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    upstream = Fake()
    broker = b.Broker(tmp_path / "ledger", upstream, "d" * 40, experience_fourcase=True)
    try:
        token = broker.begin_stage(exp.OPERATIONS[0])
        monkeypatch.setattr(b.time, "time", lambda: exp.PRICE_VALID_UNTIL)
        with pytest.raises(b.GateError, match="price_review_expired"):
            broker.request(token, b.encode(payload(exp.OPERATIONS[0])))
        assert upstream.calls == []
        assert broker.summary()["halted"]
        assert broker.summary()["reserved_micro_cny"] == 0
    finally:
        broker.close()


@pytest.mark.parametrize("anomaly", ["budget", "unknown", "status", "second_request"])
def test_retest_budget_unknown_status_and_extra_call_fail_closed(tmp_path, anomaly):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    upstream = Fake()
    if anomaly == "status":
        upstream.status = 503
    broker = b.Broker(tmp_path / "ledger", upstream, "d" * 40, experience_fourcase=True)
    token = broker.begin_stage(exp.OPERATIONS[0])
    if anomaly == "budget":
        broker._state["settled_micro_cny"] = b.BUDGET - b.RESERVATION + 1
    if anomaly == "unknown":
        def unknown(raw):
            upstream.calls.append(raw)
            raise OSError("synthetic transport failure")
        broker._send = unknown
    try:
        if anomaly == "second_request":
            broker.request(token, b.encode(payload(exp.OPERATIONS[0])))
        with pytest.raises(b.GateError):
            broker.request(token, b.encode(payload(exp.OPERATIONS[0])))
        with pytest.raises(b.GateError):
            broker.begin_stage(exp.OPERATIONS[1])
        assert len(upstream.calls) == (0 if anomaly == "budget" else 1)
        assert broker.summary()["halted"]
        if anomaly in {"unknown", "status"}:
            assert broker.summary()["reserved_micro_cny"] == b.RESERVATION
    finally:
        broker.close()



@pytest.mark.parametrize("field", ["search", "fetch", "web_search", "extra_body", "thinking"])
def test_provider_extensions_denied_before_send(tmp_path, field):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    fake = Fake()
    broker = b.Broker(tmp_path / "ledger", fake, "d" * 40, experience_fourcase=True)
    try:
        token = broker.begin_stage(exp.OPERATIONS[0])
        body = {**payload(), field: {"enabled": True}}
        with pytest.raises(b.GateError, match="unexpected_request_field"):
            broker.request(token, b.encode(body))
        assert fake.calls == [] and broker.summary()["halted"] is True
    finally:
        broker.close()


@pytest.mark.parametrize("mode", ["experience_retest", "experience", "shadow_review", "shadow_quote_v2", "shadow_projection", "shadow_single_target"])
def test_new_profile_cannot_mix_with_old(mode):
    with pytest.raises(b.GateError, match="experiment_mode"):
        b.profile_lineage(experience_fourcase=True, **{mode: True})
