from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from offerpilot.db import init_database
from offerpilot.models import Application, ApplicationEvent, InterviewNote, InterviewReadinessSignal, InterviewReadinessSignalVersion
from offerpilot.repositories.adaptive_interview_practice import AdaptivePracticeRepository
from offerpilot.training_evidence.contracts import parse_command
from offerpilot.training_evidence.models import TrainingEvidenceCommand, TrainingEvidenceRecord
from offerpilot.training_evidence.repository import TrainingEvidenceError, TrainingEvidenceRepository
from tests.test_adaptive_interview_practice_repository import _seed_v2


@pytest.fixture
def setup(tmp_path):  # type: ignore[no-untyped-def]
    factory, seeded, version, target, source_fp, target_fp = _seed_v2(tmp_path)
    practice = AdaptivePracticeRepository(factory)
    plan, _ = practice.start_v2(
        readiness_signal_version_id=version, target_application_event_id=target,
        expected_source_fingerprint=source_fp, expected_target_fingerprint=target_fp,
        idempotency_key=str(uuid4()),
    )
    plan, _ = practice.complete(
        plan_id=plan["id"], expected_revision=1, response_text="保存回执，并以唯一约束防止重复写入。",
        reflection_text="", self_assessment="confident", idempotency_key=str(uuid4()),
    )
    return factory, seeded, plan, TrainingEvidenceRepository(factory)


def record(revision=0, **kwargs):  # type: ignore[no-untyped-def]
    return parse_command({
        "action": "record", "expected_revision": revision, "idempotency_key": str(uuid4()),
        "skill_id": "idempotency_retry", "scenario_id": "retry_timeout",
        "answer": "先查询同一幂等键对应的回执，再决定是否重试。", "assistance": "independent",
        **kwargs,
    })


def change(action, state, record_id, **kwargs):  # type: ignore[no-untyped-def]
    return parse_command({
        "action": action, "expected_revision": state["revision"],
        "idempotency_key": str(uuid4()), "record_id": record_id, **kwargs,
    })


def test_no_implicit_import_or_mastery_from_confident_assessment(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    state = repo.get(plan["id"])
    assert state["revision"] == 0 and state["records"] == []
    assert state["recommendation"] is None
    assert state["available"]
    assert "mastered" not in state


@pytest.mark.parametrize("assistance", ["unknown", "independent", "hinted", "reference"])
def test_explicit_conditions_bound_to_record_and_alternate_scenario(setup, assistance):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(assistance=assistance))
    saved = state["records"][0]
    assert saved["assistance"] == assistance and saved["answer"]
    advice = state["recommendation"]
    assert advice["scenario_id"] == "concurrent_retry"
    assert advice["evidence_record_ids"] == [saved["id"]]
    assert str(saved["id"]) in advice["reason"]
    assert "不能" in advice["reason"] if assistance == "unknown" else "不证明" in advice["reason"]
    assert saved["created_at"].endswith("Z")


