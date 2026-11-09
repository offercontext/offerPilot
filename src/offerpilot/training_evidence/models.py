"""Plan-local, explicitly saved training evidence; not a global memory store."""
from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class TrainingEvidenceState(Base):
    __tablename__ = "training_evidence_states"
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("adaptive_practice_plans.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TrainingEvidenceRecord(Base):
    __tablename__ = "training_evidence_records"
    __table_args__ = (
        CheckConstraint("scenario_id IN ('original','retry_timeout','concurrent_retry')"),
        CheckConstraint("assistance IN ('unknown','independent','hinted','reference')"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("training_evidence_states.plan_id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(String, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    assistance: Mapped[str] = mapped_column(String, nullable=False)
    withdrawn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class TrainingEvidenceCommand(Base):
    """No answers or response snapshots: retries cannot resurrect withdrawn text."""
    __tablename__ = "training_evidence_commands"
    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("training_evidence_states.plan_id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
