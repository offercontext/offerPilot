"""Budget refusal provenance cannot turn an unrelated HTTP or semantic failure into a pass."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from offerpilot.evaluation.adapters import ProductFailure
from offerpilot.evaluation.core_actions import CoreHTTPStatusFailure
from offerpilot.evaluation.core_runner import _offline_budget_denial
from offerpilot.evaluation.core_subject import ReservationDenied, RunLedger
from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_targeted_ledger import TargetedLedger, stage_trial
from .test_failure_observations import control_line, make_driver, make_subject


def ledger_with_one_prior_send(tmp_path):
    directory = tmp_path / "budget"
    directory.mkdir()
    profile = SimpleNamespace(input_cny_per_million=1, output_cny_per_million=1)
    manifest = SimpleNamespace(mode="real", provider_profile=profile, run_max_requests=1)
    grant = SimpleNamespace(max_requests=1, max_cny=10,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    ledger = RunLedger(manifest, grant, directory)
    ledger.reserve(dict(purpose="provider_send", trial_key={"run_id": "prior"}, epoch=1,
                        seq=1, input_bytes=10, output_cap=10))
    return ledger


def denied_reader(tmp_path, monkeypatch):
    ledger = ledger_with_one_prior_send(tmp_path)
    line = control_line(kind="call_attempt", purpose="provider_send", model_call_id="model-call",
        provider_attempt_id="provider-attempt", input_bytes=12519, output_cap=1024, execution=None,
        api_key="KEY-SECRET", prompt="PROMPT-SECRET")
    subject, process, _ = make_subject(tmp_path, monkeypatch, line, ledger=ledger)
    assert isinstance(subject.reservation_denial, ReservationDenied)
    return subject, process, ledger


def bound_http_failure(tmp_path, monkeypatch):
    directory = tmp_path / "targeted-budget"
    directory.mkdir()
    parent = PlannedTrial(run_id=uuid4(), variant_id="candidate", execution_item_id="E06.legacy_saved",
        mode="real", repeat_index=1, case_id="E06", case_version=3, spec_hash="a" * 64,
        fixture_hash="b" * 64, coverage_kind="task", required_rules=["execution.expected_outcome"])
    profile = SimpleNamespace(input_cny_per_million=1, output_cny_per_million=1,
                              max_input_bytes=50000, max_output_tokens=1024)
    manifest = SimpleNamespace(mode="real", provider_profile=profile, run_max_requests=1,
                               run_id=parent.run_id, planned_trials=[parent], variants={})
    grant = SimpleNamespace(max_requests=1, max_cny=10,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    ledger = TargetedLedger(manifest, grant, directory, "c" * 64,
                            initialize=True, simulation_only=True)
    ledger.stage = parent.execution_item_id
    plan = stage_trial(manifest, ledger.stage, True)
    key = plan.model_dump(mode="json", include={
        "run_id", "variant_id", "execution_item_id", "mode", "repeat_index"})
    ledger.reserve(dict(purpose="provider_send", trial_key=key, epoch=1,
                        seq=1, input_bytes=10, output_cap=10))
    raw = []
    for seq, kind in [(1, "ready"), (2, "call_attempt")]:
        event = json.loads(control_line(seq, kind, purpose="provider_send", model_call_id="model-call",
            provider_attempt_id="provider-attempt", input_bytes=12519, output_cap=1024, execution=None))
        event["trial_key"] = key
        raw.append(json.dumps(event) + "\n")
    subject, _, _ = make_subject(tmp_path, monkeypatch, "".join(raw), ledger=ledger, trial_key=key)
    # The helper tests model a completed owned cleanup; the live controller
    # integration separately verifies the actual process tree termination.
    subject.owned_tree_exit_proven = True
    key = copy.deepcopy(subject.config["trial_key"])
    context = dict(trial_key=key, epoch=subject.config["epoch"])
    driver = SimpleNamespace(request_context=context, _request_index=1)
    denial = subject.reservation_denial
    failure = CoreHTTPStatusFailure(502, "POST", "/api/chat", driver, 1,
                                    denial.captured_at_monotonic - 0.1)
    return failure, plan, subject, driver


def admit(plan, subject, *, scope="target"):
    return _offline_budget_denial(subject, plan, offline_simulation=scope)


def test_denied_row_is_durable_without_counting_or_refunding_another_send(tmp_path, monkeypatch):
    subject, process, ledger = denied_reader(tmp_path, monkeypatch)
    rows = [json.loads(line) for line in (ledger.directory / "run-budget.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[-1]["allowed"] is False and ledger.requests == 1
    assert ledger.reserved_cny == Decimal("0.00002")
    assert subject.reservation_denial.ledger is ledger
    proof = subject.reservation_denial.observation()
    assert proof["row"] == rows[-1] and proof["event"]["execution"] is None
    assert "SECRET" not in json.dumps(proof)
    commands = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert not any(command.get("command") == "ack" for command in commands)
    message = subject.messages.get_nowait()
    assert message["error_type"] == "TimeoutError"
    subject.messages.put(message)
    with pytest.raises(TimeoutError, match="reservation exhausted"):
        subject.wait("ready", 0.1)
    observation = subject.control_observation()
    assert observation["error_type"] == "ReservationDenied" and observation["last_ack_seq"] == 0
    assert observation["reservation_denial"] == proof
    observation["reservation_denial"]["row"]["allowed"] = True
    assert subject.reservation_denial.row["allowed"] is False


def test_failed_durable_record_does_not_create_a_typed_refusal(tmp_path, monkeypatch):
    ledger = ledger_with_one_prior_send(tmp_path)
    monkeypatch.setattr(ledger, "record", lambda *_: (_ for _ in ()).throw(OSError("record failed")))
    with pytest.raises(OSError):
        ledger.reserve(dict(purpose="provider_send", trial_key={}, epoch=1, seq=2,
                            input_bytes=10, output_cap=10))


def test_matching_denial_admits_cancellation_without_claiming_http_causality(tmp_path, monkeypatch):
    _, plan, subject, _ = bound_http_failure(tmp_path, monkeypatch)
    result = admit(plan, subject)
    assert result is subject.reservation_denial
    assert result.observation()["event"]["execution"] is None
    assert result.observation()["row"]["allowed"] is False
    assert "SECRET" not in json.dumps(result.observation())


@pytest.mark.parametrize("damage", [
    "ledger", "subject_epoch", "event_epoch", "event_trial", "row_allowed", "row_bool_seq",
    "ack", "persist", "event_attempt", "duplicate_event", "json_proof", "observer_error",
    "simulation", "stage", "missing_epoch", "missing_input", "missing_cap", "missing_row_hash",
    "missing_event_id", "stale_ack", "reader_running", "row_hash", "ledger_tail",
])
def test_missing_or_foreign_provenance_keeps_original_http_failure(tmp_path, monkeypatch, damage):
    failure, plan, subject, driver = bound_http_failure(tmp_path, monkeypatch)
    if damage == "ledger":
        subject.ledger = SimpleNamespace()
    elif damage == "subject_epoch":
        subject.config["epoch"] = 3
    elif damage == "event_epoch":
        subject.reservation_denial.event["epoch"] = 3
    elif damage == "event_trial":
        subject.reservation_denial.event["trial_key"]["run_id"] = "another"
    elif damage == "row_allowed":
        subject.reservation_denial.row["allowed"] = True
    elif damage == "row_bool_seq":
        subject.reservation_denial.row["seq"] = True
    elif damage == "ack":
        subject._control_state["last_ack_seq"] = 2
    elif damage == "persist":
        subject._control_state["last_persisted_seq"] = 0
    elif damage == "event_attempt":
        subject.events[-1]["provider_attempt_id"] = "another"
    elif damage == "duplicate_event":
        subject.events.append(copy.deepcopy(subject.events[-1]))
    elif damage == "json_proof":
        subject.reservation_denial = subject.reservation_denial.observation()
    elif damage == "observer_error":
        monkeypatch.setattr(subject, "control_observation", lambda: (_ for _ in ()).throw(ValueError("bad observation")))
    elif damage == "simulation":
        subject.ledger.simulation_only = False
    elif damage == "stage":
        subject.reservation_denial.row["stage"] = "probe"
    elif damage.startswith("missing_"):
        name = {"missing_input": "input_bytes", "missing_cap": "output_cap"}.get(damage, damage[8:])
        subject.reservation_denial.row.pop(name)
    elif damage == "stale_ack":
        subject._control_state["last_ack_seq"] = 0
    elif damage == "reader_running":
        subject._control_state["reader_exited"] = False
        monkeypatch.setattr(subject.reader, "is_alive", lambda: True)
    elif damage == "row_hash":
        subject.reservation_denial.row["row_hash"] = "d" * 64
    elif damage == "ledger_tail":
        subject.ledger.tail = "d" * 64
    assert admit(plan, subject) is None
    assert isinstance(failure, ProductFailure) and failure.status >= 400


@pytest.mark.parametrize("gap", ["ordinary_real_run", "stale_ack", "reader_running"])
def test_review_boundary_never_adopts_ineligible_or_unfinished_proof(tmp_path, monkeypatch, gap):
    failure, plan, subject, driver = bound_http_failure(tmp_path, monkeypatch)
    if gap == "stale_ack":
        subject._control_state["last_ack_seq"] = 0
    elif gap == "reader_running":
        subject._control_state["reader_exited"] = False
        monkeypatch.setattr(subject.reader, "is_alive", lambda: True)
    assert admit(plan, subject,
                    scope=None if gap == "ordinary_real_run" else "target") is None


@pytest.mark.parametrize("json_body", [True, False])
def test_http_status_context_has_no_body_auth_or_unrelated_context_fields(monkeypatch, json_body):
    response = (httpx.Response(502, json={"error": "BODY-SECRET"}) if json_body
                else httpx.Response(502, text="BODY-SECRET"))
    driver, client, observations = make_driver(monkeypatch, response)
    driver.request_context = dict(epoch=2, trial_key={"run_id": "run"}, credential="KEY-SECRET")
    with pytest.raises(CoreHTTPStatusFailure) as caught:
        driver.request("POST", "/api/chat?token=QUERY-SECRET", {"message": "REQUEST-SECRET"})
    failure = caught.value
    assert failure.driver is driver and failure.request_index == 1
    assert failure.context == {"epoch": 2, "trial_key": {"run_id": "run"}}
    assert "SECRET" not in json.dumps(failure.observation()) and "SECRET" not in str(failure)
    assert "BODY-SECRET" in json.dumps(observations[-1]) and len(client.calls) == 1
    assert driver.request_errors == []  # A real HTTP reply is not a fabricated transport exception.


@pytest.mark.parametrize("error_kind", ["http502", "http500", "semantic"])
@pytest.mark.parametrize("known_failure,final_observation", [(False, True), (True, True), (False, False)])
def test_run_trial_cleanup_grading_and_aggregate_keep_unknown_and_known_failure(
        tmp_path, monkeypatch, known_failure, final_observation, error_kind):
    """Stub process/HTTP I/O, real ledger, reader, run_trial, seal, grader and aggregate."""
    from offerpilot.evaluation import core_runner
    from offerpilot.evaluation.core_storage import load

    failure, plan, subject, driver = bound_http_failure(tmp_path, monkeypatch)
    if error_kind == "http500":
        failure.status = 500
    elif error_kind == "semantic":
        failure = ProductFailure("independent semantic violation")
    plan = plan.model_copy(update=dict(required_rules=["execution.expected_outcome",
        "evidence.required_complete", "business.authorized_deltas"]))
    source = dict(commit="a" * 40, diff_hash="b" * 64, lock_hash="c" * 64, files_hash="d" * 64)
    runtime = dict(python="unit", sqlite="unit", dependencies={})
    variant = dict(variant_id="candidate", checkout=str(tmp_path), interpreter=sys.executable,
                   runtime=runtime, **source)
    profile = subject.ledger.manifest.provider_profile
    profile.credential_ref, profile.endpoint, profile.model = (
        "offline:no-credential", "https://offline.invalid/v1", "eval-core")
    profile.model_dump = lambda **kwargs: {}
    manifest = SimpleNamespace(variants={"candidate": variant}, provider_profile=profile,
                               run_id=plan.run_id, repeats=1, planned_trials=[plan])
    private = tmp_path / "sealed-unit-trial"
    driver.local_http_observations = []
    driver.request_error_observations = lambda: []
    driver.request_error_capture_errors = []
    ready = dict(checkout_sha=source["commit"], interpreter=sys.executable,
        product_file=str(tmp_path / "src/offerpilot/api.py"), database_file=str(private / "data.db"),
        product_budget_effective={}, address="http://127.0.0.1", capabilities=[], **runtime)
    subject.wait = lambda kind, timeout: (dict(initial={}, aliases={}) if kind == "prepared" else ready)

    def start(*args):
        events = args[3]
        events.extend(subject.events)
        if known_failure:
            events.insert(0, dict(type="call_attempt", purpose="storage", blocked=True, epoch=1, seq=1))
        subject.events = events
        return subject

    def drive(*args):
        args[4]["http"].append(dict(method="POST", path="/api/chat", status=502,
                                    response={"error": "original unit HTTP error"}))
        raise failure

    cleaned = []
    def cleanup(*args):
        cleaned.append(True)
        subject.closed = True
        if not final_observation:
            subject.control_observation = lambda: None
        return []

    monkeypatch.setattr(core_runner, "CoreSubject", start)
    monkeypatch.setattr(core_runner, "CoreHTTPDriver", lambda *args, **kwargs: driver)
    monkeypatch.setattr(core_runner, "drive", drive)
    monkeypatch.setattr(core_runner, "cleanup_resources", cleanup)
    monkeypatch.setattr(core_runner, "snapshot", lambda _: source)
    result = core_runner.run_trial(manifest, plan, private, subject.ledger, offline_simulation="target")
    evidence = load(private, result.evidence_hash)
    assert cleaned == [True] and evidence["errors"] == {}
    assert evidence["http"][0]["response"] == {"error": "original unit HTTP error"}
    assert evidence["facts"]["control_closed"] is False
    # A coincident refusal proves no causal link to this received HTTP 502.
    # Even a complete final denial proof cannot remove a known product failure.
    assert result.execution_status == "product_failed"
    assert evidence["facts"]["product_failure"] == str(failure)
    assert "evaluator_interruption" not in evidence["facts"]
    expected = "fail"
    assert result.verdict == expected
    report = core_runner.aggregate(manifest, [result])
    assert report["counts"][expected] == 1 and report["counts"]["pass"] == 0
    assert report["exit_code"] == (1 if expected == "fail" else 3)


def test_execution_metadata_keeps_only_identifiers_without_inventing_missing_values(tmp_path):
    ledger = ledger_with_one_prior_send(tmp_path)
    denial = ReservationDenied(dict(execution=dict(turn_id="turn", generation=1,
        conversation_id=None, request_id="request", prompt="PROMPT-SECRET", token="TOKEN-SECRET")),
        dict(allowed=False), ledger)
    assert denial.observation()["event"]["execution"] == dict(
        turn_id="turn", generation=1, conversation_id=None, request_id="request")
    assert "SECRET" not in json.dumps(denial.observation())


@pytest.mark.parametrize("execution", ["PROMPT-SECRET", ["PROMPT-SECRET"], 42, True])
def test_invalid_execution_metadata_is_omitted_without_replacing_it_with_null(tmp_path, execution):
    ledger = ledger_with_one_prior_send(tmp_path)
    denial = ReservationDenied(dict(execution=execution), dict(allowed=False), ledger)
    assert "execution" not in denial.observation()["event"]
    assert "SECRET" not in json.dumps(denial.observation())