def test_original_is_explicit_exact_source_and_not_forged(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    with pytest.raises(TrainingEvidenceError, match="invalid"):
        repo.mutate(plan["id"], record(scenario_id="original", answer="伪造原回答"))
    state = repo.mutate(plan["id"], record(scenario_id="original", answer=plan["response_text"], assistance="unknown"))
    with pytest.raises(TrainingEvidenceError, match="conflict"):
        repo.mutate(plan["id"], record(1, scenario_id="original", answer=plan["response_text"]))
    original = state["records"][0]
    with pytest.raises(TrainingEvidenceError, match="invalid"):
        repo.mutate(plan["id"], change("correct", state, original["id"], answer="篡改", assistance="independent"))
    state = repo.mutate(plan["id"], change("correct", state, original["id"], answer=plan["response_text"], assistance="hinted"))
    assert state["records"][0]["assistance"] == "hinted"


def test_replay_correction_withdrawal_never_resurrects_old_answer(setup):  # type: ignore[no-untyped-def]
    factory, _, plan, repo = setup
    command = record(answer="SECRET_FIRST_ANSWER")
    state = repo.mutate(plan["id"], command)
    assert repo.mutate(plan["id"], command) == state
    corrected = repo.mutate(plan["id"], change("correct", state, state["records"][0]["id"], answer="CORRECTED", assistance="reference"))
    assert repo.mutate(plan["id"], command) == corrected
    withdrawal = change("withdraw", corrected, corrected["records"][0]["id"])
    withdrawn = repo.mutate(plan["id"], withdrawal)
    assert withdrawn["recommendation"] is None
    assert withdrawn["records"][0]["answer"] == ""
    assert withdrawn["records"][0]["assistance"] == "unknown"
    assert repo.mutate(plan["id"], command) == withdrawn
    assert repo.mutate(plan["id"], withdrawal) == withdrawn
    with factory() as session:
        assert session.scalar(select(TrainingEvidenceRecord.answer)) == ""
        receipts = session.scalars(select(TrainingEvidenceCommand)).all()
        assert all("SECRET" not in repr(row.__dict__) and "CORRECTED" not in repr(row.__dict__) for row in receipts)
    with pytest.raises(TrainingEvidenceError, match="conflict"):
        repo.mutate(plan["id"], change("correct", withdrawn, withdrawn["records"][0]["id"], answer="restore", assistance="independent"))


def test_key_binds_all_inputs_and_cas_does_not_overwrite(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    command = record()
    state = repo.mutate(plan["id"], command)
    with pytest.raises(TrainingEvidenceError, match="idempotency_conflict"):
        repo.mutate(plan["id"], record(idempotency_key=command.idempotency_key, assistance="hinted"))
    with pytest.raises(TrainingEvidenceError, match="training_evidence_conflict"):
        repo.mutate(plan["id"], record())
    assert repo.get(plan["id"]) == state


@pytest.mark.parametrize("mutation", ["source", "target", "cancelled", "retracted", "foreign_target"])
def test_stale_scope_blocks_consumption_and_new_data_but_allows_withdraw(setup, mutation):  # type: ignore[no-untyped-def]
    factory, seeded, plan, repo = setup
    command = record()
    state = repo.mutate(plan["id"], command)
    if mutation == "retracted":
        from tests.product_actions.test_compensation import _compensation_stack
        with factory() as session:
            signal = session.scalar(select(InterviewReadinessSignal))
            version = session.get(InterviewReadinessSignalVersion, signal.current_version_id)
            signal_id, operation_id = signal.id, version.write_operation_id
        issuer, coordinator = _compensation_stack(factory)
        proof = issuer.issue(application_id=plan["application_id"], signal_id=signal_id, parent_operation_id=operation_id)
        coordinator.execute(proof)
    with factory() as session:
        if mutation == "source":
            note = session.get(InterviewNote, seeded["note_id"])
            note.difficulty_points += " changed"
        elif mutation == "retracted":
            pass
        else:
            event = session.get(ApplicationEvent, plan["target_application_event_id"])
            if mutation == "target":
                event.round += 1
            elif mutation == "cancelled":
                event.status = "cancelled"
            else:
                foreign = Application(company_name="Other", position_name="Other")
                session.add(foreign)
                session.flush()
                event.application_id = foreign.id
        session.commit()
    stale = repo.get(plan["id"])
    assert not stale["available"] and stale["recommendation"] is None
    assert repo.mutate(plan["id"], command)["recommendation"] is None
    with pytest.raises(TrainingEvidenceError, match="source_unavailable"):
        repo.mutate(plan["id"], record(1))
    withdrawn = repo.mutate(plan["id"], change("withdraw", state, state["records"][0]["id"]))
    assert withdrawn["records"][0]["answer"] == ""


def test_deleted_application_hides_reads_and_replays(setup):  # type: ignore[no-untyped-def]
    factory, _, plan, repo = setup
    command = record()
    repo.mutate(plan["id"], command)
    with factory() as session:
        session.get(Application, plan["application_id"]).deleted_at = datetime.now(timezone.utc)
        session.commit()
    for action in (lambda: repo.get(plan["id"]), lambda: repo.mutate(plan["id"], command)):
        with pytest.raises(TrainingEvidenceError, match="not_found"):
            action()


def test_concurrent_same_revision_is_single_write(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    def write():
        try:
            repo.mutate(plan["id"], record())
            return "saved"
        except TrainingEvidenceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == ["saved", "training_evidence_conflict"]
    assert len(repo.get(plan["id"])["records"]) == 1


def test_concurrent_same_key_has_one_record(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    command = record()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.mutate(plan["id"], command), range(2)))
    assert results[0] == results[1]
    assert len(results[0]["records"]) == 1


def test_active_record_limit_and_comparison_cites_current_records(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    for revision in range(12):
        state = repo.mutate(plan["id"], record(revision))
    assert state["recommendation"]["evidence_record_ids"] == [state["records"][-2]["id"], state["records"][-1]["id"]]
    with pytest.raises(TrainingEvidenceError, match="conflict"):
        repo.mutate(plan["id"], record(12))


@pytest.mark.parametrize("assistance", ["unknown", "independent", "hinted", "reference"])
def test_same_assistance_comparison_does_not_imply_change(setup, assistance):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    repo.mutate(plan["id"], record(assistance=assistance))
    state = repo.mutate(plan["id"], record(1, assistance=assistance))
    comparison = state["recommendation"]["comparison"]
    assert "作答条件对照" in comparison
    assert "条件变化" not in comparison
    assert "不是能力评分" in comparison
    assert state["recommendation"]["evidence_record_ids"] == [item["id"] for item in state["records"]]


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"expected_revision": -1}, {"expected_revision": "0"},
    {"idempotency_key": "not-a-key"}, {"skill_id": "other"}, {"extra": 1},
    {"assistance": "mastered"}, {"scenario_id": "invented"}, {"answer": " "},
    {"answer": "x" * 8001}, {"answer": "\ud800"},
])
def test_strict_commands(changes):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        record(**changes)


