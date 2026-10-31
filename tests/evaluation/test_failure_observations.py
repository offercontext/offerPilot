from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from offerpilot.evaluation import core_subject
from offerpilot.evaluation.core_actions import CoreHTTPDriver


class StubClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        pass


def make_driver(monkeypatch, result):
    client = StubClient(result)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    observations = []
    return CoreHTTPDriver("http://127.0.0.1", "HEADER-SECRET", time.monotonic() + 90,
                          observations, 4), client, observations


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, RuntimeError])
@pytest.mark.parametrize("path", ["/api/chat?token=QUERY-SECRET#FRAGMENT-SECRET",
    "http://user:PASSWORD-SECRET@127.0.0.1/api/chat?token=QUERY-SECRET"])
def test_http_error_is_preserved_with_safe_request_evidence(monkeypatch, error_type, path):
    error = error_type("EXCEPTION-SECRET")
    error.__cause__ = ValueError("CAUSE-SECRET")
    driver, client, observations = make_driver(monkeypatch, error)
    with pytest.raises(error_type) as caught:
        driver.request("POST", path, {"message": "BODY-SECRET"})
    assert caught.value is error
    assert client.calls[0][2]["timeout"] == 30
    assert observations == []  # A failed request is not a successful response observation.
    assert len(driver.request_errors) == 1
    record = driver.request_errors[0]
    assert record["method"] == "POST" and record["path"] == "/api/chat"
    assert record["timeout_seconds"] == 30 and record["elapsed_seconds"] >= 0
    assert record["error_type"] == error_type.__name__
    assert record["error_chain_types"] == [error_type.__name__, "ValueError"]
    assert record["response_received"] is None and record["response_status"] is None
    assert record["driver_request_index"] == 1 and record["observation_id"]
    encoded = json.dumps(record)
    assert "SECRET" not in encoded and "127.0.0.1" not in encoded


def test_normal_http_observation_shape_and_reply_limit_stay_unchanged(monkeypatch):
    response = httpx.Response(202, json={"turn_id": "turn"})
    driver, client, observations = make_driver(monkeypatch, response)
    assert driver.request("POST", "/api/chat", {"message": "hello"}) == {"turn_id": "turn"}
    assert observations == [dict(method="POST", path="/api/chat", body={"message": "hello"},
                                 status=202, response={"turn_id": "turn"})]
    assert driver.request_errors == []
    driver.reply_counter[0] = 4
    with pytest.raises(TimeoutError, match="script reply watchdog"):
        driver.request("POST", "/api/chat")
    assert len(client.calls) == 1


def test_error_metadata_survives_a_later_success_without_fake_http_response(monkeypatch):
    error = httpx.ReadTimeout("EXCEPTION-SECRET")
    driver, client, observations = make_driver(monkeypatch, error)
    with pytest.raises(httpx.ReadTimeout):
        driver.request("GET", "/api/pilot/runtime/v1/turns/turn?auth=QUERY-SECRET")
    first = json.loads(json.dumps(driver.request_errors))
    client.result = httpx.Response(200, json={"state": "completed"})
    driver.request("GET", "/api/pilot/runtime/v1/turns/turn")
    assert driver.request_errors == first
    assert len(observations) == 1 and observations[0]["status"] == 200


def test_invalid_diagnostic_url_does_not_replace_original_exception(monkeypatch):
    error = RuntimeError("EXCEPTION-SECRET")
    driver, _, observations = make_driver(monkeypatch, error)
    with pytest.raises(RuntimeError) as caught:
        driver.request("GET", "http://[malformed")
    assert caught.value is error and observations == []
    assert driver.request_errors[0]["path"] is None
    assert "SECRET" not in json.dumps(driver.request_errors)


@pytest.mark.parametrize("cycle", [False, True])
def test_exception_metadata_is_bounded_and_handles_cycles(monkeypatch, cycle):
    error = RuntimeError("EXCEPTION-SECRET")
    current = error
    for _ in range(20):
        following = ValueError("CAUSE-SECRET")
        current.__cause__ = following
        current = following
    if cycle:
        error.__cause__.__cause__ = error
    driver, _, _ = make_driver(monkeypatch, error)
    with pytest.raises(RuntimeError) as caught:
        driver.request("GET", "/api/chat")
    assert caught.value is error
    record = driver.request_errors[0]
    assert len(record["error_chain_types"]) <= 8 and record["error_chain_truncated"] is True
    assert record["error_chain_cycle"] is cycle
    assert "SECRET" not in json.dumps(record)


