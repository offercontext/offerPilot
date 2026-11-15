"""All upstreams here are fake. No credential/config discovery or paid requests."""
from datetime import datetime, timezone
import json
import threading
import urllib.error
import urllib.request

import pytest

from scripts.simplification_ai_validation import broker as gate

SHA = "a" * 40


@pytest.fixture(autouse=True)
def reviewed_test_clock(monkeypatch):
    # Offline regression remains deterministic after the live quote expires.
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)


def payload(operation="cli_analyze"):
    data = {"snapshot": {}, "evidence_catalog": [
        {"id": "ev_001", "source": "jd", "path": "/jd/text", "value": "Synthetic JD"},
        {"id": "ev_002", "source": "turn", "path": "/turns/001/answer", "value": "Synthetic"},
    ], "turns": [], "repair_failure_category": None}
    if operation.endswith("feedback"):
        data.pop("snapshot")
        data.pop("repair_failure_category")
    return {"model": gate.MODEL, "max_tokens": 4096, "messages": [
        {"role": "system", "content": "Synthetic prompt"},
        {"role": "user", "content": gate.encode(data).decode()},
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
        record = json.loads(path.read_text().splitlines()[-1])
        assert record["event"] == "reserved"
        assert record["reserved_micro_cny"] == gate.RESERVATION
        assert record["request_count"] == 1
        return 200, gate.encode(response())

    broker = gate.Broker(path, send, SHA)
    token = broker.begin_stage("cli_analyze")
    broker.request(token, gate.encode(payload()))
    assert broker.summary()["settled_micro_cny"] == 360
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
    lambda p: p.update(stream=True),
    lambda p: p.update(tools=[]),
    lambda p: p.update(api_base="https://wrong.invalid"),
    lambda p: p["messages"].append({"role": "user", "content": "extra"}),
    lambda p: p["messages"][0].update(role="user"),
    lambda p: p["messages"][0].update(name="unexpected"),
])
def test_invalid_request_cannot_reach_upstream(tmp_path, change):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
    request = payload()
    change(request)
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(request))
    assert not fake.calls
    assert broker.summary()["halted"]
    broker.close()


@pytest.mark.parametrize("raw", [
    b'{"x":1,"x":2}', b'{"x":NaN}', b'[]',
    pytest.param(b'{}' * 100000, id="oversized-body"),
])
def test_bad_json_never_sends(tmp_path, raw):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
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
    token = broker.begin_stage("cli_analyze")
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["settled_micro_cny"] == 0
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    broker.close()


@pytest.mark.parametrize("status", [301, 302, 307, 308, 429, 500])
def test_no_redirect_or_status_retry(tmp_path, status):
    fake = Fake()
    fake.status = status
    broker, fake = make(tmp_path, fake)
    token = broker.begin_stage("cli_analyze")
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
    token = broker.begin_stage("cli_analyze")
    with pytest.raises(gate.GateError, match="upstream_unknown") as error:
        broker.request(token, gate.encode(payload()))
    assert "private" not in str(error.value)
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    broker.close()