def test_additive_migration_repeat_preserves_old_data(setup, tmp_path):  # type: ignore[no-untyped-def]
    factory, _, plan, repo = setup
    state = repo.mutate(plan["id"], record())
    new_factory = init_database(tmp_path / "v2.db")
    assert TrainingEvidenceRepository(new_factory).get(plan["id"]) == state
    with new_factory() as session:
        assert session.scalar(text("SELECT count(*) FROM schema_migrations WHERE version='0039_training_evidence'")) == 1


@pytest.mark.parametrize("field", ["source_fingerprint", "target_fingerprint", "application_id"])
def test_plan_binding_tamper_blocks_new_evidence(setup, field):  # type: ignore[no-untyped-def]
    from offerpilot.models import AdaptivePracticePlan
    factory, _, plan, repo = setup
    with factory() as session:
        # Deliberately simulate an already-corrupt isolated database. Normal
        # writes are additionally rejected by the immutable-source trigger.
        session.execute(text("DROP TRIGGER trg_adaptive_practice_v2_identity_immutable"))
        row = session.get(AdaptivePracticePlan, plan["id"])
        if field == "application_id":
            other = Application(company_name="Foreign", position_name="Other")
            session.add(other)
            session.flush()
            row.application_id = other.id
        else:
            setattr(row, field, "sha256:" + "0" * 64)
        session.commit()
    assert not repo.get(plan["id"])["available"]
    with pytest.raises(TrainingEvidenceError, match="source_unavailable"):
        repo.mutate(plan["id"], record())


def test_foreign_record_id_and_command_key_rejected(setup, tmp_path):  # type: ignore[no-untyped-def]
    from offerpilot.models import AdaptivePracticePlan
    factory, _, plan, repo = setup
    command = record()
    state = repo.mutate(plan["id"], command)
    # Copy an existing valid plan to a second identity; even though it cannot
    # admit fresh evidence, a foreign key replay is rejected before projection.
    from tests.test_adaptive_interview_practice_repository import _add_target
    second_target = _add_target(factory, plan["application_id"])
    with factory() as session:
        source = session.get(AdaptivePracticePlan, plan["id"])
        values = {column.name: getattr(source, column.name) for column in AdaptivePracticePlan.__table__.columns if column.name != "id"}
        values.update(start_idempotency_key=str(uuid4()), completion_idempotency_key=str(uuid4()), target_application_event_id=second_target)
        second = AdaptivePracticePlan(**values)
        session.add(second)
        session.commit()
        second_id = second.id
    with pytest.raises(TrainingEvidenceError, match="idempotency_conflict"):
        repo.mutate(second_id, command)
    with pytest.raises(TrainingEvidenceError, match="not_found"):
        repo.mutate(plan["id"], change("withdraw", state, 99999))
    with pytest.raises(TrainingEvidenceError, match="not_found"):
        repo.mutate(second_id, change("withdraw", {"revision": 0}, state["records"][0]["id"]))


