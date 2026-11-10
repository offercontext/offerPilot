"""Atomic, data-preserving expansion of the opt-in evidence schema."""
from sqlalchemy import Engine

VERSION = "0040_training_evidence_decisions"


def _checkpoint() -> None:
    """Failure-injection seam before replacing the original table."""


def ensure_decision_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        if connection.exec_driver_sql(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,),
        ).scalar():
            return
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        if connection.exec_driver_sql(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,),
        ).scalar():
            return
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(training_evidence_records)")}
        if "decision_json" not in columns:
            # No table references records: states and receipts remain untouched.
            # Preserve every existing index/trigger, not just the ORM-known index.
            objects = list(connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE tbl_name = 'training_evidence_records' "
                "AND type IN ('index','trigger') AND sql IS NOT NULL"
            ))
            connection.exec_driver_sql("""
                CREATE TABLE training_evidence_records_v40 (
                    id INTEGER NOT NULL PRIMARY KEY,
                    plan_id INTEGER NOT NULL REFERENCES training_evidence_states(plan_id) ON DELETE CASCADE,
                    scenario_id VARCHAR NOT NULL CHECK (scenario_id IN
                        ('original','retry_timeout','concurrent_retry','payload_conflict',
                         'transaction_gap','expired_receipt','stale_revision')),
                    answer TEXT NOT NULL,
                    assistance VARCHAR NOT NULL CHECK (assistance IN ('unknown','independent','hinted','reference')),
                    withdrawn BOOLEAN NOT NULL,
                    created_at VARCHAR NOT NULL,
                    updated_at VARCHAR NOT NULL,
                    decision_json TEXT
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO training_evidence_records_v40
                    (id,plan_id,scenario_id,answer,assistance,withdrawn,created_at,updated_at)
                SELECT id,plan_id,scenario_id,answer,assistance,withdrawn,created_at,updated_at
                FROM training_evidence_records
            """)
            _checkpoint()
            connection.exec_driver_sql("DROP TABLE training_evidence_records")
            connection.exec_driver_sql("ALTER TABLE training_evidence_records_v40 RENAME TO training_evidence_records")
            _checkpoint()
            for (sql,) in objects:
                connection.exec_driver_sql(sql)
        if connection.exec_driver_sql("PRAGMA foreign_key_check(training_evidence_records)").first():
            raise RuntimeError("training evidence migration foreign key failure")
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations(version,description) VALUES (?,?)",
            (VERSION, "Expand scenario pool and add optional explicit decision evidence without inferring legacy answers"),
        )
