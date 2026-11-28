from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from offerpilot.models import Base


class ApplicationStrategyChange(Base):
    __tablename__ = "application_strategy_changes"
    __table_args__ = (
        CheckConstraint(
            "(revision = 0 AND target_snapshot_id IS NULL AND applied_detail IS NULL "
            "AND target_outcome_id IS NULL AND decision IS NULL) OR "
            "(revision = 1 AND target_snapshot_id IS NOT NULL AND applied_detail IS NOT NULL "
            "AND target_outcome_id IS NULL AND decision IS NULL) OR "
            "(revision = 2 AND target_snapshot_id IS NOT NULL AND applied_detail IS NOT NULL "
            "AND target_outcome_id IS NOT NULL AND decision IN ('keep', 'revise', 'drop') "
            "AND decision IS NOT NULL)",
            name="ck_application_strategy_phase",
        ),
    )

    origin_outcome_id: Mapped[int] = mapped_column(
        ForeignKey("application_outcomes.id", ondelete="CASCADE"), primary_key=True
    )
    proposal: Mapped[str] = mapped_column(Text, nullable=False)
    target_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_submission_snapshots.id", ondelete="CASCADE"), index=True
    )
    applied_detail: Mapped[str | None] = mapped_column(Text)
    target_outcome_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_outcomes.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str | None] = mapped_column(String)
    revision: Mapped[int] = mapped_column(nullable=False)


class ApplicationStrategyReceipt(Base):
    """Keep replay metadata only; never duplicate user text or a view in a receipt."""

    __tablename__ = "application_strategy_receipts"

    origin_outcome_id: Mapped[int] = mapped_column(
        ForeignKey("application_strategy_changes.origin_outcome_id", ondelete="CASCADE"),
        primary_key=True,
    )
    request_key: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