def test_api_auth_and_validation_remain_enforced(tmp_path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from offerpilot.api import create_app
    from offerpilot.config import Config, save_config
    save_config(tmp_path, Config(auth_enabled=True, auth_token="test-training-token"))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        url = "/api/interview-practice/plans/1/training-evidence"
        assert client.get(url).status_code == 401
        assert client.post(url, json=record().model_dump()).status_code == 401
        headers = {"Authorization": "Bearer test-training-token"}
        assert client.get(url, headers=headers).status_code == 404
        assert client.post(url, headers=headers, json={"action": "record"}).status_code == 422
        assert client.post(url, headers=headers, json=record().model_dump()).status_code == 404


def test_http_roundtrip_and_replay(setup, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from offerpilot.api import create_app
    from offerpilot import api as api_module
    factory, _, plan, repo = setup
    from tests.product_actions.conftest import KEY_ONE
    monkeypatch.setattr(api_module, "session_factory_for_data_dir", lambda _: factory)
    monkeypatch.setattr(api_module, "load_or_create_ledger_key", lambda *_: KEY_ONE)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        url = f"/api/interview-practice/plans/{plan['id']}/training-evidence"
        assert client.get(url).json() == repo.get(plan["id"])
        command = record().model_dump()
        response = client.post(url, json=command)
        assert response.status_code == 200
        assert client.post(url, json=command).json() == response.json()
        assert client.post(url, json={**command, "assistance": "hinted"}).status_code == 409
        assert client.post(url, json={**command, "expected_revision": True}).status_code == 422


def test_failed_receipt_rolls_back_record_and_revision(setup):  # type: ignore[no-untyped-def]
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    factory, _, plan, repo = setup
    command = record()
    def fail_receipt(*_):
        raise OperationalError("INSERT", {}, RuntimeError("simulated storage failure"))
    event.listen(TrainingEvidenceCommand, "before_insert", fail_receipt)
    try:
        with pytest.raises(TrainingEvidenceError, match="unavailable"):
            repo.mutate(plan["id"], command)
    finally:
        event.remove(TrainingEvidenceCommand, "before_insert", fail_receipt)
    assert repo.get(plan["id"])["revision"] == 0
    assert repo.get(plan["id"])["records"] == []
    assert repo.mutate(plan["id"], command)["revision"] == 1


def test_deleted_plan_cascades_live_answers_and_receipts(setup):  # type: ignore[no-untyped-def]
    from offerpilot.models import AdaptivePracticePlan
    factory, _, plan, repo = setup
    repo.mutate(plan["id"], record())
    with factory() as session:
        session.delete(session.get(AdaptivePracticePlan, plan["id"]))
        session.commit()
        assert session.scalars(select(TrainingEvidenceRecord)).all() == []
        assert session.scalars(select(TrainingEvidenceCommand)).all() == []
    with pytest.raises(TrainingEvidenceError, match="not_found"):
        repo.get(plan["id"])


def test_replay_after_source_missing_is_history_only(setup):  # type: ignore[no-untyped-def]
    factory, _, plan, repo = setup
    command = record()
    repo.mutate(plan["id"], command)
    with factory() as session:
        session.delete(session.get(ApplicationEvent, plan["target_application_event_id"]))
        session.commit()
    state = repo.mutate(plan["id"], command)
    assert not state["available"]
    assert state["recommendation"] is None
    assert state["reason"] == "target_missing"
    state = repo.mutate(plan["id"], change("withdraw", state, state["records"][0]["id"]))
    assert state["records"][0]["answer"] == ""


def test_withdrawal_releases_capacity_without_reusing_identity_or_replaying_old_answer(setup):  # type: ignore[no-untyped-def]
    factory, _, plan, repo = setup
    commands = [record(revision, answer=f"回答 {revision}") for revision in range(12)]
    for command in commands:
        state = repo.mutate(plan["id"], command)
    assert len(state["records"]) == 12
    first_id = state["records"][0]["id"]
    corrected = repo.mutate(plan["id"], change(
        "correct", state, first_id, answer="已更正回答", assistance="hinted",
    ))
    assert corrected["records"][0]["answer"] == "已更正回答"
    withdrawal = change("withdraw", corrected, first_id)
    withdrawn = repo.mutate(plan["id"], withdrawal)
    new_command = record(withdrawn["revision"], answer="使用释放的有效名额")
    final = repo.mutate(plan["id"], new_command)
    assert len(final["records"]) == 13
    assert sum(not item["withdrawn"] for item in final["records"]) == 12
    assert final["records"][-1]["id"] > first_id
    tombstone = final["records"][0]
    assert tombstone["withdrawn"] and tombstone["answer"] == ""
    # Replay at full capacity must return current state without recreating the
    # original answer, consuming another slot or repeating the withdrawal.
    assert repo.mutate(plan["id"], commands[0]) == final
    assert repo.mutate(plan["id"], withdrawal) == final
    assert repo.mutate(plan["id"], new_command) == final
    with pytest.raises(TrainingEvidenceError, match="training_evidence_conflict"):
        repo.mutate(plan["id"], record(final["revision"]))
    with factory() as session:
        assert len(session.scalars(select(TrainingEvidenceCommand)).all()) == 15
        assert session.get(TrainingEvidenceRecord, first_id).answer == ""


def test_last_active_slot_concurrent_creation_preserves_limit(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    for revision in range(11):
        state = repo.mutate(plan["id"], record(revision))
    commands = [record(state["revision"]), record(state["revision"])]
    def write(command):
        try:
            repo.mutate(plan["id"], command)
            return "saved"
        except TrainingEvidenceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, commands))
    assert sorted(results) == ["saved", "training_evidence_conflict"]
    final = repo.get(plan["id"])
    assert final["revision"] == 12
    assert sum(not item["withdrawn"] for item in final["records"]) == 12
    # Two fresh commands against a full quota both fail; neither leaves a
    # receipt or increments the revision, even when racing.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [record(12), record(12)]))
    assert results == ["training_evidence_conflict", "training_evidence_conflict"]
    assert repo.get(plan["id"]) == final


def test_withdrawn_original_can_be_explicitly_saved_again_as_new_record(setup):  # type: ignore[no-untyped-def]
    _, _, plan, repo = setup
    original = record(scenario_id="original", answer=plan["response_text"], assistance="unknown")
    first = repo.mutate(plan["id"], original)
    first_id = first["records"][0]["id"]
    withdrawn = repo.mutate(plan["id"], change("withdraw", first, first_id))
    second = repo.mutate(plan["id"], record(
        withdrawn["revision"], scenario_id="original", answer=plan["response_text"], assistance="hinted",
    ))
    assert second["records"][0]["withdrawn"]
    assert second["records"][0]["answer"] == ""
    assert second["records"][1]["id"] != first_id
    assert second["records"][1]["assistance"] == "hinted"
    assert repo.mutate(plan["id"], original) == second


def decision(scenario="retry_timeout", choice="query_same_key"):
    return {"rubric_version": "idempotency_retry.v1", "question_id": f"{scenario}.v1", "choice_id": choice}


@pytest.mark.parametrize("body", ["唯一约束、事务、回执", "我反对事务，也不查询回执", "完全无关的正文"])
@pytest.mark.parametrize("choice,outcome", [("query_same_key", "supported"), ("new_key_retry", "contradicted"), ("uncertain", "abstain")])
def test_feedback_only_evaluates_explicit_option_not_text(setup, body, choice, outcome):
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(answer=body, assistance="unknown", decision_answer=decision(choice=choice)))
    feedback = state["records"][0]["feedback"]
    assert feedback["outcome"] == outcome
    assert feedback["text_assessment"] == "not_evaluated"
    assert feedback["scope"] == "selected_option_only"
    assert feedback["record_id"] == state["records"][0]["id"]
    if outcome == "contradicted":
        assert "原键" in feedback["next_step"]


