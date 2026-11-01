"""Fixed five-case retest: real runtime, synthetic data, fake upstream only."""
import json
import subprocess
from copy import deepcopy

import pytest

from scripts.companion_ai_validation import broker as b, run as r
from scripts.companion_ai_validation import experience_retest_candidate as candidate
from scripts.companion_ai_validation import experience_retest_continuation as exp
from scripts.companion_ai_validation import experience_driver as driver
from scripts.companion_ai_validation.tests.test_experience_runner import reply


def test_retest_product_pin_is_new_and_exact():
    actual = {name: subprocess.check_output(["git", "rev-parse", f"HEAD:{name}"],
              cwd=r.ROOT, text=True).strip() for name in candidate.OBJECTS}
    candidate.validate_objects(actual)
    assert candidate.SOURCE_SHA == "e570fd170cc476324d0580bed894cc2c394f63b1"
    for name in candidate.OBJECTS:
        with pytest.raises(b.GateError, match="product_candidate_mismatch"):
            candidate.validate_objects({**candidate.OBJECTS, name: "0" * 40})


@pytest.mark.parametrize("mode", ["experience", "shadow_review", "shadow_quote_v2", "shadow_projection", "shadow_single_target"])
def test_retest_cannot_mix_any_old_mode(mode):
    with pytest.raises(b.GateError):
        b.profile_lineage(experience_retest=True, **{mode: True})
    with pytest.raises(b.GateError):
        b.RunWindow(experience_retest=True, **{mode: True})


@pytest.mark.parametrize("failure", [False, True])
def test_five_runtime_turns_or_truncation_stops_once(tmp_path, failure):
    sent = []
    def upstream(body):
        payload = b.decode(body)
        assert payload["model"] == b.MODEL and payload["max_tokens"] == 512
        assert payload["thinking"] == {"type": "disabled"} and payload["stream"] is False
        assert payload["messages"] and payload["tools"]
        sent.append(deepcopy(payload))
        return 200, reply(content=f"本次新生成的合成回复{len(sent)}。", finish="length" if failure else "stop")
    target = tmp_path / "evidence"
    report = r.execute(upstream, "d" * 40, target, experience_retest=True)
    assert report["matrix"]["passed"] is (not failure), report
    assert len(sent) == (1 if failure else 5)
    budget = report["budget"]
    assert budget["prior_request_count"] == 73 and budget["prior_settled_micro_cny"] == 558124
    assert budget["lifetime_request_count"] == 73 + len(sent)
    requests = [json.loads(x) for x in (target / "requests.jsonl").read_text().splitlines()]
    responses = [json.loads(x) for x in (target / "responses.jsonl").read_text().splitlines()]
    assert len(requests) == len(sent) and len(responses) == (0 if failure else 5)
    assert report["product_candidate"]["source_sha"] == candidate.SOURCE_SHA
    assert report["product_candidate"]["web_executed"] is False
    if failure:
        assert budget["halted"] and budget["stages_complete"] == 0
        return
    assert budget["stages_complete"] == 5 and budget["reserved_micro_cny"] == 0
    assert budget["lifetime_reserved_micro_cny"] == budget["batch_reserved_micro_cny"] == 0
    ledger = [json.loads(line) for line in (target / "budget.jsonl").read_text().splitlines()]
    assert ledger[-1]["event"] == "closed" and ledger[-1]["reserved_micro_cny"] == 0
    assert not any(row["event"] == "halted" or "after_reservation" in row for row in ledger)
    assert budget["settled_micro_cny"] == 558124 + 5 * 360
    assert [x["operation"] for x in requests] == list(exp.OPERATIONS)
    original = {row["id"]: row["text"] for row in driver.cases()}
    assert all(row["user_input"] == original[row["operation"]] for row in responses)
    assert all(row["ordinary_turn_read_only"] and row["source_verified"] for row in responses)
    assert all(row["provenance"]["runtime_budget"] == {
        "model_calls": 1, "title_model_calls": 0, "tool_calls": 0} for row in responses)
    groups = report["matrix"]["groups"]
    assert [group["group"] for group in groups] == ["a", "d", "e"]
    controls = [row for group in groups for row in group["controls"]]
    assert len(controls) == 2 and [row["action"] for row in controls] == ["wait", "answer"]
    assert all(row["runtime_budget"] == {"model_calls": 0, "title_model_calls": 0,
                                         "tool_calls": 0} for row in controls)
    assert len({row["provenance"]["conversation_id"] for row in responses[:3]}) == 1
    for index in (1, 2):
        prior = [message["content"] for message in sent[index]["messages"] if message["role"] == "assistant"]
        assert f"本次新生成的合成回复{index}。" in prior
    assert all("本次新生成" not in json.dumps(payload["messages"], ensure_ascii=False) for payload in sent[3:])
    assert responses[2]["source_facts_before_projection"] is not None
    assert responses[3]["source_facts_before_projection"]["recall_status"] == "source_unavailable"
    assert responses[4]["source_facts_before_projection"]["recall_status"] == "source_unavailable"
    assert "棉花糖号" not in json.dumps(sent[4], ensure_ascii=False)


