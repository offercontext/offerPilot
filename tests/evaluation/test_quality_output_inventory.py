"""Synthetic sealed I/O fixtures, not product runs or human quality labels."""

import json
import sqlite3
from uuid import uuid4

import pytest

from offerpilot.evaluation.contracts import PlannedTrial
from offerpilot.evaluation.core_quality_outputs import bound_outputs, output_inventory
from offerpilot.evaluation.core_registry import case_definition, required_rules
from offerpilot.evaluation.core_runner import missing
from offerpilot.evaluation.core_storage import seal
from offerpilot.evaluation.registry import digest, file_hash


def sealed_trial(tmp_path, condition, row=None):
    item = "E09.memory_override" if condition in {"single_timeout", "handshake_only"} else "E09.memory_withdraw"
    if row is None:
        spec = case_definition(item, 3)
        plan = PlannedTrial(
            run_id=uuid4(), variant_id="candidate", execution_item_id=item, mode="real",
            repeat_index=1, case_id="E09", case_version=3, spec_hash=digest(spec),
            fixture_hash=digest("synthetic fixture only"), coverage_kind="task",
            required_rules=required_rules(item, "real", 3),
        )
        row = missing(plan, "synthetic I/O fixture only")
    private = tmp_path / ("sealed-"+condition)
    private.mkdir()
    database = private / "sealed.db"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, role TEXT, content TEXT);"
            "CREATE TABLE pilot_turn_messages(turn_id TEXT, message_id INTEGER);"
        )
    evidence = dict(
        trial_key=row.model_dump(mode="json", include={
            "run_id", "variant_id", "execution_item_id", "mode", "repeat_index",
        }),
        facts=dict(handshakes=[{"scope": "synthetic startup only"}], control_closed=True),
        http=[], input_observations=[], files={},
    )
    count = 0 if condition=="handshake_only" else 1 if condition in {
        "single_timeout", "second_not_submitted"
    } else 2 if row.execution_item_id=="E09.memory_withdraw" else 1
    for number in range(1, count+1):
        execution = dict(request_id=f"r{number}", turn_id=f"t{number}",
                         generation=1, conversation_id=1)
        initial = dict(request_id=f"r{number}", turn_id=f"t{number}",
                       execution_generation=1, conversation_id=1)
        evidence["http"].append(dict(
            method="POST", path="/api/pilot/runtime/v1/turns",
            body=dict(request_id=f"r{number}", message="synthetic request"), response=initial,
        ))
        request = private / f"request-{number}.json"
        request.write_text(json.dumps(dict(messages=[dict(role="user", content="synthetic request")])),
                           encoding="utf-8")
        evidence["files"][request.name] = dict(sha256=file_hash(request))
        evidence["input_observations"].extend([
            dict(category="final_input", request_file=request.name, payload_hash=file_hash(request),
                 epoch=1, seq=number*10,
                 identity=dict(model_call_id=f"c{number}", segment_id=f"s{number}",
                               surface_fingerprint=f"f{number}", execution=execution)),
            dict(category="process_worker", event="started", execution=execution),
        ])
        success = condition!="single_timeout" and (
            number==1 or condition not in {"second_failed", "second_timeout"}
        )
        answer = f"synthetic answer {number}" if success else ""
        evidence["http"].append(dict(
            method="GET", path=f"/api/pilot/runtime/v1/turns/t{number}",
            response={**initial, "state":"completed" if success else "failed",
                      "terminal":{"response":{"message":answer}}},
        ))
        if success:
            with sqlite3.connect(database) as db:
                db.execute("INSERT INTO chat_messages VALUES(?, 'assistant', ?)", (number,answer))
                db.execute("INSERT INTO pilot_turn_messages VALUES(?,?)", (f"t{number}",number))
    evidence["files"]["sealed.db"] = dict(sha256=file_hash(database))
    evidence_hash = seal(private, evidence)
    result = row.model_copy(update={
        "private_directory":str(private), "evidence_hash":evidence_hash,
        "execution_status":"completed" if condition=="complete" else "product_failed",
    })
    if condition=="bad_database":
        with database.open("ab") as stream:
            stream.write(b"synthetic corruption")
    return result


@pytest.mark.parametrize("condition", ["second_failed", "second_timeout", "second_not_submitted"])
def test_r04_preserves_first_output_when_the_second_reply_or_submission_is_missing(tmp_path, condition):
    row = sealed_trial(tmp_path, condition)
    inventory = output_inventory(row)
    assert inventory["expected_requests"] == 2
    assert len(inventory["observed_outputs"]) == len(inventory["outputs"]) == 1
    assert inventory["outputs"][0]["request_number"] == 1
    assert inventory["outputs"][0]["call_numbers"] == [1]
    assert inventory["execution_observed"] is True
    assert inventory["gaps"]
    with pytest.raises(ValueError, match="incomplete"):
        bound_outputs(row)


def test_timeout_keeps_one_planned_slot_and_zero_observable_outputs(tmp_path):
    inventory = output_inventory(sealed_trial(tmp_path, "single_timeout"))
    assert inventory["expected_requests"] == 1
    assert inventory["execution_observed"] is True
    assert inventory["request_submission_attempts_observed"] == 1
    assert inventory["observed_outputs"] == inventory["outputs"] == []
    assert inventory["gaps"]


def test_handshake_is_only_subject_startup_and_does_not_prove_turn_execution(tmp_path):
    inventory = output_inventory(sealed_trial(tmp_path, "handshake_only"))
    assert inventory["subject_started_observed"] is True
    assert inventory["request_accepted_observed"] is None
    assert inventory["execution_observed"] is None
    assert inventory["gaps"]


def test_unbound_database_keeps_observed_replies_but_cannot_certify_them(tmp_path):
    inventory = output_inventory(sealed_trial(tmp_path, "bad_database"))
    assert len(inventory["observed_outputs"]) == 2
    assert not inventory["outputs"]
    assert inventory["gaps"]


def test_two_r04_replies_keep_distinct_request_call_and_hash_bindings(tmp_path):
    row = sealed_trial(tmp_path, "complete")
    outputs = bound_outputs(row)
    assert len(outputs) == 2
    assert len({o["sample_id"] for o in outputs}) == 2
    assert [o["request_number"] for o in outputs] == [1,2]
    assert [o["call_numbers"] for o in outputs] == [[1],[2]]
    assert len({o["answer_sha256"] for o in outputs}) == 2
