"""Explicit plan-local practice evidence. No AI, mastery inference or cross-job recall.

Reads and writes use the canonical readiness projection; source changes disable
consumption but never prevent a user withdrawing their own visible evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import AdaptivePracticePlan, Application
from offerpilot.repositories.json_contract import canonical_json, sha256_text
from offerpilot.review_readiness.projection import project_practice_focus
from offerpilot.training_evidence.contracts import (
    Command, CorrectCommand, RecordCommand, WithdrawCommand,
)
from offerpilot.training_evidence.models import (
    TrainingEvidenceCommand, TrainingEvidenceRecord, TrainingEvidenceState,
)

SCENARIOS = (
    {"id": "original", "prompt": "回看这次已保存的练习回答，并如实标注作答时的帮助情况。"},
    {"id": "retry_timeout", "prompt": "投递已成功写入，但客户端等待响应超时。用户点击重试：如何判断是否已成功，并避免重复写入？请说明依据和恢复步骤。"},
    {"id": "concurrent_retry", "prompt": "同一份投递请求被两个客户端同时重试。如何保证只写入一次，并让两个请求都获得可核对的结果？请说明唯一约束和回执处理。"},
)
ASSISTANCE_LABELS = {
    "unknown": "帮助情况未知", "independent": "独立作答",
    "hinted": "提示后作答", "reference": "看过参考答案后作答",
}


class TrainingEvidenceError(ValueError):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _visible_plan(session: Session, plan_id: int) -> AdaptivePracticePlan:
    plan = session.get(AdaptivePracticePlan, plan_id)
    app = session.get(Application, plan.application_id) if plan is not None else None
    if plan is None or app is None or app.deleted_at is not None:
        raise TrainingEvidenceError("training_evidence_not_found", 404)
    return plan


def _availability(session: Session, plan: AdaptivePracticePlan) -> str:
    if plan.origin_contract != "confirmed_readiness_signal_v1" or plan.status != "completed":
        return "not_eligible"
    projection = project_practice_focus(
        session, signal_version_id=plan.readiness_signal_version_id or 0,
        target_event_id=plan.target_application_event_id or 0,
    )
    if projection.state != "completed":
        return projection.state
    if (
        projection.source is None or projection.target is None
        or projection.source.application_id != plan.application_id
        or projection.target.application_id != plan.application_id
        or projection.source.practice_source_fingerprint != plan.source_fingerprint
        or projection.target.practice_target_fingerprint != plan.target_fingerprint
    ):
        return "source_changed"
    return "current"


def _record_json(record: TrainingEvidenceRecord) -> dict[str, Any]:
    return {
        "id": record.id, "scenario_id": record.scenario_id,
        "answer": record.answer, "assistance": record.assistance,
        "created_at": record.created_at, "updated_at": record.updated_at,
        "withdrawn": record.withdrawn,
    }


def _recommendation(records: list[TrainingEvidenceRecord]) -> dict[str, Any] | None:
    active = [record for record in records if not record.withdrawn]
    if not active:
        return None
    latest = active[-1]
    scenario_id = "concurrent_retry" if latest.scenario_id == "retry_timeout" else "retry_timeout"
    prompt = next(item["prompt"] for item in SCENARIOS if item["id"] == scenario_id)
    label = ASSISTANCE_LABELS[latest.assistance]
    reason = f"你将记录 #{latest.id} 标注为“{label}”。建议换一个情境独立尝试，再核对推理依据；此标注不证明已经掌握。"
    if latest.assistance == "unknown":
        reason = f"记录 #{latest.id} 的帮助情况未知，不能由回答质量倒推。建议换情境作答并如实标注帮助情况。"
    comparison = "目前只有一条可用记录，尚不能比较变化。"
    ids = [latest.id]
    if len(active) > 1:
        previous = active[-2]
        ids.insert(0, previous.id)
        comparison = (
            f"记录 #{previous.id} 标注为“{ASSISTANCE_LABELS[previous.assistance]}”，"
            f"记录 #{latest.id} 标注为“{label}”。这是用户标注的作答条件变化，不是能力评分。"
        )
    return {
        "scenario_id": scenario_id, "prompt": prompt, "reason": reason,
        "evidence_record_ids": ids, "comparison": comparison,
    }


def _state(session: Session, plan: AdaptivePracticePlan) -> dict[str, Any]:
    state = session.get(TrainingEvidenceState, plan.id)
    records = list(session.scalars(select(TrainingEvidenceRecord).where(
        TrainingEvidenceRecord.plan_id == plan.id,
    ).order_by(TrainingEvidenceRecord.id)))
    reason = _availability(session, plan)
    available = reason == "current"
    return {
        "revision": state.revision if state else 0,
        "skill_id": "idempotency_retry", "available": available, "reason": reason,
        "records": [_record_json(record) for record in records],
        "scenarios": list(SCENARIOS),
        "recommendation": _recommendation(records) if available else None,
    }


class TrainingEvidenceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, plan_id: int) -> dict[str, Any]:
        try:
            with self._session_factory() as session:
                session.execute(text("BEGIN"))
                return _state(session, _visible_plan(session, plan_id))
        except SQLAlchemyError as exc:
            raise TrainingEvidenceError("training_evidence_unavailable", 503) from exc

    def mutate(self, plan_id: int, command: Command) -> dict[str, Any]:
        fingerprint = sha256_text(canonical_json({"plan_id": plan_id, **command.model_dump()}))
        try:
            with self._session_factory() as session:
                # Revision check, source check, mutation and receipt are one SQLite
                # transaction. A competing correction cannot overwrite a later one.
                session.execute(text("BEGIN IMMEDIATE"))
                plan = _visible_plan(session, plan_id)
                receipt = session.get(TrainingEvidenceCommand, command.idempotency_key)
                if receipt is not None:
                    if receipt.plan_id != plan_id or receipt.fingerprint != fingerprint:
                        raise TrainingEvidenceError("training_evidence_idempotency_conflict", 409)
                    return _state(session, plan)
                if plan.origin_contract != "confirmed_readiness_signal_v1" or plan.status != "completed":
                    raise TrainingEvidenceError("training_evidence_source_unavailable", 409)
                if not isinstance(command, WithdrawCommand) and _availability(session, plan) != "current":
                    raise TrainingEvidenceError("training_evidence_source_unavailable", 409)
                state = session.get(TrainingEvidenceState, plan_id)
                revision = state.revision if state else 0
                if revision != command.expected_revision:
                    raise TrainingEvidenceError("training_evidence_conflict", 409)
                if state is None:
                    state = TrainingEvidenceState(plan_id=plan_id, revision=0)
                    session.add(state)
                    session.flush()
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if isinstance(command, RecordCommand):
                    records = list(session.scalars(select(TrainingEvidenceRecord).where(
                        TrainingEvidenceRecord.plan_id == plan_id,
                    )))
                    if len(records) >= 12:
                        raise TrainingEvidenceError("training_evidence_conflict", 409)
                    if command.scenario_id == "original":
                        if command.answer != plan.response_text:
                            raise TrainingEvidenceError("training_evidence_invalid", 422)
                        if any(record.scenario_id == "original" and not record.withdrawn for record in records):
                            raise TrainingEvidenceError("training_evidence_conflict", 409)
                    record = TrainingEvidenceRecord(
                        plan_id=plan_id, scenario_id=command.scenario_id,
                        answer=command.answer, assistance=command.assistance,
                        withdrawn=False, created_at=now, updated_at=now,
                    )
                    session.add(record)
                else:
                    existing_record = session.get(TrainingEvidenceRecord, command.record_id)
                    if existing_record is None or existing_record.plan_id != plan_id:
                        raise TrainingEvidenceError("training_evidence_not_found", 404)
                    record = existing_record
                    if record.withdrawn:
                        raise TrainingEvidenceError("training_evidence_conflict", 409)
                    if isinstance(command, CorrectCommand):
                        if record.scenario_id == "original" and command.answer != plan.response_text:
                            raise TrainingEvidenceError("training_evidence_invalid", 422)
                        record.answer = command.answer
                        record.assistance = command.assistance
                    else:
                        # Physical text removal from the live row, not a cosmetic
                        # UI filter. Original plan data remains in its own feature.
                        record.answer = ""
                        record.assistance = "unknown"
                        record.withdrawn = True
                    record.updated_at = now
                state.revision += 1
                session.add(TrainingEvidenceCommand(
                    idempotency_key=command.idempotency_key, plan_id=plan_id,
                    fingerprint=fingerprint, revision=state.revision,
                ))
                session.flush()
                result = _state(session, plan)
                session.commit()
                return result
        except SQLAlchemyError as exc:
            raise TrainingEvidenceError("training_evidence_unavailable", 503) from exc
