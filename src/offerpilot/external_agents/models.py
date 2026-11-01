"""Additive external-connection grants and durable scheduling requests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base
from offerpilot.external_agents.catalog import ACTIONS


class ExternalAgentConnection(Base):
    __tablename__ = "external_agent_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    application_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    workspace: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_scheduling: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capabilities_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExternalAgentRequest(Base):
    __tablename__ = "external_agent_requests"
    __table_args__ = (
        UniqueConstraint("connection_id", "idempotency_key", name="uq_external_agent_request_key"),
        CheckConstraint(
            "status IN ('pending','committed','rejected','cancelled','expired','conflict')",
            name="ck_external_agent_request_status",
        ),
        CheckConstraint(
            "action IN (" + ",".join(f"'{action}'" for action in ACTIONS) + ")",
            name="ck_external_agent_request_action",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("external_agent_connections.id"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    input_digest: Mapped[str] = mapped_column(String, nullable=False)
    proposal_digest: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    application_id: Mapped[int | None] = mapped_column(nullable=True)
    proposal_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    outcome_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
