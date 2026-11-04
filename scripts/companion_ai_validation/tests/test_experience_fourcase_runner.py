"""Product HTTP to actual broker and durable ledger, fake upstream only."""
import json
import time
from types import SimpleNamespace

import pytest
from scripts.companion_ai_validation import broker as b, run as r
from scripts.companion_ai_validation import experience_fourcase_driver as driver
from scripts.companion_ai_validation import experience_fourcase_continuation as exp
from scripts.companion_ai_validation.tests.test_experience_runner import reply


@pytest.fixture(autouse=True)
def offline_clock(monkeypatch):
    monkeypatch.setattr(b, "time", SimpleNamespace(
        time=lambda: exp.PRICE_VALID_UNTIL - 3600, monotonic=time.monotonic))


@pytest.mark.parametrize("failure", [None, "length", "tool_calls", "unknown"])
def test_exact_transport_ledger_or_first_failure_halts(tmp_path, failure):
    sent = []
    def fake(body):
        payload = b.decode(body)
        sent.append(payload)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 512 and payload["stream"] is False
        assert payload["tool_choice"] == "auto" and payload["tools"]
        if failure == "unknown":
            raise OSError("synthetic unknown send outcome")
        response = b.decode(reply(content="合成普通回复。", finish=failure or "stop"))
        if failure == "tool_calls":
            response["choices"][0]["message"]["tool_calls"] = [{"id": "fake", "type": "function", "function": {"name": "fake", "arguments": "{}"}}]
        return 200, b.encode(response)
    output = tmp_path / "evidence"
    report = r.execute(fake, "d" * 40, output, experience_fourcase=True)
    assert report["matrix"]["passed"] is (failure is None), report
    assert len(sent) == (4 if failure is None else 1)
    state = report["budget"]
    assert state["prior_request_count"] == 78
    assert state["prior_settled_micro_cny"] == 598118
    assert state["lifetime_request_count"] == 78 + len(sent)
    assert state["lifetime_reserved_micro_cny"] == (0 if failure is None else 3000000)
    ledger = [json.loads(row) for row in (output / "budget.jsonl").read_text().splitlines()]
    assert ledger[-1]["event"] == "closed" and ledger[-1]["halted"] is True
    assert [row["event"] for row in ledger].count("reserved") == len(sent)
    if failure is None:
        assert state["stages_complete"] == 4 and state["settled_micro_cny"] == 598118 + 4 * 360
        assert [p["messages"][-1]["content"] for p in sent] == list(driver.TEXTS)
        assert [("persona_profile" in json.dumps(p["messages"], ensure_ascii=False)) for p in sent] == [False, True, False, False]
        assert [len([m for m in p["messages"] if m["role"] == "assistant"]) for p in sent] == [0, 1, 2, 0]
    else:
        assert state["halted"] is True and state["stages_complete"] == 0
        assert (output / "responses.jsonl").read_bytes() == b""