def test_exact_five_requests_closes_all_new_allowance(tmp_path):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    upstream = Fake()
    broker = b.Broker(tmp_path / "ledger", upstream, "d" * 40, experience_retest=True)
    try:
        assert broker._max_requests == 5
        for operation in exp.OPERATIONS:
            token = broker.begin_stage(operation)
            broker.request(token, b.encode(payload(operation)))
            broker.close_stage()
        with pytest.raises(b.GateError, match="gate_closed"):
            broker.begin_stage("x12")
        state = broker.summary()
        assert len(upstream.calls) == 5 and state["lifetime_request_count"] == 78
        assert state["lifetime_reserved_micro_cny"] == 0
    finally:
        broker.close()


@pytest.mark.parametrize("anomaly", ["budget", "unknown", "status", "second_request"])
def test_retest_budget_unknown_status_and_extra_call_fail_closed(tmp_path, anomaly):
    from scripts.companion_ai_validation.tests.test_companion_ai_broker import Fake, payload
    upstream = Fake()
    if anomaly == "status":
        upstream.status = 503
    broker = b.Broker(tmp_path / "ledger", upstream, "d" * 40, experience_retest=True)
    token = broker.begin_stage("x01")
    if anomaly == "budget":
        broker._state["settled_micro_cny"] = b.BUDGET - b.RESERVATION + 1
    if anomaly == "unknown":
        def unknown(raw):
            upstream.calls.append(raw)
            raise OSError("synthetic transport failure")
        broker._send = unknown
    try:
        if anomaly == "second_request":
            broker.request(token, b.encode(payload("x01")))
        with pytest.raises(b.GateError):
            broker.request(token, b.encode(payload("x01")))
        with pytest.raises(b.GateError):
            broker.begin_stage("x02")
        assert len(upstream.calls) == (0 if anomaly == "budget" else 1)
        assert broker.summary()["halted"]
        if anomaly in {"unknown", "status"}:
            assert broker.summary()["reserved_micro_cny"] == b.RESERVATION
    finally:
        broker.close()


def test_new_admission_checks_new_lineage_and_rejects_old(tmp_path, monkeypatch):
    from scripts.companion_ai_validation.tests import test_experience_retest_preflight as pf
    monkeypatch.setattr(pf.p.time, "time", lambda: exp.PRICE_VALID_UNTIL - 3600)
    values = pf.fixture("paid")
    ctx = pf.p.validate_context(values[0], experience_retest=True)
    path = tmp_path / "admission.json"
    pf.p.write_admission(str(path), ctx)
    record = json.loads(path.read_text())
    assert r.check_admission(record, values[0], exp.PRICE_VALID_UNTIL - 3600,
                             experience_retest=True) == pf.old.R
    for flags in ({}, {"experience": True}):
        with pytest.raises(b.GateError, match="admission_lineage"):
            r.check_admission(record, values[0], exp.PRICE_VALID_UNTIL - 3600, **flags)
    for field, value in (("prior_request_count", 61), ("prior_settled_micro_cny", 0),
                         ("prior_reserved_micro_cny", 1)):
        with pytest.raises(b.GateError, match="admission_lineage"):
            r.check_admission(dict(record, **{field: value}), values[0],
                              exp.PRICE_VALID_UNTIL - 3600, experience_retest=True)


def test_only_offline_time_is_extended_no_paid_protection_changes():
    import yaml
    workflow = yaml.safe_load((r.ROOT / ".github/workflows/companion-real-ai.yml").read_text())
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    jobs = workflow["jobs"]
    assert jobs["offline"]["timeout-minutes"] == 60
    assert jobs["preflight"]["timeout-minutes"] == 5
    assert jobs["paid"]["timeout-minutes"] == 25
    assert jobs["paid"]["environment"] == "offerpilot-companion-ai-20261021"
    assert "OFFERPILOT_COMPANION_DEEPSEEK_20261021" not in json.dumps(jobs["offline"])
    assert "secrets.GITHUB_TOKEN" in json.dumps(jobs["offline"])
    assert (b.REQUEST_SECONDS, b.SESSION_SECONDS, b.BUDGET, b.RESERVATION) == (90, 1200, 20000000, 3000000)
    assert exp.PRICE_VALID_UNTIL == 1793664000
    assert "--experience-retest" in json.dumps(jobs["paid"])
