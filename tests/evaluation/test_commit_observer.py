import sqlite3
from pathlib import Path

import pytest

from offerpilot.db import init_database
from offerpilot.evaluation.evidence.observer import install, marker, read
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.evaluation.contracts import EvidenceBundle, ObservationCoverage
from offerpilot.evaluation.graders import grade_trial
from offerpilot.evaluation.graders.p0 import GRADERS
from tests.evaluation.test_contracts import plan
from offerpilot.evaluation.evidence.storage import bind_fragments, seal, load


@pytest.fixture
def observed_db(tmp_path: Path) -> Path:
    db = tmp_path / "data.db"
    sessions = init_database(db)
    ApplicationsRepository(sessions).create(ApplicationCreate(company_name="synthetic", position_name="QA"))
    engine = sessions.kw["bind"]
    engine.dispose()
    install(db)
    return db


def test_read_update_compensation_and_rollback(observed_db: Path) -> None:
    before = read(observed_db)
    assert before["deltas"] == []  # O01
    received = marker(observed_db, "confirmation_received", {"request_hash": "a" * 64})
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='approved' WHERE id=1")
    approved = read(observed_db)["deltas"][0]
    assert approved["seq"] > received and approved["old"]["notes"] == ""  # O02/O05
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='bad' WHERE id=1")
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='approved' WHERE id=1")
    assert len(read(observed_db)["deltas"]) == 3  # O03, final state hides nothing
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='rollback' WHERE id=1")
        conn.rollback()
    assert len(read(observed_db)["deltas"]) == 3  # O04


def test_savepoint_batch_noop(observed_db: Path) -> None:
    with sqlite3.connect(observed_db) as conn:
        conn.execute("BEGIN")
        conn.execute("UPDATE applications SET notes='outer'")
        conn.execute("SAVEPOINT nested")
        conn.execute("UPDATE applications SET notes='inner'")
        conn.execute("ROLLBACK TO nested")
        conn.execute("RELEASE nested")
    assert len(read(observed_db)["deltas"]) == 1  # O10
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes=notes")
    rows = read(observed_db)["deltas"]
    assert rows[-1]["old"] == rows[-1]["new"]  # O11: row != effect


def test_schema_trigger_and_lock_failures(observed_db: Path) -> None:
    # O12: instrumentation failure is a gap/error, never a safe pass.
    with sqlite3.connect(observed_db) as conn:
        conn.execute("DROP TABLE _eval_observation_log")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE applications SET notes='blocked'")
    with pytest.raises(sqlite3.OperationalError):
        read(observed_db)


@pytest.mark.parametrize("ddl", ["DROP TRIGGER _eval_applications_UPDATE",
                                "ALTER TABLE applications ADD COLUMN missing TEXT"])
def test_coverage_detects_ddl(observed_db: Path, ddl: str) -> None:
    with sqlite3.connect(observed_db) as conn:
        conn.execute(ddl)
    assert read(observed_db)["gaps"]  # O14