def test_diagnostic_capture_failure_cannot_mask_main_exception(monkeypatch):
    from offerpilot.evaluation import core_actions

    error = httpx.ReadTimeout("EXCEPTION-SECRET")
    driver, _, _ = make_driver(monkeypatch, error)
    monkeypatch.setattr(core_actions, "uuid4", lambda: (_ for _ in ()).throw(ValueError("CAPTURE-SECRET")))
    with pytest.raises(httpx.ReadTimeout) as caught:
        driver.request("GET", "/api/chat")
    assert caught.value is error and driver.request_errors == []
    assert driver.request_error_capture_errors == ["ValueError"]


def make_subject(tmp_path, monkeypatch, raw, *, mode="normal", previous_events=None,
                 ledger=None, trial_key=None, epoch=2):
    private = tmp_path / "private"
    private.mkdir()
    process = SimpleNamespace(stdout=io.StringIO(raw), stdin=io.StringIO(), returncode=0,
                              poll=lambda: 0)
    monkeypatch.setattr(core_subject, "process_tree", lambda *args, **kwargs: SimpleNamespace(process=process))
    writes = []
    original_fsync = core_subject.os.fsync

    def fsync(fd):
        writes.append(fd)
        if mode == "persist":
            raise OSError("PERSIST-SECRET")
        return original_fsync(fd)

    monkeypatch.setattr(core_subject.os, "fsync", fsync)
    if mode == "observer":
        (private / "data.db").touch()
        monkeypatch.setattr(core_subject.observer, "connect",
                            lambda *args: (_ for _ in ()).throw(RuntimeError("OBSERVER-SECRET")))
    original_send = core_subject.CoreSubject.send

    def send(self, value):
        if mode == "ack" and value.get("command") == "ack":
            raise BrokenPipeError("ACK-SECRET")
        return original_send(self, value)

    monkeypatch.setattr(core_subject.CoreSubject, "send", send)

    def reserve(event):
        if mode == "reserve":
            raise TimeoutError("RESERVATION-SECRET")

    key = trial_key or dict(run_id="run", variant_id="candidate", execution_item_id="E06.legacy_saved",
                           mode="deterministic", repeat_index=1)
    config = dict(epoch=epoch, trial_key=key, limits=dict(model_calls=8, tool_attempts=20), command="prepare")
    variant = SimpleNamespace(checkout=str(tmp_path), interpreter="unused")
    subject = core_subject.CoreSubject(variant, config, private,
        previous_events if previous_events is not None else [], time.monotonic() + 60,
        ledger if ledger is not None else SimpleNamespace(reserve=reserve))
    subject.reader.join(3)
    assert not subject.reader.is_alive()
    subject.stderr.close()
    return subject, process, writes


def control_line(seq=1, kind="ready", **values):
    return json.dumps(dict(schema_version=1, trial_key=dict(run_id="run", variant_id="candidate",
        execution_item_id="E06.legacy_saved", mode="deterministic", repeat_index=1),
        epoch=2, seq=seq, type=kind, **values)) + "\n"


@pytest.mark.parametrize("mode,raw,error_type,phase,validated,persisted", [
    ("normal", "NOT-JSON-SECRET\n", "JSONDecodeError", "identity", 0, 0),
    ("normal", control_line(seq=3), "ValueError", "identity", 0, 0),
    ("observer", control_line(kind="call_attempt", purpose="provider_send"), "RuntimeError", "observer", 1, 0),
    ("persist", control_line(), "OSError", "persist", 1, 0),
    ("reserve", control_line(kind="call_attempt", purpose="provider_send"), "TimeoutError", "reserve", 1, 1),
    ("ack", control_line(), "BrokenPipeError", "ack", 1, 1),
])
def test_control_failure_has_epoch_local_phase_and_sequence(tmp_path, monkeypatch, mode, raw,
                                                           error_type, phase, validated, persisted):
    old = [dict(type="model_observation", seq=99, epoch=1, payload="OLD-SECRET")]
    subject, _, _ = make_subject(tmp_path, monkeypatch, raw, mode=mode, previous_events=old)
    record = subject.control_observation()
    assert record["epoch"] == 2 and record["phase"] == phase
    assert record["error_type"] == error_type and record["exit_reason"] == "error"
    assert record["last_validated_seq"] == validated
    assert record["last_persisted_seq"] == persisted and record["last_ack_seq"] == 0
    assert record["reader_exited"] is True and record["reader_alive"] is False
    assert "SECRET" not in json.dumps(record)