@pytest.mark.parametrize("invalid", [decision("concurrent_retry"), decision(choice="atomic_unique_receipt"), decision(choice="missing")])
def test_decision_must_belong_to_scenario(setup, invalid):
    _, _, plan, repo = setup
    with pytest.raises(TrainingEvidenceError, match="invalid"):
        repo.mutate(plan["id"], record(decision_answer=invalid))
    assert repo.get(plan["id"])["revision"] == 0


def test_decision_version_and_original_rejected(setup):
    _, _, plan, repo = setup
    with pytest.raises(ValueError):
        record(decision_answer={**decision(), "rubric_version": "new"})
    with pytest.raises(TrainingEvidenceError, match="invalid"):
        repo.mutate(plan["id"], record(scenario_id="original", answer=plan["response_text"], decision_answer=decision()))


def test_six_scenario_pool_exhaustion_withdrawal_and_explicit_retest(setup):
    from offerpilot.training_evidence.scenarios import POOL_IDS
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(scenario_id="original", answer=plan["response_text"]))
    assert state["pool"]["practiced_scenario_ids"] == []
    for index, sid in enumerate(POOL_IDS):
        assert state["recommendation"]["scenario_id"] == sid
        state = repo.mutate(plan["id"], record(state["revision"], scenario_id=sid))
        assert state["pool"]["practiced_scenario_ids"] == list(POOL_IDS[:index+1])
    assert state["pool"]["exhausted"] and state["recommendation"] is None
    for saved in state["records"]:
        state = repo.mutate(plan["id"], change("withdraw", state, saved["id"]))
    assert state["pool"]["exhausted"] and state["recommendation"] is None
    state = repo.mutate(plan["id"], record(state["revision"]))
    assert state["pool"]["exhausted"] and state["recommendation"] is None


