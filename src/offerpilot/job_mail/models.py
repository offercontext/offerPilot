"""Mail source/proposal storage; credentials and raw MIME are deliberately absent."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class JobMailConnection(Base):
    __tablename__ = "job_mail_connections"
    __table_args__ = (
        UniqueConstraint("scope_id"),
        CheckConstraint("interval_minutes >= 5 AND interval_minutes <= 1440"),
        CheckConstraint("scope_version >= 1"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, default="", server_default="")
    provider: Mapped[str] = mapped_column(String, default="qq", server_default="qq")
    status: Mapped[str] = mapped_column(String, default="disconnected", server_default="disconnected")
    # Opaque OS-vault reference only; never store a password or authorization code.
    credential_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    folders_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    scope_version: Mapped[int] = mapped_column(default=1, server_default="1")
    sync_mode: Mapped[str] = mapped_column(String, default="manual", server_default="manual")
    interval_minutes: Mapped[int] = mapped_column(default=15, server_default="15")
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    not_before_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_count: Mapped[int] = mapped_column(default=0, server_default="0")
    cursor_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    budget_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class JobMailSyncRun(Base):
    __tablename__ = "job_mail_sync_runs"
    __table_args__ = (Index("idx_job_mail_sync_connection", "connection_id", "started_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("job_mail_connections.id"))
    scope_version: Mapped[int] = mapped_column(nullable=False)
    trigger: Mapped[str] = mapped_column(String, default="manual")
    status: Mapped[str] = mapped_column(String, default="running")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner_token: Mapped[str] = mapped_column(String, default="", server_default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    progress_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    error_code: Mapped[str] = mapped_column(String, default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobMailEvidence(Base):
    __tablename__ = "job_mail_evidence"
    __table_args__ = (UniqueConstraint("scope_id", "source_identity", "body_fingerprint"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("job_mail_connections.id"), nullable=True)
    source_identity: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, default="", server_default="")
    subject: Mapped[str] = mapped_column(String, default="", server_default="")
    sender: Mapped[str] = mapped_column(String, default="", server_default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    locations_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class JobMailSuggestion(Base):
    __tablename__ = "job_mail_suggestions"
    __table_args__ = (
        UniqueConstraint("evidence_id", "candidate_key"),
        Index("idx_job_mail_suggestions_scope_status", "scope_id", "status"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("job_mail_evidence.id"))
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("job_mail_connections.id"), nullable=True)
    scope_version: Mapped[int] = mapped_column(default=1, server_default="1")
    candidate_key: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    action: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    time_mode: Mapped[str] = mapped_column(String, default="unknown", server_default="unknown")
    proposed_fields_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    field_evidence_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    application_candidates_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    target_event_id: Mapped[int | None] = mapped_column(nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class JobMailPreview(Base):
    __tablename__ = "job_mail_previews"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    suggestion_id: Mapped[str] = mapped_column(ForeignKey("job_mail_suggestions.id"))
    suggestion_version: Mapped[int] = mapped_column(nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    scope_version: Mapped[int] = mapped_column(nullable=False)
    application_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    target_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class JobMailReceipt(Base):
    __tablename__ = "job_mail_receipts"
    __table_args__ = (UniqueConstraint("scope_id", "operation_id"), UniqueConstraint("suggestion_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    operation_id: Mapped[str] = mapped_column(String, nullable=False)
    suggestion_id: Mapped[str] = mapped_column(String, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    application_event_id: Mapped[int] = mapped_column(nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class JobMailExtractionAttempt(Base):
    """Durable extraction admission: unknown calls are never automatically replayed."""
    __tablename__ = "job_mail_extraction_attempts"
    __table_args__ = (UniqueConstraint("scope_id", "source_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    source_key: Mapped[str] = mapped_column(String, nullable=False)
    connection_id: Mapped[str] = mapped_column(ForeignKey("job_mail_connections.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("job_mail_sync_runs.id"))
    state: Mapped[str] = mapped_column(String, default="running")
    payload_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
