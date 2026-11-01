"""All upstreams here are fake. No credential/config discovery or paid requests."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import continuation

SHA = "a" * 40


@pytest.fixture(autouse=True)
def reviewed_test_clock(monkeypatch):
    # Offline regression remains deterministic after the live quote expires.
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)


def payload(operation=gate.OPERATIONS[0]):
    return {"model": gate.MODEL, "max_tokens": gate.OUTPUT_TOKENS, "messages": [
        {"role": "system", "content": "Synthetic persona and sourced life context"},
        {"role": "user", "content": "Synthetic user turn " + operation},
    ]}


def response(content='{"question":"Synthetic question","evidence_ids":["ev_001"]}',
             prompt=100, completion=20):
    return {"model": gate.MODEL, "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion, "prompt_cache_hit_tokens": 0,
                      "prompt_cache_miss_tokens": prompt}}


class Fake:
    def __init__(self, result=None):
        self.result = result or response()
        self.calls = []
        self.status = 200

    def __call__(self, raw):
        self.calls.append(gate.decode(raw))
        return self.status, gate.encode(self.result)


def make(tmp_path, fake=None):
    fake = fake or Fake()
    return gate.Broker(tmp_path / "journal.jsonl", fake, SHA), fake


def advance(broker, target):
    for operation in gate.OPERATIONS:
        token = broker.begin_stage(operation)
        if operation == target:
            return token
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    raise AssertionError(target)


def test_reservation_is_fsynced_before_actual_http_and_only_numbers_are_journaled(tmp_path):
    path = tmp_path / "journal.jsonl"

    def send(raw):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        created, record = records
        assert created["event"] == "created"
        assert created["settled_micro_cny"] == 315652
        assert created["lifetime_liability_micro_cny"] == 315652
        assert created["batch_settled_micro_cny"] == 0
        assert created["prior_request_count"] == 36
        assert created["batch_request_count"] == 0
        assert created["lifetime_request_count"] == 36
        assert record["event"] == "reserved"
        assert record["reserved_micro_cny"] == gate.RESERVATION
        assert record["request_count"] == 1
        assert record["lifetime_liability_micro_cny"] == 3315652
        assert record["batch_request_count"] == 1
        assert record["lifetime_request_count"] == 37
        assert all(record[key] == value for key, value in continuation.lineage_fields().items())
        return 200, gate.encode(response())

    broker = gate.Broker(path, send, SHA)
    token = broker.begin_stage(gate.OPERATIONS[0])
    broker.request(token, gate.encode(payload()))
    assert broker.summary()["settled_micro_cny"] == continuation.PRIOR_SETTLED + 360
    assert broker.summary()["reserved_micro_cny"] == 0
    broker.close_stage()
    broker.close()
    text = path.read_text()
    assert token not in text
    assert "Synthetic" not in text
    with pytest.raises(gate.GateError, match="journal_unavailable"):
        gate.Broker(path, send, SHA)


@pytest.mark.parametrize("change", [
    lambda p: p.update(model="other"),
    lambda p: p.update(max_tokens=4097),
    lambda p: p.update(max_tokens=True),
    lambda p: p.pop("max_tokens"),
    lambda p: p.update(stream=1),
    lambda p: p.update(tools=[]),
    lambda p: p.update(api_base="https://wrong.invalid"),
    lambda p: p["messages"].append({"role": "tool", "content": "extra"}),
    lambda p: p["messages"][0].update(role="user"),
    lambda p: p["messages"][0].update(name="unexpected"),
])
def test_invalid_request_cannot_reach_upstream(tmp_path, change):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    request = payload()
    change(request)
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(request))
    assert not fake.calls
    assert broker.summary()["halted"]
    broker.close()


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{}' * 100000])
def test_bad_json_never_sends(tmp_path, raw):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError):
        broker.request(token, raw)
    assert not fake.calls
    broker.close()


@pytest.mark.parametrize("change", [
    lambda r: r.pop("usage"),
    lambda r: r["usage"].update(prompt_tokens=True),
    lambda r: r["usage"].update(prompt_tokens=-1),
    lambda r: r["usage"].update(prompt_tokens=100.0),
    lambda r: r["usage"].update(completion_tokens=4097),
    lambda r: r["usage"].update(total_tokens=999),
    lambda r: r["usage"].update(prompt_cache_miss_tokens=99),
    lambda r: r["usage"].pop("prompt_cache_hit_tokens"),
    lambda r: r["usage"].update(completion_tokens_details={"reasoning_tokens": 21}),
    lambda r: r["usage"].update(prompt_tokens_details={"cached_tokens": 1}),
    lambda r: r["usage"].update(unknown_billable_tokens=200),
    lambda r: r["choices"][0].update(finish_reason="length"),
    lambda r: r.update(model="different-model"),
])
def test_unknown_or_conflicting_usage_preserves_liability_and_stops(tmp_path, change):
    result = response()
    change(result)
    broker, fake = make(tmp_path, Fake(result))
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["settled_micro_cny"] == continuation.PRIOR_SETTLED
    assert broker.summary()["lifetime_liability_micro_cny"] == 3315652
    assert broker.summary()["batch_settled_micro_cny"] == 0
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    broker.close()


@pytest.mark.parametrize("status", [301, 302, 307, 308, 429, 500])
def test_no_redirect_or_status_retry(tmp_path, status):
    fake = Fake()
    fake.status = status
    broker, fake = make(tmp_path, fake)
    token = broker.begin_stage(gate.OPERATIONS[0])
    for _ in range(3):
        with pytest.raises(gate.GateError):
            broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    broker.close()


def test_timeout_unknown_retains_three_yuan(tmp_path):
    calls = []

    def timeout(raw):
        calls.append(raw)
        raise TimeoutError("upstream private response must not leak")

    broker, _ = make(tmp_path, timeout)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match="upstream_unknown") as error:
        broker.request(token, gate.encode(payload()))
    assert "private" not in str(error.value)
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    broker.close()


def test_budget_stops_before_total_could_exceed_twenty_yuan(tmp_path):
    # With both historical batches inherited, eight maximum-context responses
    # cannot authorize a ninth reservation. The earlier one-batch seed allowed nine.
    cost = (gate.CONTEXT_TOKENS - gate.OUTPUT_TOKENS) * 2 + gate.OUTPUT_TOKENS * 8
    broker, fake = make(tmp_path, Fake(response(
        prompt=gate.CONTEXT_TOKENS - gate.OUTPUT_TOKENS, completion=gate.OUTPUT_TOKENS)))
    for operation in gate.OPERATIONS[:8]:
        token = broker.begin_stage(operation)
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    token = broker.begin_stage(gate.OPERATIONS[8])
    with pytest.raises(gate.GateError, match="budget_exhausted"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 8
    assert broker.summary()["settled_micro_cny"] == continuation.PRIOR_SETTLED + 8 * cost
    broker.close()


def test_single_ticket_sdk_retry_and_old_stage_token_fail_closed(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    broker.request(token, gate.encode(payload()))
    broker.close_stage()
    new_token = broker.begin_stage(gate.OPERATIONS[1])
    assert new_token != token
    with pytest.raises(gate.GateError, match="gate_closed"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    with pytest.raises(gate.GateError):
        broker.request(new_token, gate.encode(payload()))
    broker.close()


@pytest.mark.parametrize("changed", [False, True])
def test_second_response_never_becomes_a_format_repair(tmp_path, changed):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    first = payload()
    broker.request(token, gate.encode(first))
    if changed:
        first["messages"][-1]["content"] += " Please repair the answer"
    with pytest.raises(gate.GateError, match="ticket_consumed"):
        broker.request(token, gate.encode(first))
    assert len(fake.calls) == 1
    broker.close()


def test_twelve_stages_exactly_twelve_http_requests(tmp_path):
    broker, fake = make(tmp_path)
    for operation in gate.OPERATIONS:
        token = broker.begin_stage(operation)
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    assert len(fake.calls) == 12
    assert broker.summary()["stages_complete"] == 12
    assert broker.summary()["prior_request_count"] == 36
    assert broker.summary()["batch_request_count"] == 12
    assert broker.summary()["lifetime_request_count"] == 48
    assert all(body["thinking"] == {"type": "disabled"} for body in fake.calls)
    with pytest.raises(gate.GateError):
        broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 12
    broker.close()


@pytest.mark.parametrize("seed", [0, False, -1])
def test_no_public_opening_balance_override(tmp_path, monkeypatch, seed):
    monkeypatch.setenv("OFFERPILOT_COMPANION_INITIAL_SETTLED_MICRO_CNY", str(seed))
    monkeypatch.setenv("OFFERPILOT_COMPANION_PRIOR_SETTLED_MICRO_CNY", str(seed))
    with pytest.raises(TypeError):
        gate.Broker(tmp_path / "never-created.jsonl", Fake(), SHA,
                    initial_settled_micro_cny=seed)
    assert not (tmp_path / "never-created.jsonl").exists()
    broker, fake = make(tmp_path)
    assert broker.summary()["settled_micro_cny"] == 315652
    assert not fake.calls
    broker.close()


@pytest.mark.parametrize("damage", ["missing", "changed"])
@pytest.mark.parametrize("evidence_attr", ["EVIDENCE_PATH", "SECOND_EVIDENCE_PATH", "ORIGINAL_EVIDENCE_PATH"])
def test_unverified_predecessor_cannot_create_journal_or_send(
        tmp_path, monkeypatch, damage, evidence_attr):
    evidence = tmp_path / "original.zip"
    if damage == "changed":
        evidence.write_bytes(getattr(continuation, evidence_attr).read_bytes() + b"changed")
    monkeypatch.setattr(continuation, evidence_attr, evidence)
    fake = Fake()
    with pytest.raises(gate.GateError, match="predecessor_evidence_invalid"):
        make(tmp_path, fake)
    assert not (tmp_path / "journal.jsonl").exists()
    assert not fake.calls


@pytest.mark.parametrize("excess", [0, 2])
def test_real_settlements_use_lifetime_budget_not_a_new_twenty_yuan(tmp_path, excess):
    # All prior costs are genuinely settled through fake HTTP, never injected.
    # Fixed integer pricing permits costs in multiples of two micro-CNY.
    target = gate.BUDGET - gate.RESERVATION + excess
    costs = [2_000_000] * 8 + [target - 315652 - 16_000_000]
    calls = []

    def send(raw):
        records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
        assert records[-1]["lifetime_liability_micro_cny"] <= 20_000_000
        calls.append(raw)
        cost = costs[len(calls) - 1] if len(calls) <= len(costs) else 360
        return 200, gate.encode(response(prompt=(cost - 160) // 2, completion=20))

    broker, _ = make(tmp_path, send)
    for operation in gate.OPERATIONS[:9]:
        token = broker.begin_stage(operation)
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    assert broker.summary()["lifetime_settled_micro_cny"] == target
    token = broker.begin_stage(gate.OPERATIONS[9])
    if excess:
        with pytest.raises(gate.GateError, match="budget_exhausted"):
            broker.request(token, gate.encode(payload()))
        assert len(calls) == 9
    else:
        broker.request(token, gate.encode(payload()))
        assert len(calls) == 10
        assert broker.summary()["settled_micro_cny"] == target + 360
    broker.close()


@pytest.mark.parametrize("remaining", [3_000_000, 2_999_999])
def test_exact_micro_boundary_before_http(tmp_path, remaining):
    broker, fake = make(tmp_path)
    # White-box arithmetic boundary only: the odd micro-CNY state is not
    # obtainable with this provider's even-token prices or any public API.
    broker._state["settled_micro_cny"] = gate.BUDGET - remaining
    token = broker.begin_stage(gate.OPERATIONS[0])
    if remaining == gate.RESERVATION:
        broker.request(token, gate.encode(payload()))
        assert len(fake.calls) == 1
    else:
        with pytest.raises(gate.GateError, match="budget_exhausted"):
            broker.request(token, gate.encode(payload()))
        assert not fake.calls
        assert broker.summary()["reserved_micro_cny"] == 0
    broker.close()


def test_every_journal_event_preserves_consistent_prior_batch_and_lifetime(tmp_path):
    broker, fake = make(tmp_path)
    for operation in gate.OPERATIONS:
        token = broker.begin_stage(operation)
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    broker.close()
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert len(fake.calls) == 12 and len(records) == 38
    for record in records:
        assert record["schema"] == 2
        assert all(record[key] == value for key, value in continuation.lineage_fields().items())
        assert record["batch_request_count"] == record["request_count"]
        assert record["lifetime_request_count"] == 36 + record["batch_request_count"]
        assert record["lifetime_settled_micro_cny"] == 315652 + record["batch_settled_micro_cny"]
        assert record["settled_micro_cny"] == record["lifetime_settled_micro_cny"]
        assert record["reserved_micro_cny"] == record["batch_reserved_micro_cny"]
        assert record["lifetime_reserved_micro_cny"] == record["reserved_micro_cny"]
        liability = record["lifetime_settled_micro_cny"] + record["lifetime_reserved_micro_cny"]
        assert record["lifetime_liability_micro_cny"] == liability
        assert record["lifetime_remaining_micro_cny"] == 20_000_000 - liability
    assert records[-1]["event"] == "closed" and records[-1]["halted"] is True
    assert records[-2]["event"] == "stage_complete" and records[-2]["halted"] is False


def test_concurrent_request_never_sends_second(tmp_path):
    entered, release = threading.Event(), threading.Event()
    errors = []
    calls = []

    def slow(raw):
        calls.append(raw)
        entered.set()
        assert release.wait(5)
        return 200, gate.encode(response())

    broker, _ = make(tmp_path, slow)
    token = broker.begin_stage(gate.OPERATIONS[0])

    def run():
        try:
            broker.request(token, gate.encode(payload()))
        except gate.GateError as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(gate.GateError, match="concurrent_request"):
        broker.request(token, gate.encode(payload()))
    release.set()
    thread.join(5)
    assert len(calls) == 1
    assert broker.summary()["halted"]
    assert errors
    broker.close()


@pytest.mark.parametrize("event", ["reserved", "settled"])
def test_ledger_write_failure_never_releases_unknown_reservation(tmp_path, monkeypatch, event):
    broker, fake = make(tmp_path)
    original = broker._record

    def fail(name, **values):
        if name == event:
            raise gate.GateError("journal_failure")
        return original(name, **values)

    monkeypatch.setattr(broker, "_record", fail)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match="journal_failure"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == (event == "settled")
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["settled_micro_cny"] == continuation.PRIOR_SETTLED
    broker.close()


def test_http_loopback_auth_path_and_success(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with gate.serve(broker) as base:
        request = urllib.request.Request(base + "/chat/completions", gate.encode(payload()),
                                         {"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
        with urllib.request.urlopen(request) as result:
            assert result.status == 200
        request = urllib.request.Request(base + "/other", gate.encode(payload()),
                                         {"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 400
    assert len(fake.calls) == 1
    broker.close()


def test_tls_transport_has_fixed_destination_and_never_uses_proxy_or_redirect(monkeypatch):
    events = []
    for name in ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)

    class Socket:
        def settimeout(self, value):
            assert 0 < value <= 90

    class Response:
        status = 307
        def read1(self, size):
            return b""

    class Connection:
        def __init__(self, host, port, timeout, context):
            events.append((host, port))
            assert context.check_hostname is True
            assert context.verify_mode == 2
            self.sock = Socket()
        def connect(self):
            events.append("connected")
        def request(self, method, path, body, headers):
            events.append((method, path))
        def getresponse(self):
            return Response()
        def close(self):
            events.append("closed")

    monkeypatch.setattr(gate.http.client, "HTTPSConnection", Connection)
    status, raw = gate.HTTPSUpstream("synthetic-test-only")(b"{}")
    assert status == 307 and raw == b""
    assert events == [("api.deepseek.com", 443), "connected",
                      ("POST", "/chat/completions"), "closed"]


def test_maximum_documented_input_and_output_have_strict_reservation_margin():
    assert gate.CONTEXT_TOKENS * 2 + gate.OUTPUT_TOKENS * 8 == 2_101_248
    assert 2_101_248 < gate.RESERVATION
    assert gate.BUDGET == 20_000_000


@pytest.mark.parametrize("damage", ["truncate", "replace", "corrupt"])
def test_midrun_journal_damage_stops_before_next_http(tmp_path, damage):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    broker.request(token, gate.encode(payload()))
    broker.close_stage()
    path = tmp_path / "journal.jsonl"
    if damage == "truncate":
        path.write_bytes(b"")
    elif damage == "replace":
        path.unlink()
        path.write_bytes(broker._journal_bytes)
    else:
        raw = bytearray(path.read_bytes())
        raw[0] = ord("[")
        path.write_bytes(raw)
    token = broker.begin_stage(gate.OPERATIONS[1])
    with pytest.raises(gate.GateError, match="journal_failure"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    with pytest.raises(gate.GateError):
        broker.close()


@pytest.mark.parametrize("boundary", ["price", "session"])
def test_every_request_requires_full_timeout_within_review_and_session(tmp_path, monkeypatch, boundary):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    broker.request(token, gate.encode(payload()))
    broker.close_stage()
    token = broker.begin_stage(gate.OPERATIONS[1])
    if boundary == "price":
        monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 89)
    else:
        monkeypatch.setattr(gate.time, "monotonic", lambda: broker._deadline - 89)
    with pytest.raises(gate.GateError, match="price_review_expired|session_deadline"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    broker.close()


@pytest.mark.parametrize("name", ["SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_upstream_rejects_tls_logging_and_trust_injection(monkeypatch, name):
    monkeypatch.setenv(name, "/synthetic/not-read")
    with pytest.raises(gate.GateError, match="unsafe_tls_environment"):
        gate.HTTPSUpstream("synthetic-test-only")(b"{}")


@pytest.mark.parametrize("boundary", ["price", "session"])
def test_slow_reservation_fsync_must_recheck_before_sending(tmp_path, monkeypatch, boundary):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    original = broker._record

    def slow(name, **values):
        original(name, **values)
        if name == "reserved":
            if boundary == "price":
                monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 89)
            else:
                monkeypatch.setattr(gate.time, "monotonic", lambda: broker._deadline + 1)

    monkeypatch.setattr(broker, "_record", slow)
    with pytest.raises(gate.GateError, match="price_review_expired|session_deadline"):
        broker.request(token, gate.encode(payload()))
    assert not fake.calls
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["halted"]
    broker.close()


@pytest.mark.parametrize("boundary", ["price", "session", "request"])
def test_slow_tls_connect_cannot_send_http_after_deadline(tmp_path, monkeypatch, boundary):
    for name in ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    monotonic = [100.0]
    wall = [gate.PRICE_VALID_UNTIL - 3600]
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    events = []

    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = object()
        def connect(self):
            events.append("tls_only")
            if boundary == "price":
                wall[0] = gate.PRICE_VALID_UNTIL - 89
            else:
                monotonic[0] = 1301 if boundary == "session" else 191
        def request(self, *args, **kwargs):
            pytest.fail("No HTTP or credential may be sent after connect expired")
        def close(self):
            events.append("closed")

    monkeypatch.setattr(gate.http.client, "HTTPSConnection", Connection)
    transport = gate.HTTPSUpstream("synthetic-test-only")
    broker, _ = make(tmp_path, transport)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match="price_review_expired|session_deadline|upstream_timeout"):
        broker.request(token, gate.encode(payload()))
    assert events == ["tls_only", "closed"]
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["halted"]
    broker.close()


def test_wall_clock_rollback_cannot_extend_price_deadline(tmp_path, monkeypatch):
    monotonic = [100.0]
    wall = [gate.PRICE_VALID_UNTIL - 100]
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    original = broker._record

    def slow(name, **values):
        original(name, **values)
        if name == "reserved":
            monotonic[0] += 11
            wall[0] -= 3600

    monkeypatch.setattr(broker, "_record", slow)
    with pytest.raises(gate.GateError, match="price_review_expired"):
        broker.request(token, gate.encode(payload()))
    assert not fake.calls
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    broker.close()


@pytest.mark.parametrize("boundary", ["price", "session"])
def test_shared_window_cannot_be_reanchored_by_transport_or_broker_creation(
        tmp_path, monkeypatch, boundary):
    monotonic, wall = [100.0], [gate.PRICE_VALID_UNTIL - (1201 if boundary == "price" else 3600)]
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    window = gate.RunWindow()
    monotonic[0] += 1112
    wall[0] -= 3600
    transport = gate.HTTPSUpstream("synthetic-test-only", window=window)
    fake = Fake()
    broker = gate.Broker(tmp_path / "journal.jsonl", fake, SHA, window=window)
    assert broker._deadline == transport._deadline == window.session_deadline == 1300
    assert broker._price_deadline == transport._price_deadline == window.price_deadline
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match="price_review_expired|session_deadline"):
        broker.request(token, gate.encode(payload()))
    assert not fake.calls
    broker.close()


def test_slow_predecessor_verification_cannot_reanchor_broker_quote(tmp_path, monkeypatch):
    monotonic, wall = [100.0], [gate.PRICE_VALID_UNTIL - 100]
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    original = continuation.lineage_fields

    def slow():
        result = original()
        monotonic[0] += 11
        wall[0] -= 3600
        return result

    monkeypatch.setattr(continuation, "lineage_fields", slow)
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    with pytest.raises(gate.GateError, match="price_review_expired"):
        broker.request(token, gate.encode(payload()))
    assert not fake.calls
    broker.close()


def test_window_rejects_monotonic_regression_and_disallows_custom_anchor(monkeypatch):
    monotonic = [100.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: monotonic[0])
    window = gate.RunWindow()
    monotonic[0] = 110.0
    window.effective_wall()
    monotonic[0] = 109.0
    with pytest.raises(gate.GateError, match="clock_anomaly"):
        window.effective_wall()
    with pytest.raises(TypeError):
        gate.RunWindow(price_deadline=999999999999)


def test_carried_preflight_quote_can_only_shorten_process_deadline(monkeypatch):
    monkeypatch.setattr(gate.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
    window = gate.RunWindow()
    assert window.price_deadline == 3700
    window.restrict_quote_deadline(9999_000_000_000)
    assert window.price_deadline == 3700
    window.restrict_quote_deadline(1301_000_000_000)
    narrowed = window.price_deadline
    assert narrowed < 1301 and narrowed == pytest.approx(1301)
    window.restrict_quote_deadline(3700_000_000_000)
    assert window.price_deadline == narrowed


def test_actual_chat_history_and_tools_are_preserved_but_thinking_is_disabled(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    request = payload()
    request['messages'][1:1] = [
        {'role': 'user', 'content': 'Earlier synthetic turn'},
        {'role': 'assistant', 'content': 'Earlier sourced reply'},
    ]
    request['tools'] = [{'type': 'function', 'function': {
        'name': 'synthetic_read', 'description': 'Synthetic tool metadata, never executed',
        'parameters': {'type': 'object', 'properties': {}}}}]
    request['tool_choice'] = 'auto'
    broker.request(token, gate.encode(request))
    assert fake.calls[0]['messages'] == request['messages']
    assert fake.calls[0]['tools'] == request['tools']
    assert fake.calls[0]['thinking'] == {'type': 'disabled'}
    assert fake.calls[0]['stream'] is False
    broker.close()


def test_stream_is_local_only_after_complete_validated_upstream_response(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage(gate.OPERATIONS[0])
    request = payload()
    request.update(stream=True, stream_options={'include_usage': True})
    output = broker.request(token, gate.encode(request))
    assert fake.calls[0]['stream'] is False
    assert 'stream_options' not in fake.calls[0]
    assert output.endswith(b'data: [DONE]\n\n')
    chunks = [json.loads(line[6:]) for line in output.decode().splitlines()
              if line.startswith('data: {')]
    assert chunks[0]['choices'][0]['delta']['content'] == response()['choices'][0]['message']['content']
    assert chunks[1]['choices'][0]['finish_reason'] == 'stop'
    assert broker.summary()['reserved_micro_cny'] == 0
    broker.close()


def test_stream_tool_response_exposes_no_partial_content(tmp_path):
    result = response(content='This text must never reach the product')
    result['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'write'}}]
    broker, fake = make(tmp_path, Fake(result))
    token = broker.begin_stage(gate.OPERATIONS[0])
    request = payload()
    request['stream'] = True
    with pytest.raises(gate.GateError, match='unexpected_tool'):
        broker.request(token, gate.encode(request))
    assert len(fake.calls) == 1
    assert broker.summary()['reserved_micro_cny'] == gate.RESERVATION
    assert broker.summary()['halted']
    broker.close()
