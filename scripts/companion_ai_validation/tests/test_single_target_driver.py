"""Real durable Broker with fake transport only; no provider credentials or calls."""

import copy
import hashlib
import json

import pytest

from experiments.shadow_single_target import adapter
from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import shadow_single_target_driver as driver


def assessment(row, fact_gate="eligible", relation="supported"):
    states = {
        "eligible": "decided",
        "nonfactual": "not_executed",
        "out_of_scope": "abstain",
        "split_required": "split",
    }
    return {
        "id": row["id"],
        "fact_gate": fact_gate,
        "surface_form_tags": ["declarative"],
        "speech_act_tags": [],
        "conditionality": "unspecified",
        "tag_uncertainty": "",
        "interpretation": "Synthetic transport fixture; not a semantic judgment",
        "alternatives": [],
        "relation_expectation": relation,
        "deliverable_relation": None,
        "decision_state": states[fact_gate],
        "reason": "Unreviewed fake test only",
        "citations": [
            {
                **q,
                "mechanical": "pass",
                "relevance": "ungraded",
                "entailment": "ungraded",
                "sufficiency": "ungraded",
            }
            for q in row["candidate_citations"]
        ],
        "review_status": "unresolved",
        "children": [],
    }


def fixture_content(case):
    row = case["input"]
    operation = case["operation"]
    kind, relation = {
        "t01": ("eligible", "supported"),
        "t02": ("nonfactual", None),
        "t03": ("eligible", "supported"),
        "t04": ("eligible", "insufficient"),
        "t05": ("out_of_scope", None),
        "t06": ("split_required", None),
    }[operation]
    value = assessment(row, kind, relation)
    if operation == "t06":
        split = row["target"].index("，") + 1
        for i, (start, end, k, r) in enumerate(
            [(0, split, "eligible", "supported"), (split, len(row["target"]), "nonfactual", None)],
            1,
        ):
            child = assessment(row, k, r)
            child.pop("children")
            child["id"] = row["id"] + "." + str(i)
            value["children"].append(
                {
                    "target": row["target"][start:end],
                    "target_span": [start, end],
                    "shared_scope_spans": [],
                    "assessment": child,
                }
            )
    request = adapter.build_request(adapter.encode(row))
    return {"version": adapter.VERSION, "request_id": request.request_id, "assessment": value}


def envelope(content):
    return {
        "model": gate.MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": adapter.encode(content).decode()},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 100,
            "total_tokens": 200,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        },
    }


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(gate.time, "time", lambda: 1793420000.0)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 1000.0)


def run_fake(tmp_path, change=None):
    calls = []
    fixed = driver.cases()

    def send(raw):
        case = fixed[len(calls)]
        calls.append(raw)
        result = envelope(fixture_content(case))
        if change:
            result = change(result)
        return 200, result if isinstance(result, bytes) else gate.encode(result)

    result = driver.execute(
        send, "a" * 40, tmp_path / "evidence", window=gate.RunWindow(shadow_single_target=True)
    )
    return result, calls


def test_real_broker_six_once_and_raw_evidence(tmp_path, fixed_clock):
    report, calls = run_fake(tmp_path)
    assert report["matrix"]["passed"] is True
    assert len(calls) == 6
    assert report["budget"]["prior_request_count"] == 58
    assert report["budget"]["lifetime_request_count"] == 64
    assert report["budget"]["prior_settled_micro_cny"] == 429280
    assert report["budget"]["reserved_micro_cny"] == 0
    assert report["experiment"]["semantic_quality_established"] is False
    lines = [
        json.loads(x) for x in (tmp_path / "evidence/requests.jsonl").read_bytes().splitlines()
    ]
    assert [x["body_sha256"] for x in lines] == [hashlib.sha256(x).hexdigest() for x in calls]
    assert all(gate.decode(x)["max_tokens"] == 4096 for x in calls)
    assert len((tmp_path / "evidence/responses.jsonl").read_bytes().splitlines()) == 6


@pytest.mark.parametrize(
    "mode",
    [
        "truncated",
        "wrong_id",
        "wrong_request_id",
        "extra_hash",
        "invalid_json",
        "tool",
        "finish",
        "usage",
        "over_cap",
        "semantic",
    ],
)
def test_real_broker_failstop(tmp_path, fixed_clock, mode):
    def change(response):
        if mode == "invalid_json":
            return b"{"
        if mode == "usage":
            response.pop("usage")
        elif mode == "over_cap":
            response["usage"].update(completion_tokens=4097, total_tokens=4197)
        elif mode == "tool":
            response["choices"][0]["message"]["tool_calls"] = [{"id": "bad"}]
        elif mode == "finish":
            response["choices"][0]["finish_reason"] = "length"
        else:
            message = response["choices"][0]["message"]
            value = json.loads(message["content"])
            if mode == "wrong_id":
                value["assessment"]["id"] = "item_ffffffffffffffff"
            elif mode == "wrong_request_id":
                value["request_id"] = "req_" + "0" * 32
            elif mode == "extra_hash":
                value["input_sha256"] = "0" * 64
            elif mode == "semantic":
                value["assessment"]["relation_expectation"] = "insufficient"
            message["content"] = adapter.encode(value).decode()
            if mode == "truncated":
                message["content"] = message["content"][:-5]
        return response

    report, calls = run_fake(tmp_path, change)
    assert not report["matrix"]["passed"]
    assert len(calls) == 1
    assert report["experiment"]["not_attempted"] == 5
    assert report["budget"]["halted"]
    if mode == "semantic":
        assert report["matrix"]["error_code"] == "semantic_disagreement"
        assert report["matrix"]["cases"][0]["status"] == "structure_valid_semantic_disagreement"
        assert report["budget"]["reserved_micro_cny"] == 0