def test_budget_stops_before_total_could_exceed_ten_yuan(tmp_path):
    # Even four known near-context calls cannot authorize a fifth reservation.
    broker, fake = make(tmp_path, Fake(response(prompt=1_044_480, completion=4096)))
    for operation in gate.OPERATIONS[:4]:
        token = broker.begin_stage(operation)
        broker.request(token, gate.encode(payload(operation)))
        broker.close_stage()
    token = broker.begin_stage(gate.OPERATIONS[4])
    with pytest.raises(gate.GateError, match="budget_exhausted"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 4
    assert broker.summary()["settled_micro_cny"] == 8_486_912
    broker.close()


def test_single_ticket_sdk_retry_and_old_stage_token_fail_closed(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
    broker.request(token, gate.encode(payload()))
    broker.close_stage()
    new_token = broker.begin_stage("http_analyze")
    assert new_token != token
    with pytest.raises(gate.GateError, match="gate_closed"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    with pytest.raises(gate.GateError):
        broker.request(new_token, gate.encode(payload()))
    broker.close()


def test_same_body_is_not_a_structural_repair(tmp_path):
    broker, fake = make(tmp_path)
    token = advance(broker, "quick_start")
    fake.result = response(content="not JSON")
    raw = gate.encode(payload("quick_start"))
    broker.request(token, raw)
    with pytest.raises(gate.GateError, match="not_structural_repair"):
        broker.request(token, raw)
    assert len(fake.calls) == 7
    broker.close()


def test_repair_cannot_change_source_snapshot_or_evidence(tmp_path):
    broker, fake = make(tmp_path)
    token = advance(broker, "quick_start")
    fake.result = response(content="not JSON")
    first = payload("quick_start")
    broker.request(token, gate.encode(first))
    second = gate._expected_repair("quick_start", first, "invalid_json")
    source = gate.decode(second["messages"][1]["content"].encode())
    source["snapshot"] = {"application_id": 999}
    second["messages"][1]["content"] = gate.encode(source).decode()
    with pytest.raises(gate.GateError, match="not_structural_repair"):
        broker.request(token, gate.encode(second))
    assert len(fake.calls) == 7
    broker.close()


def test_twelve_stages_six_unique_repairs_eighteen_actual_http_max(tmp_path):
    broker, fake = make(tmp_path)
    for operation in gate.OPERATIONS:
        token = broker.begin_stage(operation)
        first = payload(operation)
        mock = operation.startswith(("quick_", "event_"))
        fake.result = response(content="not JSON" if mock else "{}")
        broker.request(token, gate.encode(first))
        if mock:
            fake.result = response(content=(
                '{"schema_version":"mock-interview-feedback-v1","proposal_status":"safe_empty",'
                '"strengths":[],"practice_points":[],"follow_up_questions":[],"next_practice_steps":[]}'
                if operation.endswith("feedback") else
                '{"question":"Question?","evidence_ids":["ev_001"]}'
            ))
            broker.request(token, gate.encode(gate._expected_repair(operation, first, "invalid_json")))
        broker.close_stage()
    assert len(fake.calls) == 18
    assert broker.summary()["stages_complete"] == 12
    with pytest.raises(gate.GateError):
        broker.begin_stage("quick_start")
    with pytest.raises(gate.GateError):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 18
    broker.close()


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
    token = broker.begin_stage("cli_analyze")

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
    token = broker.begin_stage("cli_analyze")
    with pytest.raises(gate.GateError, match="journal_failure"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == (event == "settled")
    assert broker.summary()["reserved_micro_cny"] == gate.RESERVATION
    assert broker.summary()["settled_micro_cny"] == 0
    broker.close()


def test_http_loopback_auth_path_and_success(tmp_path):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
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
    assert gate.CONTEXT_TOKENS * 2 + gate.OUTPUT_TOKENS * 8 == 2_129_920
    assert 2_129_920 < gate.RESERVATION
    assert gate.BUDGET == 10_000_000


@pytest.mark.parametrize("damage", ["truncate", "replace", "corrupt"])
def test_midrun_journal_damage_stops_before_next_http(tmp_path, damage):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
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
    token = broker.begin_stage("http_analyze")
    with pytest.raises(gate.GateError, match="journal_failure"):
        broker.request(token, gate.encode(payload()))
    assert len(fake.calls) == 1
    with pytest.raises(gate.GateError):
        broker.close()


@pytest.mark.parametrize("boundary", ["price", "session"])
def test_every_request_requires_full_timeout_within_review_and_session(tmp_path, monkeypatch, boundary):
    broker, fake = make(tmp_path)
    token = broker.begin_stage("cli_analyze")
    broker.request(token, gate.encode(payload()))
    broker.close_stage()
    token = broker.begin_stage("http_analyze")
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
    token = broker.begin_stage("cli_analyze")
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
    token = broker.begin_stage("cli_analyze")
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
    token = broker.begin_stage("cli_analyze")
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


def test_reviewed_quote_has_fixed_deadline_and_unchanged_bounds():
    assert gate.PRICE_VALID_UNTIL == datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
    assert gate.MODEL == "deepseek-flash" and gate.BASE_URL == "https://api.deepseek.com"
    assert gate.CONTEXT_TOKENS == 1_048_576 and gate.OUTPUT_TOKENS == 4096
    assert gate.CONTEXT_TOKENS * 2 + gate.OUTPUT_TOKENS * 8 == 2_129_920
    assert gate.RESERVATION == 3_000_000 and gate.BUDGET == 10_000_000
    assert gate.REQUEST_SECONDS == 90 and gate.SESSION_SECONDS == 20 * 60
    assert gate.MAX_REQUESTS == 18