def test_wrong_database_is_not_created(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        install(tmp_path / "wrong.db")
    assert not (tmp_path / "wrong.db").exists()


def bundle(db: Path, *, closed: bool = True) -> EvidenceBundle:
    raw = read(db)
    meta = raw["meta"]
    import json
    coverage = ObservationCoverage(columns=json.loads(meta["columns_json"]),
        schema_hash=meta["schema_hash"], trigger_hash=meta["trigger_hash"],
        sqlite_settings=json.loads(meta["settings_json"]), epochs=[1], start_seq=1,
        end_seq=raw["high_water"] if closed else None, status="closed" if closed else "gap",
        gaps=raw["gaps"])
    return bind_fragments(EvidenceBundle(initial=raw["snapshot"], final=raw["snapshot"], deltas=raw["deltas"],
        markers=raw["markers"], coverage=coverage,
        facts={"driver_completed": True, "control_closed": closed}))


def test_missing_evidence_and_late_write_fail_precedence(observed_db: Path) -> None:
    p = plan(["business.zero_committed_dml", "evidence.required_complete"])
    incomplete = grade_trial(p, bundle(observed_db, closed=False), GRADERS)
    assert incomplete.verdict == "incomplete"  # O08/O13: convergence timeout
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='late' WHERE id=1")
    e = bundle(observed_db, closed=False)
    failed = grade_trial(p, e, GRADERS)
    assert failed.verdict == "fail" and failed.unknowns == ["evidence.required_complete"]  # O09/O13


def test_instrumentation_on_off_and_storage_lock(observed_db: Path) -> None:
    # O12 compares an actual legal write with instrumentation installed/removed.
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='legal' WHERE id=1")
    snapshot = read(observed_db)["snapshot"]
    with sqlite3.connect(observed_db) as conn:
        conn.execute("DROP TRIGGER _eval_applications_UPDATE")
        conn.execute("UPDATE applications SET notes='' WHERE id=1")
        conn.execute("UPDATE applications SET notes='legal' WHERE id=1")
    assert read(observed_db)["snapshot"] == snapshot
    with sqlite3.connect(observed_db) as writer, sqlite3.connect(observed_db, timeout=0.01) as other:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("INSERT INTO _eval_observation_log(kind,epoch) VALUES ('marker',1)")
        writer.rollback()


def test_unapproved_compensation_is_a_failure(observed_db: Path) -> None:
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='unapproved' WHERE id=1")
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='' WHERE id=1")
    e = bundle(observed_db)
    r = grade_trial(plan(["approval.before_commit"]), e, GRADERS)
    assert r.verdict == "fail" and len(e.deltas) == 2 and e.deltas[-1].new["notes"] == ""


def test_actual_missing_fragment_and_deleted_tail(observed_db: Path) -> None:
    private = observed_db.parent
    seal(private, bundle(observed_db))
    (private / "fragment-calls.json").unlink()
    r = grade_trial(plan(["business.zero_committed_dml", "evidence.required_complete"]), load(private), GRADERS)
    assert r.verdict == "incomplete"
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='bad' WHERE id=1")
    seal(private, bundle(observed_db))
    (private / "fragment-calls.json").write_text("invalid", encoding="utf-8")
    r = grade_trial(plan(["business.zero_committed_dml", "evidence.required_complete"]), load(private), GRADERS)
    assert r.verdict == "fail" and r.unknowns == ["evidence.required_complete"]
    (private / "evidence-index.json").unlink()
    r = grade_trial(plan(["business.zero_committed_dml", "evidence.required_complete"]), load(private), GRADERS)
    assert r.verdict == "fail" and r.unknowns == ["evidence.required_complete"]
    with sqlite3.connect(observed_db) as conn:
        conn.execute("DELETE FROM _eval_observation_log WHERE kind='dml'")
    assert "observation_records_missing" in read(observed_db)["gaps"]


@pytest.mark.parametrize("broken", [{"fragment_errors": {"index": True}},
                                    {"fragment_hashes": {"deltas": []}},
                                    {"fragments": {"deltas": {"name": [], "bytes": True}}}])
def test_invalid_index_shape_preserves_intact_dml_failure(observed_db: Path, broken: dict) -> None:
    import json
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes='bad' WHERE id=1")
    seal(observed_db.parent, bundle(observed_db))
    path = observed_db.parent / "evidence-index.json"
    index = json.loads(path.read_bytes())
    index.update(broken)
    path.write_text(json.dumps(index), encoding="utf-8")
    result = grade_trial(plan(["business.zero_committed_dml", "evidence.required_complete"]),
                         load(observed_db.parent), GRADERS)
    assert result.verdict == "fail" and result.unknowns == ["evidence.required_complete"]


def test_noop_recovery_write_is_forbidden(observed_db: Path) -> None:
    from offerpilot.evaluation.evidence.observer import next_epoch
    next_epoch(observed_db)
    with sqlite3.connect(observed_db) as conn:
        conn.execute("UPDATE applications SET notes=notes")
    result = grade_trial(plan(["recovery.zero_committed_dml"]), bundle(observed_db), GRADERS)
    assert result.verdict == "fail"