def test_transport_unknown_retains_reservation(tmp_path, fixed_clock):
    calls = []

    def send(raw):
        calls.append(raw)
        raise OSError("unknown transport outcome")

    report = driver.execute(
        send, "a" * 40, tmp_path / "evidence", window=gate.RunWindow(shadow_single_target=True)
    )
    assert len(calls) == 1
    assert report["matrix"]["error_code"] == "upstream_unknown"
    assert report["budget"]["reserved_micro_cny"] == 3000000
    assert report["budget"]["settled_micro_cny"] == 429280


def test_expired_price_zero_send(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.time, "time", lambda: 1793577601.0)
    report, calls = run_fake(tmp_path)
    assert calls == []
    assert report["matrix"]["error_code"] == "price_review_expired"


def test_same_id_revision_stale_response_rejected():
    case = driver.cases()[0]
    request = adapter.build_request(adapter.encode(case["input"]))
    revised = copy.deepcopy(case["input"])
    revised["source_context"]["evaluation_time"] = "later"
    changed = adapter.build_request(adapter.encode(revised))
    assert changed.request_id != request.request_id
    with pytest.raises(adapter.AdapterError, match="response_correlation"):
        adapter.parse_response(adapter.encode(fixture_content(case)), changed)


def test_pinned_request_tamper_before_send(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "ASSETS", tmp_path)
    (tmp_path / "t01.json").write_bytes(b"{}")
    with pytest.raises(gate.GateError, match="shadow_asset_changed"):
        driver.frozen_request("t01")


def test_semantic_disagreement_is_not_protocol_invalid():
    case = driver.cases()[0]
    content = fixture_content(case)
    content["assessment"]["relation_expectation"] = "insufficient"
    parsed = driver.validate(adapter.encode(content).decode(), case)
    assert parsed["semantic_review_required"] is True
    assert not driver.semantic_matches("t01", parsed["reviewer_claims"])


def test_4096_output_is_accepted_by_new_profile(tmp_path, fixed_clock):
    def change(response):
        response["usage"].update(completion_tokens=4096, total_tokens=4196)
        return response

    report, calls = run_fake(tmp_path, change)
    assert report["matrix"]["passed"]
    assert len(calls) == 6


def test_new_profile_rejects_seventh_stage(tmp_path, fixed_clock):
    cases = driver.cases()
    calls = []

    def send(raw):
        case = cases[len(calls)]
        calls.append(raw)
        return 200, gate.encode(envelope(fixture_content(case)))

    broker = gate.Broker(
        tmp_path / "budget.jsonl",
        send,
        "a" * 40,
        window=gate.RunWindow(shadow_single_target=True),
        shadow_single_target=True,
    )
    try:
        for case in cases:
            token = broker.begin_stage(case["operation"])
            broker.request(token, driver.frozen_request(case["operation"]))
            broker.close_stage()
        with pytest.raises(gate.GateError, match="gate_closed"):
            broker.begin_stage("t07")
        assert len(calls) == 6
    finally:
        broker.close()


def test_budget_insufficient_reserve_zero_send(tmp_path, fixed_clock):
    calls = []
    broker = gate.Broker(
        tmp_path / "budget.jsonl",
        lambda raw: calls.append(raw),
        "a" * 40,
        window=gate.RunWindow(shadow_single_target=True),
        shadow_single_target=True,
    )
    try:
        # White-box boundary injection, never a caller-supplied live opening balance.
        broker._state["settled_micro_cny"] = gate.BUDGET - gate.RESERVATION + 1
        token = broker.begin_stage("t01")
        with pytest.raises(gate.GateError, match="budget_exhausted"):
            broker.request(token, driver.frozen_request("t01"))
        assert calls == []
        assert broker.summary()["halted"]
    finally:
        broker.close()


def test_preregistration_tamper_blocks_before_transport(tmp_path, monkeypatch):
    import shutil

    assets = tmp_path / "assets"
    shutil.copytree(driver.ASSETS, assets)
    (tmp_path / "semantic-preregistration.json").write_bytes(b"{}")
    monkeypatch.setattr(driver, "ASSETS", assets)
    calls = []
    with pytest.raises(gate.GateError, match="shadow_asset_changed"):
        driver.execute(
            lambda raw: calls.append(raw),
            "a" * 40,
            tmp_path / "evidence",
            window=gate.RunWindow(shadow_single_target=True),
        )
    assert calls == []
    assert not (tmp_path / "evidence").exists()


def test_split_preregistration_compares_source_order_not_response_array_order():
    case = driver.cases()[-1]
    content = fixture_content(case)
    children = content["assessment"]["children"]
    children.reverse()
    for index, child in enumerate(children, 1):
        child["assessment"]["id"] = case["input"]["id"] + "." + str(index)
    parsed = driver.validate(adapter.encode(content).decode(), case)
    assert driver.semantic_matches("t06", parsed["reviewer_claims"])
    assert parsed["reviewer_claims"]["children"][0]["target_span"][0] > 0
    children[0]["assessment"].update(
        fact_gate="eligible", decision_state="decided", relation_expectation="supported"
    )
    parsed = driver.validate(adapter.encode(content).decode(), case)
    assert not driver.semantic_matches("t06", parsed["reviewer_claims"])


@pytest.mark.parametrize("span,allowed", [([0, 8], True), ([1, 8], False)])
def test_split_only_preregistered_separator_variants_allow_no_factual_word_loss(span, allowed):
    case = driver.cases()[-1]
    content = fixture_content(case)
    child = content["assessment"]["children"][0]
    child["target_span"] = span
    child["target"] = case["input"]["target"][span[0] : span[1]]
    parsed = driver.validate(adapter.encode(content).decode(), case)
    assert driver.semantic_matches("t06", parsed["reviewer_claims"]) is allowed