def test_control_eof_does_not_inherit_previous_epoch_or_invent_errors(tmp_path, monkeypatch):
    subject, _, writes = make_subject(tmp_path, monkeypatch, "", previous_events=[
        dict(type="model_observation", seq=99, epoch=1)])
    record = subject.control_observation()
    assert record["epoch"] == 2 and record["exit_reason"] == "eof"
    assert record["error_type"] is None and record["phase"] == "receive"
    assert record["last_validated_seq"] == record["last_persisted_seq"] == record["last_ack_seq"] == 0
    assert writes == []


def test_normal_control_keeps_ack_order_fsync_count_and_detached_snapshots(tmp_path, monkeypatch):
    subject, process, writes = make_subject(tmp_path, monkeypatch,
        control_line() + control_line(2, "shutdown_complete"))
    record = subject.control_observation()
    assert record["exit_reason"] == "eof" and record["error_type"] is None
    assert record["last_validated_seq"] == record["last_persisted_seq"] == 2
    assert record["last_ack_seq"] == 1
    commands = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [row["seq"] for row in commands if row["command"] == "ack"] == [1]
    assert len(writes) == 2
    subject.errors.append("abnormal_shutdown")
    assert "abnormal_shutdown" not in record["errors"]
    assert subject.control_observation()["errors"] == ["abnormal_shutdown"]


@pytest.mark.parametrize("failure_epoch,primary_type", [pytest.param(None, None, id="success"),
    pytest.param(1, httpx.ReadTimeout, id="epoch1"), pytest.param(2, httpx.ReadTimeout, id="epoch2"),
    pytest.param(1, TimeoutError, id="interrupted"),
    pytest.param(1, "product", id="product-failed")])
