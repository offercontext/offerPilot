"""Explicit plan-local practice evidence. No AI, mastery inference or cross-job recall.

Reads and writes use the canonical readiness projection; source changes disable
consumption but never prevent a user withdrawing their own visible evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import AdaptivePracticePlan, Application
from offerpilot.repositories.json_contract import canonical_json, sha256_text
from offerpilot.review_readiness.projection import project_practice_focus
from offerpilot.training_evidence.contracts import (
    Command, CorrectCommand, DecisionAnswer, RecordCommand, WithdrawCommand,
)
from offerpilot.training_evidence.models import (
    TrainingEvidenceCommand, TrainingEvidenceRecord, TrainingEvidenceState,
)

from offerpilot.training_evidence.scenarios import GUIDANCE, POOL_IDS, POOL_VERSION, RULES, SCENARIOS

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


def _validate_decision(scenario_id: str, decision: DecisionAnswer | None) -> str | None:
    if decision is None:
        return None
    rule = RULES.get(scenario_id)
    if (rule is None or decision.question_id != f"{scenario_id}.v1"
            or decision.choice_id not in {*rule["choices"], "uncertain"}):
        raise TrainingEvidenceError("training_evidence_invalid", 422)
    return canonical_json(decision.model_dump())


def _feedback(record: TrainingEvidenceRecord, available: bool) -> dict[str, Any]:
    decision = json.loads(record.decision_json) if record.decision_json else None
    result: dict[str, Any] = {
        "scope": "selected_option_only", "text_assessment": "not_evaluated",
        "outcome": "abstain", "record_id": record.id,
        "rubric_version": decision["rubric_version"] if decision else None,
        "question_id": decision["question_id"] if decision else None,
        "choice_id": decision["choice_id"] if decision else None,
        "rule_id": None, "reason": "未提供可评估的方案选择；自由文本未评估。", "next_step": None,
    }
    if not available or record.withdrawn:
        result["reason"] = "来源不可用或记录已撤回，暂停反馈。"
        return result
    if decision is None:
        return result
    if decision["choice_id"] == "uncertain":
        result["reason"] = "你选择了不确定，尚不判断方案是否符合本题规则。"
        result["next_step"] = "先核对本题前提和参考依据，再比较各方案的恢复后果。"
        return result
    rule = RULES[record.scenario_id]
    supported = decision["choice_id"] == rule["supported"]
    result.update(
        outcome="supported" if supported else "contradicted",
        rule_id=f"{rule['rule_id']}:{decision['choice_id']}",
        **GUIDANCE[record.scenario_id][decision["choice_id"]],
    )
    return result


def _pool(records: list[TrainingEvidenceRecord]) -> dict[str, Any]:
    practiced = {record.scenario_id for record in records}
    remaining = [sid for sid in POOL_IDS if sid not in practiced]
    return {"version": POOL_VERSION, "total": len(POOL_IDS),
            "practiced_scenario_ids": [sid for sid in POOL_IDS if sid in practiced],
            "remaining_scenario_ids": remaining, "exhausted": not remaining}


def _record_json(record: TrainingEvidenceRecord, available: bool) -> dict[str, Any]:
    return {
        "id": record.id, "scenario_id": record.scenario_id,
        "answer": record.answer, "assistance": record.assistance,
        "created_at": record.created_at, "updated_at": record.updated_at,
        "withdrawn": record.withdrawn,
        "decision_answer": json.loads(record.decision_json) if record.decision_json else None,
        "feedback": _feedback(record, available),
    }


def _recommendation(records: list[TrainingEvidenceRecord]) -> dict[str, Any] | None:
    active = [record for record in records if not record.withdrawn]
    remaining = _pool(records)["remaining_scenario_ids"]
    if not active or not remaining:
        return None
    latest = active[-1]
    scenario_id = remaining[0]
    prompt = next(item["prompt"] for item in SCENARIOS if item["id"] == scenario_id)
    label = ASSISTANCE_LABELS[latest.assistance]
    reason = f"你将记录 #{latest.id} 标注为“{label}”。未记录过作答的情境优先；建议独立尝试，再核对推理依据；此标注不证明已经掌握。"
    if latest.assistance == "unknown":
        reason = f"记录 #{latest.id} 的帮助情况未知，不能由回答质量倒推。未记录过作答的情境优先；请作答并如实标注帮助情况。"
    comparison = "目前只有一条可用记录，尚不能对照作答条件。"
    ids = [latest.id]
    if len(active) > 1:
        previous = active[-2]
        ids.insert(0, previous.id)
        comparison = (
            f"记录 #{previous.id} 标注为“{ASSISTANCE_LABELS[previous.assistance]}”，"
            f"记录 #{latest.id} 标注为“{label}”。这是用户标注的作答条件对照，不是能力评分。"
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
        "records": [_record_json(record, available) for record in records],
        "pool": _pool(records),
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
        payload = command.model_dump()
        # Pre-v40 receipts were hashed without this newly optional field.
        if payload.get("decision_answer") is None:
            payload.pop("decision_answer", None)
        fingerprint = sha256_text(canonical_json({"plan_id": plan_id, **payload}))
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
                    # Bound the evidence used by this plan, not its lifetime
                    # history. Withdrawal releases one active slot but keeps
                    # tombstones and receipts so old retries cannot recreate it.
                    records = list(session.scalars(select(TrainingEvidenceRecord).where(
                        TrainingEvidenceRecord.plan_id == plan_id,
                        TrainingEvidenceRecord.withdrawn.is_(False),
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
                        decision_json=_validate_decision(command.scenario_id, command.decision_answer),
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
                        record.decision_json = _validate_decision(record.scenario_id, command.decision_answer)
                        record.answer = command.answer
                        record.assistance = command.assistance
                    else:
                        # Physical text removal from the live row, not a cosmetic
                        # UI filter. Original plan data remains in its own feature.
                        record.decision_json = None
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
