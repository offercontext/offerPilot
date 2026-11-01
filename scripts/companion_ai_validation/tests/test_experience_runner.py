"""Real runtime + actual one-shot broker, with a strictly fake upstream."""
import json
from copy import deepcopy
import pytest
from scripts.companion_ai_validation import broker as b, run as r
from scripts.companion_ai_validation import experience_candidate as candidate
from scripts.companion_ai_validation import experience_continuation as exp
from scripts.companion_ai_validation.tests import test_experience_preflight as pf
from scripts.companion_ai_validation.tests import test_companion_ai_preflight as old


def reply(content="合成简短回复，仅验证机制。", finish="stop"):
    return b.encode({"model": b.MODEL, "choices": [{"index": 0, "finish_reason": finish,
        "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100}})


def test_experience_candidate_objects_exact():
    candidate.validate_objects(candidate.OBJECTS)
    for name in candidate.OBJECTS:
        with pytest.raises(b.GateError, match="product_candidate_mismatch"):
            candidate.validate_objects({**candidate.OBJECTS, name: "0" * 40})
    with pytest.raises(b.GateError):
        candidate.validate_objects({**candidate.OBJECTS, "extra": "x"})


@pytest.mark.parametrize("mode", ["shadow_review", "shadow_quote_v2", "shadow_projection", "shadow_single_target"])
def test_experience_never_combines_other_modes(mode):
    with pytest.raises(b.GateError):
        b.profile_lineage(experience=True, **{mode: True})
    with pytest.raises(b.GateError):
        b.RunWindow(experience=True, **{mode: True})


def test_new_admission_is_not_old_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(pf.p.time, "time", lambda: exp.PRICE_VALID_UNTIL - 3600)
    values = pf.fixture("paid")
    ctx = pf.p.validate_context(values[0], experience=True)
    path = tmp_path / "admission.json"
    pf.p.write_admission(str(path), ctx)
    record = json.loads(path.read_text())
    assert r.check_admission(record, values[0], exp.PRICE_VALID_UNTIL - 3600, experience=True) == old.R
    for flags in ({}, {"shadow_single_target": True}):
        with pytest.raises(b.GateError, match="admission_lineage"):
            r.check_admission(record, values[0], exp.PRICE_VALID_UNTIL - 3600, **flags)
    for field, value in (("prior_request_count", 58), ("prior_settled_micro_cny", 0)):
        changed = dict(record, **{field: value})
        with pytest.raises(b.GateError, match="admission_lineage"):
            r.check_admission(changed, values[0], exp.PRICE_VALID_UNTIL - 3600, experience=True)


@pytest.mark.parametrize("failure", [False, True])
def test_product_runtime_through_broker_twelve_or_fail_stop(tmp_path, failure):
    sent = []
    def upstream(body):
        payload = b.decode(body)
        assert payload["model"] == b.MODEL and payload["max_tokens"] == 512
        assert payload["thinking"] == {"type": "disabled"} and payload["stream"] is False
        assert payload["messages"] and payload["tools"]
        assert "synthetic-acceptance-local-confirmation-only" not in body.decode()
        sent.append(deepcopy(payload))
        return 200, reply(finish="length" if failure else "stop")
    target = tmp_path / "evidence"
    report = r.execute(upstream, "d" * 40, target, experience=True)
    assert report["matrix"]["passed"] is (not failure), report
    assert len(sent) == (1 if failure else 12)
    budget = report["budget"]
    assert budget["prior_request_count"] == 61 and budget["prior_settled_micro_cny"] == 458256
    assert budget["lifetime_request_count"] == 61 + len(sent)
    requests = [json.loads(x) for x in (target / "requests.jsonl").read_text().splitlines()]
    responses = [json.loads(x) for x in (target / "responses.jsonl").read_text().splitlines()]
    assert len(requests) == len(sent) and len(responses) == (0 if failure else 12)
    assert report["product_candidate"]["source_sha"] == candidate.SOURCE_SHA
    assert report["product_candidate"]["web_executed"] is False
    if not failure:
        assert budget["stages_complete"] == 12 and budget["reserved_micro_cny"] == 0
        assert budget["settled_micro_cny"] == 458256 + 12 * 360
        assert [x["operation"] for x in requests] == list(exp.OPERATIONS)
        assert all(x["ordinary_turn_read_only"] and x["source_verified"] for x in responses)
        assert all(x["provenance"]["runtime_budget"] == {"model_calls": 1,
            "title_model_calls": 0, "tool_calls": 0} for x in responses)
    else:
        assert budget["halted"] and budget["stages_complete"] == 0