def test_decision_correction_and_withdrawal_do_not_resurrect(setup):
    _, _, plan, repo = setup
    old = record(decision_answer=decision())
    state = repo.mutate(plan["id"], old)
    correction = change("correct", state, state["records"][0]["id"], answer="same text", assistance="hinted", decision_answer=decision(choice="new_key_retry"))
    state = repo.mutate(plan["id"], correction)
    assert state["records"][0]["feedback"]["outcome"] == "contradicted"
    with pytest.raises(TrainingEvidenceError, match="idempotency_conflict"):
        repo.mutate(plan["id"], record(idempotency_key=old.idempotency_key, decision_answer=decision(choice="uncertain")))
    state = repo.mutate(plan["id"], change("withdraw", state, state["records"][0]["id"]))
    assert state["records"][0]["decision_answer"] is None
    assert state["records"][0]["feedback"]["outcome"] == "abstain"
    assert repo.mutate(plan["id"], old) == state
    assert repo.mutate(plan["id"], correction) == state


def test_legacy_receipt_hash_unchanged_by_optional_null(setup):
    from offerpilot.repositories.json_contract import canonical_json, sha256_text
    factory, _, plan, repo = setup
    command = record()
    state = repo.mutate(plan["id"], command)
    legacy = command.model_dump()
    legacy.pop("decision_answer")
    with factory() as session:
        receipt = session.get(TrainingEvidenceCommand, command.idempotency_key)
        assert receipt.fingerprint == sha256_text(canonical_json({"plan_id": plan["id"], **legacy}))
    assert repo.mutate(plan["id"], parse_command({**legacy, "decision_answer": None})) == state
    assert state["records"][0]["feedback"]["outcome"] == "abstain"


def test_source_change_suspends_feedback_and_preserves_withdrawal(setup):
    factory, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(decision_answer=decision()))
    with factory() as session:
        session.get(ApplicationEvent, plan["target_application_event_id"]).round += 1
        session.commit()
    state = repo.get(plan["id"])
    assert state["records"][0]["feedback"]["outcome"] == "abstain"
    state = repo.mutate(plan["id"], change("withdraw", state, state["records"][0]["id"]))
    assert state["records"][0]["decision_answer"] is None


def _downgrade_training_schema(factory):
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql("""CREATE TABLE training_evidence_records_old (
            id INTEGER NOT NULL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES training_evidence_states(plan_id) ON DELETE CASCADE,
            scenario_id VARCHAR NOT NULL CHECK(scenario_id IN ('original','retry_timeout','concurrent_retry')),
            answer TEXT NOT NULL, assistance VARCHAR NOT NULL CHECK(assistance IN ('unknown','independent','hinted','reference')),
            withdrawn BOOLEAN NOT NULL, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)""")
        connection.exec_driver_sql("INSERT INTO training_evidence_records_old SELECT id,plan_id,scenario_id,answer,assistance,withdrawn,created_at,updated_at FROM training_evidence_records")
        connection.exec_driver_sql("DROP TABLE training_evidence_records")
        connection.exec_driver_sql("ALTER TABLE training_evidence_records_old RENAME TO training_evidence_records")
        connection.exec_driver_sql("CREATE INDEX ix_training_evidence_records_plan_id ON training_evidence_records(plan_id)")
        connection.exec_driver_sql("DELETE FROM schema_migrations WHERE version='0040_training_evidence_decisions'")
    return engine


