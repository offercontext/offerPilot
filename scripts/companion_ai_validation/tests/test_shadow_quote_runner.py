"""Offline real-adapter contract: no provider or credential access."""
import base64
import hashlib
import json
import math

import pytest

from scripts.companion_ai_validation import broker as gate, run
from scripts.companion_ai_validation import shadow_quote_driver as driver
from scripts.companion_ai_validation import shadow_quote_continuation as lineage
from scripts.companion_ai_validation import shadow_continuation as legacy


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(gate.time, "time", lambda: lineage.PRICE_VALID_UNTIL - 3600)


def content(case):
    return {"protocol": "shadow-quote-v2", "target_id": case["id"], "verdict": "ambiguous",
            "evidence": [{"source_id": "user", "quote": case["sources"][0]["text"]}],
            "rationale": "Offline scripted evidence; not a semantic grade."}


class Fake:
    def __init__(self, fault="none", index=0):
        self.sent = []
        self.fault, self.index = fault, index

    def __call__(self, raw):
        index = len(self.sent)
        self.sent.append(raw)
        value = content(driver.cases()[index])
        result = {"model": gate.MODEL, "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": gate.encode(value).decode()}}], "usage": {
            "prompt_tokens": 100, "completion_tokens": 700, "total_tokens": 800,
            "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100}}
        if index == self.index:
            if self.fault == "unknown":
                raise OSError("synthetic unknown")
            if self.fault == "usage":
                result.pop("usage")
            if self.fault == "tools":
                result["choices"][0]["message"]["tool_calls"] = [{"name": "no_execute"}]
            if self.fault == "target":
                value["target_id"] = "s99"
            if self.fault == "quote":
                value["evidence"][0]["quote"] = "not present"
            if self.fault in {"target", "quote"}:
                result["choices"][0]["message"]["content"] = gate.encode(value).decode()
            if self.fault == "json":
                result["choices"][0]["message"]["content"] = "{"
        return 200, gate.encode(result)


def test_v2_all_six_capture_pinned_requests_and_locate_original_targets(tmp_path):
    fake = Fake()
    out = tmp_path / "run"
    def send(raw):
        journal = [json.loads(line) for line in (out / "budget.jsonl").read_text().splitlines()]
        requests = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
        assert journal[-1]["reserved_micro_cny"] == 3_000_000
        assert requests[-1]["raw_body"].encode() == raw
        return fake(raw)
    report = run.execute(send, "a" * 40, out, shadow_quote_v2=True)
    assert report["matrix"]["passed"]
    assert report["matrix"]["pass_scope"] == "structure_only_not_semantic_correctness"
    assert report["experiment"]["protocol"] == "shadow-quote-v2"
    assert report["budget"]["lifetime_request_count"] == 55
    assert report["budget"]["settled_micro_cny"] == 414284 + 6 * 5800
    assert report["budget"]["reserved_micro_cny"] == 0
    assert fake.sent == [driver.frozen_request(op) for op in lineage.OPERATIONS]
    assert report["matrix"]["cases"][0]["review"]["evidence"][0]["end"] == 51
    for case, state in zip(driver.cases(), report["matrix"]["cases"], strict=True):
        assert state["review"]["target"] == case["target"]
        assert state["review"]["verdict"] == "ambiguous"
        assert state["status"] == "structure_valid_semantics_ungraded"
    rows = [json.loads(line) for line in (out / "responses.jsonl").read_text().splitlines()]
    assert len(rows) == 6
    assert all(hashlib.sha256(base64.b64decode(row["raw_base64"])).hexdigest() == row["body_sha256"] for row in rows)


@pytest.mark.parametrize("fault", ["unknown", "usage", "tools", "target", "quote", "json"])
@pytest.mark.parametrize("index", [0, 2, 5])
def test_v2_failure_stops_without_retry_and_preserves_unknown_reservation(tmp_path, fault, index):
    fake = Fake(fault, index)
    out = tmp_path / "run"
    report = run.execute(fake, "a" * 40, out, shadow_quote_v2=True)
    assert not report["matrix"]["passed"] and len(fake.sent) == index + 1
    assert report["matrix"]["cases"][index]["status"] == "failed"
    assert all(s["status"] == "not_attempted" for s in report["matrix"]["cases"][index + 1:])
    assert report["budget"]["reserved_micro_cny"] == (3_000_000 if fault in {"unknown", "usage", "tools"} else 0)
    assert len((out / "requests.jsonl").read_text().splitlines()) == index + 1
    assert len((out / "responses.jsonl").read_text().splitlines()) == index + (fault != "unknown")


@pytest.mark.parametrize("damage", ["whitespace", "tools", "target", "model", "limit", "old_request"])
def test_v2_only_exact_request_bytes_reserve_and_send(tmp_path, damage):
    fake = Fake()
    broker = gate.Broker(tmp_path / "journal", fake, "a" * 40, shadow_quote_v2=True)
    raw = driver.frozen_request("s01")
    payload = gate.decode(raw)
    if damage == "whitespace":
        raw += b" "
    elif damage == "old_request":
        from scripts.companion_ai_validation.shadow_driver import frozen_request
        raw = frozen_request("s01")
    else:
        if damage == "tools":
            payload["tools"] = []
        elif damage == "target":
            payload["messages"][1]["content"] += "rewrite target"
        elif damage == "model":
            payload["model"] = "other"
        else:
            payload["max_tokens"] = 769
        raw = gate.encode(payload)
    try:
        token = broker.begin_stage("s01")
        with pytest.raises(gate.GateError, match="shadow_request_mismatch"):
            broker.request(token, raw)
        assert not fake.sent and broker.summary()["request_count"] == 0
    finally:
        broker.close()


def test_v2_no_seventh_stage_or_resume(tmp_path):
    fake = Fake()
    path = tmp_path / "journal"
    broker = gate.Broker(path, fake, "a" * 40, shadow_quote_v2=True)
    try:
        for op in lineage.OPERATIONS:
            broker.request(broker.begin_stage(op), driver.frozen_request(op))
            broker.close_stage()
        with pytest.raises(gate.GateError):
            broker.begin_stage("s01")
        assert len(fake.sent) == 6
    finally:
        broker.close()
    with pytest.raises(gate.GateError):
        gate.Broker(path, fake, "a" * 40, shadow_quote_v2=True)


def test_profiles_are_mutually_exclusive_and_cannot_share_window(tmp_path):
    with pytest.raises(gate.GateError, match="experiment_mode"):
        gate.RunWindow(shadow_review=True, shadow_quote_v2=True)
    with pytest.raises(gate.GateError, match="experiment_mode"):
        run.execute(Fake(), "a" * 40, tmp_path / "run", shadow_review=True, shadow_quote_v2=True)
    with pytest.raises(gate.GateError, match="continuation_contract_mismatch"):
        gate.Broker(tmp_path / "journal", Fake(), "a" * 40,
                    shadow_quote_v2=True, window=gate.RunWindow(shadow_review=True))


def admission():
    now, mono = gate.time.time(), gate.time.monotonic()
    return {**lineage.lineage_fields(), "schema": 2, "source_sha": "b" * 40,
        "helper_sha": "a" * 40, "run_id": "123", "run_attempt": "1", "checked_at": int(now),
        "checked_at_monotonic_ns": math.floor(mono * 1e9),
        "quote_deadline_monotonic_ns": math.floor((mono + 3600) * 1e9)}


def test_v2_old_admission_and_old_profile_are_not_authority():
    record = admission()
    old = {**record, **legacy.lineage_fields()}
    with pytest.raises(gate.GateError, match="admission_lineage"):
        run.check_admission(old, {}, gate.time.time(), shadow_quote_v2=True)
    with pytest.raises(gate.GateError, match="admission_lineage"):
        run.check_admission(record, {}, gate.time.time(), shadow_review=True)


def test_v2_deadline_and_send_after_persistence_stall(tmp_path, monkeypatch):
    assert lineage.PRICE_VALID_UNTIL == 1793577600
    record = admission()
    for remaining in [1199, 0, -1]:
        now = lineage.PRICE_VALID_UNTIL - remaining
        record["checked_at"] = int(now)
        with pytest.raises(gate.GateError, match="price_review_expired"):
            run.check_admission_time(record, now, shadow_quote_v2=True)
    fake = Fake()
    broker = gate.Broker(tmp_path / "journal", fake, "a" * 40, shadow_quote_v2=True)
    real = broker._record
    def delayed(event, **values):
        real(event, **values)
        if event == "reserved":
            monkeypatch.setattr(gate.time, "time", lambda: lineage.PRICE_VALID_UNTIL)
    monkeypatch.setattr(broker, "_record", delayed)
    try:
        with pytest.raises(gate.GateError, match="price_review_expired"):
            broker.request(broker.begin_stage("s01"), driver.frozen_request("s01"))
        assert not fake.sent and broker.summary()["reserved_micro_cny"] == 3_000_000
    finally:
        broker.close()


def test_provider_request_is_standard_json_with_no_tools_and_unchanged_input():
    from experiments.shadow_quote_v2.harness import _input, SYSTEM
    for case in driver.cases():
        payload = gate.decode(driver.frozen_request(case["id"]))
        assert set(payload) == {"model", "messages", "thinking", "temperature", "max_tokens", "response_format"}
        assert payload["model"] == "deepseek-flash" and payload["max_tokens"] == 768
        assert payload["messages"][0]["content"] == SYSTEM
        assert json.loads(payload["messages"][1]["content"]) == _input(case)
        assert payload["response_format"] == {"type": "json_object"}


def test_v2_raw_invalid_response_is_captured_before_parse(tmp_path, monkeypatch):
    out = tmp_path / "run"
    real = driver.validate
    def validate(content, case):
        rows = (out / "responses.jsonl").read_text().splitlines()
        assert len(rows) == 1
        assert gate.decode(base64.b64decode(json.loads(rows[0])["raw_base64"]))["choices"][0]["message"]["content"] == content
        return real(content, case)
    monkeypatch.setattr(driver, "validate", validate)
    report = run.execute(Fake("json"), "a" * 40, out, shadow_quote_v2=True)
    assert not report["matrix"]["passed"]


@pytest.mark.parametrize("damage,code", [
    ("lineage", "admission_lineage"), ("old_helper", "admission_identity"),
    ("expiry", "price_review_expired"), ("dirty", "checkout_changed"),
    ("parent", "checkout_helper_mismatch"), ("late_lineage", "admission_lineage"),
    ("provider", "provider_metadata_mismatch"),
])
def test_main_v2_gates_before_any_secret_access(tmp_path, monkeypatch, capsys, damage, code):
    import subprocess
    import sys

    now, mono = gate.time.time(), gate.time.monotonic()
    record = {**lineage.lineage_fields(), "schema": 2, "source_sha": "b" * 40,
              "helper_sha": "a" * 40, "run_id": "123", "run_attempt": "1", "checked_at": int(now),
              "checked_at_monotonic_ns": math.floor(mono * 1e9),
              "quote_deadline_monotonic_ns": math.floor((mono + 3600) * 1e9)}
    if damage == "lineage":
        record["prior_settled_micro_cny"] = 0
    if damage == "old_helper":
        record["helper_sha"] = lineage.PREDECESSOR_HELPER_SHA
    if damage == "expiry":
        now = lineage.PRICE_VALID_UNTIL - 600
        record["checked_at"] = int(now)
    reads = []

    class Environment(dict):
        def pop(self, key, *args):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().pop(key, *args)

        def get(self, key, *args):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().get(key, *args)

        def __getitem__(self, key):
            if key == run.KEY_NAME:
                reads.append(key)
            return super().__getitem__(key)

    environment = Environment({"GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": "offercontext/offerPilot",
        "GITHUB_REF": "refs/heads/feat/20261021-companion-life-v1",
        "GITHUB_WORKFLOW_SHA": "b" * 40,
        "GITHUB_WORKFLOW_REF": "offercontext/offerPilot/.github/workflows/"
        "companion-real-ai.yml@refs/heads/feat/20261021-companion-life-v1",
        run.MODEL_NAME: "wrong" if damage == "provider" else gate.MODEL,
        run.KEY_NAME: "synthetic-never-read"})
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(record))
    monkeypatch.setattr(run.os, "environ", environment)
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setattr(sys, "argv", ["run", "--live", "--shadow-quote-v2", "--admission-file",
                                     str(path), "--evidence-dir", str(tmp_path / "evidence")])

    def git(command, **kwargs):
        assert run.KEY_NAME not in kwargs["env"]
        if command[1] == "rev-parse":
            output = record["source_sha"].encode()
        elif command[1] == "cat-file":
            parent = "c" * 40 if damage == "parent" else record["helper_sha"]
            output = ("tree " + "d" * 40 + "\nparent " + parent + "\n\nmarker").encode()
            if damage == "late_lineage":
                monkeypatch.setattr(lineage, "EVIDENCE_PATH", tmp_path / "absent.zip")
        else:
            output = b" M changed" if damage == "dirty" else b""
        return subprocess.CompletedProcess(command, 0, output)

    constructed = []

    def never_transport(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("No transport before gates")

    monkeypatch.setattr(run.subprocess, "run", git)
    monkeypatch.setattr(run, "HTTPSUpstream", never_transport)
    assert run.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == code
    assert not reads and not constructed and not (tmp_path / "evidence").exists()
