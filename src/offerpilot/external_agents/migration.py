"""Transactional V1 -> business-grant migration, preserving all request receipts."""

from sqlalchemy import MetaData, Table, text
from typing import cast
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable

from offerpilot.external_agents.models import ExternalAgentConnection, ExternalAgentRequest


def ensure_business_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(external_agent_connections)"
                )
            }
            if "capabilities_json" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE external_agent_connections ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '[]'"
                )
            sql = str(
                connection.scalar(
                    text(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='external_agent_requests'"
                    )
                )
            )
            if "create_application" not in sql:
                metadata = MetaData()
                cast(Table, ExternalAgentConnection.__table__).to_metadata(metadata)
                stage = cast(Table, ExternalAgentRequest.__table__).to_metadata(
                    metadata, name="external_agent_requests_upgrade"
                )
                before = list(
                    connection.exec_driver_sql("SELECT * FROM external_agent_requests ORDER BY id")
                )
                connection.execute(CreateTable(stage))
                fields = ",".join(
                    f'"{column.name}"' for column in ExternalAgentRequest.__table__.columns
                )
                connection.exec_driver_sql(
                    f"INSERT INTO external_agent_requests_upgrade ({fields}) SELECT {fields} FROM external_agent_requests"
                )
                after = list(
                    connection.exec_driver_sql(
                        "SELECT * FROM external_agent_requests_upgrade ORDER BY id"
                    )
                )
                if before != after:
                    raise RuntimeError("external_agent_migration_receipt_mismatch")
                connection.exec_driver_sql("DROP TABLE external_agent_requests")
                connection.exec_driver_sql(
                    "ALTER TABLE external_agent_requests_upgrade RENAME TO external_agent_requests"
                )
                for index in cast(Table, ExternalAgentRequest.__table__).indexes:
                    index.create(connection, checkfirst=True)
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (:version, :description)"
                ),
                {
                    "version": "0036_external_agent_business",
                    "description": "Opt-in resource grants and preserved generic business request receipts",
                },
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