def test_real_old_schema_upgrade_preserves_receipts_tombstones_and_fk(setup):
    from pathlib import Path
    factory, _, plan, repo = setup
    command = record()
    state = repo.mutate(plan["id"], command)
    state = repo.mutate(plan["id"], change("withdraw", state, state["records"][0]["id"]))
    old_id = state["records"][0]["id"]
    engine = _downgrade_training_schema(factory)
    path = Path(engine.url.database)
    for _ in range(2):
        upgraded = init_database(path)
        try:
            new_repo = TrainingEvidenceRepository(upgraded)
            assert new_repo.mutate(plan["id"], command) == state
        finally:
            upgraded.kw["bind"].dispose()
    with engine.connect() as connection:
        assert not list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
        assert connection.exec_driver_sql("SELECT count(*) FROM schema_migrations WHERE version='0040_training_evidence_decisions'").scalar() == 1
        assert connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='ix_training_evidence_records_plan_id'").scalar()
    state = repo.mutate(plan["id"], record(state["revision"], scenario_id="expired_receipt", decision_answer=decision("expired_receipt", "reconcile_business_outcome")))
    assert state["records"][-1]["id"] > old_id
    assert state["records"][-1]["feedback"]["outcome"] == "supported"


@pytest.mark.parametrize("fail_at", [1, 2])
def test_old_schema_upgrade_failure_rolls_back_all_ddl_and_marker(setup, monkeypatch, fail_at):
    import offerpilot.training_evidence.migration as migration
    factory, _, plan, repo = setup
    state = repo.mutate(plan["id"], record())
    engine = _downgrade_training_schema(factory)
    calls = 0
    def fail():
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise RuntimeError("simulated migration failure")
    with monkeypatch.context() as patch:
        patch.setattr(migration, "_checkpoint", fail)
        with pytest.raises(RuntimeError, match="simulated"):
            migration.ensure_decision_schema(engine)
    with engine.connect() as connection:
        assert "decision_json" not in {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(training_evidence_records)")}
        assert not connection.exec_driver_sql("SELECT 1 FROM schema_migrations WHERE version='0040_training_evidence_decisions'").scalar()
        assert not connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE name='training_evidence_records_v40'").scalar()
        assert connection.exec_driver_sql("SELECT answer FROM training_evidence_records").scalar() == state["records"][0]["answer"]
    migration.ensure_decision_schema(engine)
    assert repo.get(plan["id"])["revision"] == 1


def test_decision_corrections_compete_on_revision_without_partial_update(setup):
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(decision_answer=decision()))
    commands = [change("correct", state, state["records"][0]["id"], answer=choice, assistance="independent", decision_answer=decision(choice=choice))
                for choice in ["new_key_retry", "assume_failed"]]
    def apply(command):
        try:
            return repo.mutate(plan["id"], command)
        except TrainingEvidenceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply, commands))
    assert results.count("training_evidence_conflict") == 1
    current = repo.get(plan["id"])
    assert current["revision"] == 2
    saved = current["records"][0]
    assert saved["answer"] == saved["decision_answer"]["choice_id"]
    assert saved["feedback"]["outcome"] == "contradicted"


def test_correction_omitting_decision_clears_it_instead_of_inheriting(setup):
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(decision_answer=decision()))
    state = repo.mutate(plan["id"], change("correct", state, state["records"][0]["id"], answer="changed", assistance="reference"))
    assert state["records"][0]["decision_answer"] is None
    assert state["records"][0]["feedback"]["outcome"] == "abstain"


def test_old_schema_custom_self_referencing_trigger_survives_upgrade(setup):
    from offerpilot.training_evidence.migration import ensure_decision_schema
    factory, _, plan, repo = setup
    state = repo.mutate(plan["id"], record())
    engine = _downgrade_training_schema(factory)
    with engine.begin() as connection:
        connection.exec_driver_sql("""CREATE TRIGGER training_evidence_custom_guard
            BEFORE UPDATE OF answer ON training_evidence_records
            WHEN NEW.answer = 'blocked' AND EXISTS (
                SELECT 1 FROM training_evidence_records WHERE id = OLD.id)
            BEGIN SELECT RAISE(ABORT, 'custom evidence guard'); END""")
        old_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='training_evidence_custom_guard'"
        ).scalar()
    ensure_decision_schema(engine)
    ensure_decision_schema(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='training_evidence_custom_guard'"
        ).scalar() == old_sql
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="custom evidence guard"):
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE training_evidence_records SET answer='blocked'")
    assert repo.get(plan["id"])["records"][0]["answer"] == state["records"][0]["answer"]
    state = repo.mutate(plan["id"], change("correct", state, state["records"][0]["id"], answer="allowed", assistance="independent"))
    assert state["records"][0]["answer"] == "allowed"