@pytest.mark.parametrize("cleanup_mode", [None, "subject", "http", "both"])
def test_runner_separates_failure_before_stop_from_cleanup_evidence(
        tmp_path, monkeypatch, failure_epoch, primary_type, cleanup_mode):
    import sys
    from offerpilot.evaluation import core_planning, core_runner, core_storage
    from offerpilot.evaluation.runner import ROOT
    from offerpilot.evaluation.adapters import ProductFailure

    source = dict(commit="a" * 40, diff_hash="b" * 64, lock_hash="c" * 64, files_hash="d" * 64)
    runtime = dict(python="python", sqlite="sqlite", dependencies={})
    monkeypatch.setattr(core_planning, "snapshot", lambda path: source)
    monkeypatch.setattr(core_planning, "target_environment", lambda variant: runtime)
    monkeypatch.setattr(core_runner, "snapshot", lambda path: source)
    variants = tmp_path / "variants.json"
    variants.write_text(json.dumps([dict(variant_id="candidate", checkout=str(ROOT), interpreter=sys.executable)]))
    manifest = core_planning.create_plan(ROOT / "evals/suites/harness-core-v1.json", variants, tmp_path / "plan")
    private = tmp_path / "trial"
    error = (ProductFailure("business rejected") if primary_type == "product"
             else (primary_type or httpx.ReadTimeout)("EXCEPTION-SECRET"))
    failed = failure_epoch is not None
    clients = []
    stopped = []
    http_closed = []

    class Client(StubClient):
        def __init__(self):
            super().__init__(None)
            self.epoch = len(clients) + 1
            clients.append(self)

        def request(self, method, path, **kwargs):
            if self.epoch == failure_epoch:
                raise error
            return httpx.Response(202 if method == "POST" else 200, json={"turn_id": "turn"}
                if method == "POST" else {"state": "completed", "terminal": {"response": {"message": "ok"}}})

        def close(self):
            http_closed.append(self.epoch)
            if cleanup_mode in {"http", "both"}:
                raise OSError(22, "HTTP-CLEANUP-SECRET")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Client())

    class Subject:
        def __init__(self, variant, config, folder, events, deadline, ledger):
            self.config, self.started, self.closed, self.errors = config, time.monotonic(), False, []
            self.reader = SimpleNamespace(is_alive=lambda: not self.closed)
            self.owned_tree_exit_proven = False
            self.state = dict(epoch=config["epoch"], phase="reserve", last_validated_seq=3,
                last_persisted_seq=3, last_ack_seq=2, errors=self.errors, reader_alive=True)

        def wait(self, kind, timeout):
            if kind == "prepared":
                return dict(initial={}, aliases={"target_application": 1})
            return dict(checkout_sha=source["commit"], interpreter=sys.executable,
                product_file=str(ROOT / "src/offerpilot/api.py"), python=runtime["python"],
                sqlite=runtime["sqlite"], dependencies={}, database_file=str(private / "data.db"),
                product_budget_effective={}, address="http://127.0.0.1")

        def control_observation(self):
            return self.state

        def stop(self):
            stopped.append(self.config["epoch"])
            self.closed, self.owned_tree_exit_proven = True, True
            self.state.update(phase="receive", last_validated_seq=4, last_persisted_seq=4,
                              last_ack_seq=3, reader_alive=False)
            if failed:
                self.errors.append("abnormal_shutdown")
            if cleanup_mode in {"subject", "both"}:
                raise OSError(22, "SUBJECT-CLEANUP-SECRET")

    monkeypatch.setattr(core_runner, "CoreSubject", Subject)
    monkeypatch.setattr(core_runner.observer, "marker", lambda *args: None)
    if failure_epoch == 2:
        monkeypatch.setattr(core_runner.observer, "next_epoch", lambda path: 2)

        def drive(action, first_driver, first_subject, folder, evidence, restart):
            # Assignment in run_trial has not completed when this inner driver raises.
            second_subject, second_driver = restart()
            second_driver.request("POST", "/api/chat?token=QUERY-SECRET", {"message": "BODY-SECRET"})
            raise AssertionError("original second-epoch exception must propagate")

        monkeypatch.setattr(core_runner, "drive", drive)
    captured = {}

    def grade(trial, evidence, status, evidence_hash, folder):
        captured.update(evidence=evidence, evidence_hash=evidence_hash)
        return SimpleNamespace(execution_status=status,
                               model_dump=lambda **kwargs: {"execution_status": status})

    monkeypatch.setattr(core_runner, "grade", grade)
    result = core_runner.run_trial(manifest, manifest.planned_trials[0], private,
        SimpleNamespace(), diagnostic_probe=failure_epoch != 2)
    evidence = core_storage.load(private, captured["evidence_hash"])
    # Core storage.errors describes fragment integrity, while runtime cleanup
    # remains in the sealed facts and cannot turn a control gap into a pass.
    assert evidence["errors"] == {}
    assert evidence == captured["evidence"]
    assert len(captured["evidence_hash"]) == 64
    facts = evidence["facts"]
    expected = ("product_failed" if primary_type == "product" else "interrupted"
                if primary_type is TimeoutError else "eval_error" if failed or cleanup_mode else "completed")
    assert result.execution_status == expected
    assert facts["termination_source"] == ("evaluator_watchdog" if primary_type is TimeoutError else
                                           "unknown" if failed or cleanup_mode else "normal")
    assert stopped == list(range(1, len(clients) + 1))
    assert http_closed == list(range(1, len(clients) + 1))
    if cleanup_mode:
        assert not facts["control_closed"] and facts["consumption"]["in_flight_unknown"]
        assert facts["consumption"]["provider_attempts"] is None
        records = facts["cleanup_errors"]
        assert len(records) == len(clients) * (2 if cleanup_mode == "both" else 1)
        assert "SECRET" not in json.dumps(records)
        assert {r["error_type"] for r in records} == {"OSError"}
    after = facts["control_observations_after_stop"][-1]
    assert after["epoch"] == (failure_epoch or 1) and after["last_validated_seq"] == 4
    if failed:
        assert facts["driver_error"] == type(error).__name__
        assert evidence["http"] == []  # Failed requests remain outside response observations.
        before = facts["driver_failure_observation"]["subjects"][-1]
        assert before["phase"] == "reserve" and before["last_validated_seq"] == 3
        assert before["reader_alive"] is True and before["errors"] == []
        assert after["reader_alive"] is False and after["errors"] == ["abnormal_shutdown"]
        assert facts["http_request_errors"][0]["epoch"] == failure_epoch
        assert facts["http_request_errors"][0]["response_received"] is None
        assert "SECRET" not in json.dumps(facts["http_request_errors"])
        if failure_epoch == 2:
            assert len(facts["driver_failure_observation"]["subjects"]) == 2
            assert len(clients) == 2  # Do not lose the inner driver's captured failure.
    else:
        assert len(evidence["http"]) == 2
        assert all(set(row) == {"method", "path", "body", "status", "response"}
                   for row in evidence["http"])
        assert evidence["http"][0]["status"] == 202
        assert evidence["http"][0]["response"] == {"turn_id": "turn"}
        assert evidence["http"][1]["status"] == 200
        assert evidence["http"][1]["response"]["state"] == "completed"
        assert "driver_failure_observation" not in facts
        assert "http_request_errors" not in facts
        assert facts["control_closed"] is (cleanup_mode is None)
