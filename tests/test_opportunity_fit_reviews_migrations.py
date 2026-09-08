from __future__ import annotations

import json

from sqlalchemy import inspect, select, text

from offerpilot.db import init_database
from offerpilot.models import (
    InterviewReviewProposal,
    KnowledgeCapturedSourceMetadata,
    KnowledgeSource,
    OpportunityFitReview,
)


def test_fresh_database_creates_v2_review_root_and_stage_tables(tmp_path) -> None:
    factory = init_database(tmp_path / "data.db")

    with factory() as session:
        table_names = set(inspect(session.bind).get_table_names())
        assert "opportunity_fit_review_sessions" in table_names
        assert "opportunity_fit_review_stages" in table_names
        migration = session.execute(
            text("SELECT version FROM schema_migrations WHERE version = '0013_opportunity_fit_v2'")
        ).scalar_one()
        assert migration == "0013_opportunity_fit_v2"
        assert session.execute(
            text("SELECT version FROM schema_migrations WHERE version = '0014_opportunity_fit_v1_schema_marker'")
        ).scalar_one() == "0014_opportunity_fit_v1_schema_marker"


def test_existing_v1_review_bytes_and_hashes_survive_v2_migration(tmp_path) -> None:
    db_path = tmp_path / "data.db"
    factory = init_database(db_path)
    snapshot = json.dumps({"jd": "JD"}, separators=(",", ":"))
    triage = json.dumps({"recommendation": "hold"}, separators=(",", ":"))
    with factory() as session:
        session.execute(
            text(
                """
                INSERT INTO applications (company_name, position_name, source, status)
                VALUES ('Acme', 'Backend', 'manual', 'applied')
                """
            )
        )
        application_id = session.execute(text("SELECT last_insert_rowid()")).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO opportunity_fit_reviews (
                    application_id, resume_id, idempotency_key,
                    source_fingerprint_sha256, source_snapshot_json,
                    triage_json, triage_sha256
                ) VALUES (:application_id, NULL, 'legacy-key', 'source-hash', :snapshot,
                          :triage, 'triage-hash')
                """
            ),
            {"application_id": application_id, "snapshot": snapshot, "triage": triage},
        )
        session.commit()

    init_database(db_path)

    with factory() as session:
        row = session.scalar(select(OpportunityFitReview))
        assert row is not None
        assert row.source_snapshot_json == snapshot
        assert row.triage_json == triage
        assert row.source_fingerprint_sha256 == "source-hash"
        assert row.triage_sha256 == "triage-hash"
        assert row.proposal_schema_version == 1
        assert session.execute(
            text("SELECT COUNT(*) FROM opportunity_fit_review_stages")
        ).scalar_one() == 0


def test_legacy_v1_ddl_is_upgraded_without_rewriting_bytes_or_hashes(tmp_path) -> None:
    db_path = tmp_path / "data.db"
    factory = init_database(db_path)
    snapshot = '{"legacy":"snapshot"}'
    triage = '{"recommendation":"hold"}'
    with factory() as session:
        app_id = session.execute(
            text(
                "INSERT INTO applications (company_name, position_name, source, status) "
                "VALUES ('Legacy', 'Engineer', 'manual', 'applied') RETURNING id"
            )
        ).scalar_one()
        event_id = session.execute(
            text(
                "INSERT INTO application_events (application_id, event_type) "
                "VALUES (:app_id, 'interview') RETURNING id"
            ),
            {"app_id": app_id},
        ).scalar_one()
        note_id = session.execute(
            text(
                "INSERT INTO interview_notes (application_id, application_event_id, company, position) "
                "VALUES (:app_id, :event_id, 'Legacy', 'Engineer') RETURNING id"
            ),
            {"app_id": app_id, "event_id": event_id},
        ).scalar_one()
        session.execute(text("DROP TABLE opportunity_fit_reviews"))
        session.execute(text("DROP TABLE interview_review_proposals"))
        session.execute(
            text(
                """
                CREATE TABLE opportunity_fit_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    resume_id INTEGER,
                    idempotency_key VARCHAR NOT NULL,
                    source_fingerprint_sha256 VARCHAR NOT NULL,
                    source_snapshot_json VARCHAR NOT NULL,
                    triage_json VARCHAR NOT NULL,
                    triage_sha256 VARCHAR NOT NULL,
                    deep_review_json VARCHAR,
                    deep_review_sha256 VARCHAR,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deep_reviewed_at DATETIME
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE interview_review_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id INTEGER NOT NULL REFERENCES interview_notes(id) ON DELETE CASCADE,
                    application_event_id INTEGER REFERENCES application_events(id) ON DELETE SET NULL,
                    idempotency_key VARCHAR NOT NULL,
                    input_snapshot_json VARCHAR NOT NULL,
                    source_fingerprint VARCHAR NOT NULL,
                    proposal_json VARCHAR NOT NULL,
                    proposal_hash VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_interview_review_proposals_note_key UNIQUE(note_id, idempotency_key)
                )
                """
            )
        )
        session.execute(
            text(
                "CREATE INDEX idx_interview_review_proposals_note "
                "ON interview_review_proposals(note_id)"
            )
        )
        session.execute(
            text(
                "INSERT INTO opportunity_fit_reviews "
                "(application_id, idempotency_key, source_fingerprint_sha256, "
                "source_snapshot_json, triage_json, triage_sha256) "
                "VALUES (:app_id, 'raw-old-key', 'raw-source', :snapshot, :triage, 'raw-triage')"
            ),
            {"app_id": app_id, "snapshot": snapshot, "triage": triage},
        )
        session.execute(
            text(
                "INSERT INTO interview_review_proposals "
                "(note_id, application_event_id, idempotency_key, input_snapshot_json, "
                "source_fingerprint, proposal_json, proposal_hash) "
                "VALUES (:note_id, :event_id, 'legacy-review-key', '{}', "
                "'legacy-review-source', '{}', 'legacy-review-hash')"
            ),
            {"note_id": note_id, "event_id": event_id},
        )
        session.commit()

    upgraded = init_database(db_path)

    with upgraded() as session:
        row = session.execute(
            text(
                "SELECT source_snapshot_json, triage_json, source_fingerprint_sha256, "
                "triage_sha256, proposal_schema_version FROM opportunity_fit_reviews"
            )
        ).one()
        assert tuple(row) == (snapshot, triage, "raw-source", "raw-triage", 1)
        review_row = session.execute(
            text(
                "SELECT note_id, source_fingerprint, proposal_hash "
                "FROM interview_review_proposals"
            )
        ).one()
        assert tuple(review_row) == (note_id, "legacy-review-source", "legacy-review-hash")


def test_existing_captured_knowledge_backfills_event_from_review_history(tmp_path) -> None:
    db_path = tmp_path / "data.db"
    factory = init_database(db_path)
    with factory() as session:
        application_id = session.execute(
            text(
                "INSERT INTO applications (company_name, position_name, source, status) "
                "VALUES ('Legacy', 'Engineer', 'manual', 'applied') RETURNING id"
            )
        ).scalar_one()
        event_id = session.execute(
            text(
                "INSERT INTO application_events (application_id, event_type) "
                "VALUES (:application_id, 'interview') RETURNING id"
            ),
            {"application_id": application_id},
        ).scalar_one()
        note_id = session.execute(
            text(
                "INSERT INTO interview_notes (application_id, application_event_id, company, position) "
                "VALUES (:application_id, :event_id, 'Legacy', 'Engineer') RETURNING id"
            ),
            {"application_id": application_id, "event_id": event_id},
        ).scalar_one()
        source = KnowledgeSource(
            source_hash="legacy-captured-source",
            source_kind="captured_interview_note",
            title_hint="Legacy capture",
            main_filename="interview-note.txt",
            main_media_type="text/plain",
            main_relative_path="captured://interview-note/legacy",
            total_bytes=1,
        )
        session.add(source)
        session.flush()
        session.add(
            KnowledgeCapturedSourceMetadata(
                source_id=source.id,
                origin_note_id=note_id,
                application_event_id=None,
                note_fingerprint="legacy-note-fingerprint",
                selected_fragments_json="[]",
                capture_schema_version="interview-note-capture-v1",
            )
        )
        session.add(
            InterviewReviewProposal(
                note_id=note_id,
                application_event_id=event_id,
                idempotency_key="legacy-review-for-capture",
                input_snapshot_json="{}",
                source_fingerprint="legacy-source-fingerprint",
                proposal_json="{}",
                proposal_hash="legacy-proposal-hash",
            )
        )
        session.commit()

    init_database(db_path)

    with factory() as session:
        metadata = session.get(KnowledgeCapturedSourceMetadata, source.id)
        assert metadata is not None
        assert metadata.application_event_id == event_id


def test_existing_captured_knowledge_stays_unattributed_for_multiple_review_events(tmp_path) -> None:
    db_path = tmp_path / "data.db"
    factory = init_database(db_path)
    with factory() as session:
        application_id = session.execute(
            text(
                "INSERT INTO applications (company_name, position_name, source, status) "
                "VALUES ('Legacy', 'Engineer', 'manual', 'applied') RETURNING id"
            )
        ).scalar_one()
        event_ids = [
            session.execute(
                text(
                    "INSERT INTO application_events (application_id, event_type) "
                    "VALUES (:application_id, 'interview') RETURNING id"
                ),
                {"application_id": application_id},
            ).scalar_one()
            for _ in range(2)
        ]
        note_id = session.execute(
            text(
                "INSERT INTO interview_notes (application_id, application_event_id, company, position) "
                "VALUES (:application_id, :event_id, 'Legacy', 'Engineer') RETURNING id"
            ),
            {"application_id": application_id, "event_id": event_ids[0]},
        ).scalar_one()
        source = KnowledgeSource(
            source_hash="ambiguous-captured-source",
            source_kind="captured_interview_note",
            title_hint="Ambiguous capture",
            main_filename="interview-note.txt",
            main_media_type="text/plain",
            main_relative_path="captured://interview-note/ambiguous",
            total_bytes=1,
        )
        session.add(source)
        session.flush()
        session.add(
            KnowledgeCapturedSourceMetadata(
                source_id=source.id,
                origin_note_id=note_id,
                application_event_id=None,
                note_fingerprint="ambiguous-note-fingerprint",
                selected_fragments_json="[]",
                capture_schema_version="interview-note-capture-v1",
            )
        )
        for event_id in event_ids:
            session.add(
                InterviewReviewProposal(
                    note_id=note_id,
                    application_event_id=event_id,
                    idempotency_key=f"ambiguous-review-{event_id}",
                    input_snapshot_json="{}",
                    source_fingerprint=f"ambiguous-source-{event_id}",
                    proposal_json="{}",
                    proposal_hash=f"ambiguous-proposal-{event_id}",
                )
            )
        session.commit()

    init_database(db_path)

    with factory() as session:
        metadata = session.get(KnowledgeCapturedSourceMetadata, source.id)
        assert metadata is not None
        assert metadata.application_event_id is None