# Explicit oracle, independent of the production outcome lookup.
_ALL_DECISION_CASES = [
    ("retry_timeout", "query_same_key", "supported"),
    ("retry_timeout", "new_key_retry", "contradicted"),
    ("retry_timeout", "assume_failed", "contradicted"),
    ("concurrent_retry", "atomic_unique_receipt", "supported"),
    ("concurrent_retry", "check_then_insert", "contradicted"),
    ("concurrent_retry", "client_mutex", "contradicted"),
    ("payload_conflict", "reject_key_conflict", "supported"),
    ("payload_conflict", "replay_other_payload", "contradicted"),
    ("payload_conflict", "overwrite_receipt", "contradicted"),
    ("transaction_gap", "atomic_business_receipt", "supported"),
    ("transaction_gap", "receipt_after_commit", "contradicted"),
    ("transaction_gap", "receipt_before_business", "contradicted"),
    ("expired_receipt", "reconcile_business_outcome", "supported"),
    ("expired_receipt", "same_key_guarantees", "contradicted"),
    ("expired_receipt", "new_key_guarantees", "contradicted"),
    ("stale_revision", "replay_current_state", "supported"),
    ("stale_revision", "reexecute_old_write", "contradicted"),
    ("stale_revision", "return_old_snapshot", "contradicted"),
    *[(scenario, "uncertain", "abstain") for scenario in [
        "retry_timeout", "concurrent_retry", "payload_conflict", "transaction_gap", "expired_receipt", "stale_revision",
    ]],
]


@pytest.mark.parametrize("scenario,choice,outcome", _ALL_DECISION_CASES)
def test_all_six_scenarios_have_scoped_option_feedback(setup, scenario, choice, outcome):
    from offerpilot.training_evidence.scenarios import GUIDANCE
    _, _, plan, repo = setup
    state = repo.mutate(plan["id"], record(scenario_id=scenario, decision_answer=decision(scenario, choice)))
    saved = state["records"][0]
    feedback = saved["feedback"]
    assert feedback["outcome"] == outcome
    assert feedback["record_id"] == saved["id"]
    assert feedback["question_id"] == f"{scenario}.v1"
    assert feedback["choice_id"] == choice
    assert feedback["rubric_version"] == "idempotency_retry.v1"
    assert feedback["text_assessment"] == "not_evaluated"
    assert feedback["scope"] == "selected_option_only"
    if choice == "uncertain":
        assert feedback["rule_id"] is None
        assert "不确定" in feedback["reason"]
        assert feedback["next_step"]
    else:
        assert feedback["rule_id"] == f"{scenario}.v1:{choice}"
        assert feedback["reason"] == GUIDANCE[scenario][choice]["reason"]
        assert feedback["next_step"] == GUIDANCE[scenario][choice]["next_step"]
        assert len({item["next_step"] for item in GUIDANCE[scenario].values()}) == 3
        assert len({item["reason"] for item in GUIDANCE[scenario].values()}) == 3


def test_choice_display_positions_are_mixed_and_correctness_uses_stable_ids():
    from offerpilot.training_evidence.scenarios import RULES, SCENARIOS
    expected_supported = {scenario: choice for scenario, choice, outcome in _ALL_DECISION_CASES if outcome == "supported"}
    positions = []
    for scenario in SCENARIOS[1:]:
        choices = [choice["id"] for choice in scenario["decision_question"]["choices"]]
        correct_id = expected_supported[scenario["id"]]
        assert RULES[scenario["id"]]["supported"] == correct_id
        assert choices[-1] == "uncertain"
        assert len(choices) == len(set(choices)) == 4
        positions.append(choices.index(correct_id))
    assert positions == [1, 2, 0, 2, 1, 0]
