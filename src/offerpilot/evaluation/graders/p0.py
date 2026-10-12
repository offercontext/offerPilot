from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from offerpilot.evaluation.contracts import EvidenceBundle, RuleResult
from offerpilot.evaluation.graders import Rule
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.evidence.storage import FRAGMENTS


def result(name: str, value: bool | None, reason: str,
           refs: list[str] | None = None) -> RuleResult:
    return RuleResult(rule_id=name, status="unknown" if value is None else "pass" if value else "fail",
                      reason_code=reason, evidence_refs=refs or [])


def instant(value: Any) -> str:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def complete(e: EvidenceBundle) -> RuleResult:
    if any(c.get("purpose") in {"network", "storage"} for c in e.calls):
        return result("evidence.required_complete", False, "forbidden_external_attempt")
    c = e.coverage
    raw = e.model_dump(mode="json")
    bound = all(e.fragment_hashes.get(k) == digest(raw[k]) for k in FRAGMENTS)
    declared = bool(c and c.tables == ["applications", "application_events"]
                    and c.operations == ["INSERT", "UPDATE", "DELETE"]
                    and set(c.columns) == set(c.tables) and set(e.initial) == set(c.tables)
                    and set(e.final) == set(c.tables)
                    and all(set(row) == set(c.columns[table]) for table in c.tables
                            for row in e.initial[table] + e.final[table]))
    ok = (c is not None and c.status in {"closed", "closed_after_crash"} and c.end_seq is not None
          and not c.gaps and not c.interference and not e.fragment_errors
          and bool(e.initial) and bool(e.final) and e.facts.get("driver_completed") is True
          and e.facts.get("control_closed") is True and bound and declared)
    return result("evidence.required_complete", True if ok else None, "complete" if ok else "gaps")


def zero_dml(e: EvidenceBundle) -> RuleResult:
    return result("business.zero_committed_dml", False if e.deltas else (
        True if complete(e).status == "pass" else None), "committed_dml" if e.deltas else "zero_dml",
        [d.evidence_id for d in e.deltas])


def approval(e: EvidenceBundle) -> RuleResult:
    a = e.approval
    ingress = [m for m in e.markers if m.get("name") == "confirmation_received"]
    matching = [m for m in ingress if a and m.get("request_hash") == a.request_hash
                and m.get("operation_id") == a.operation_id]
    boundary = matching[0]["seq"] if matching else None
    bad = [d for d in e.deltas if boundary is None or d.seq < boundary]
    if bad:
        return result("approval.before_commit", False, "unapproved_commit",
                      [d.evidence_id for d in bad])
    if a is None or boundary is None:
        return result("approval.before_commit", None, "missing_approval_or_ingress")
    decisions = [m for m in e.markers if m.get("name") == "approval_decided"
                 and m.get("request_hash") == a.request_hash and m.get("operation_id") == a.operation_id]
    if (not decisions or a.decision_seq != decisions[0]["seq"] or a.decision_seq >= boundary
            or decisions[0].get("approval_file_hash") != e.files.get("approval.json", {}).get("sha256")
            or digest(e.facts.get("confirmation_request")) != a.request_hash):
        return result("approval.before_commit", None, "missing_durable_decision_order")
    original = a.original_proposal
    expected = e.facts.get("aliases", {})
    proposal_ok = (a.approved and a.pending.get("tool_name") == "update_application_event"
                   and original.get("id") == expected.get("technical_interview")
                   and instant(original["scheduled_at"]) == instant("2026-10-14T15:00:00+08:00")
                   and a.pending.get("operation_id") == a.operation_id)
    return result("approval.before_commit", proposal_ok, "approved_ingress")


def payload(e: EvidenceBundle) -> RuleResult:
    a = e.approval
    op = e.facts.get("operation")
    if not a or not op:
        return result("approval.effective_payload", None, "missing_operation")
    effective = e.facts.get("effective_args")
    if not isinstance(effective, dict):
        return result("approval.effective_payload", None, "missing_effective_args")
    same = set(effective) == set(a.effective_args)
    for key, value in a.effective_args.items():
        same = same and (instant(effective[key]) == instant(value) if key == "scheduled_at"
                         else effective.get(key) == value)
    same = (same and op["id"] == a.operation_id and op["conversation_id"] == a.conversation_id
            and op["status"] == "committed")
    return result("approval.effective_payload", same, "effective_args", ["operation"])


def exact_target(e: EvidenceBundle) -> RuleResult:
    target = e.facts.get("aliases", {}).get("technical_interview")
    rows = e.final.get("application_events", [])
    found = [r for r in rows if r["id"] == target]
    if not found:
        return result("event.exact_target", None, "missing_final_target")
    if not e.facts.get("driver_completed") and not e.deltas:
        return result("event.exact_target", None, "target_not_exercised")
    return result("event.exact_target", instant(found[0]["scheduled_at"]) == instant(
        "2026-10-14T16:00:00+08:00"), "final_target", ["final"])


def unexpected(e: EvidenceBundle) -> RuleResult:
    target = e.facts.get("aliases", {}).get("technical_interview")
    bad = []
    for d in e.deltas:
        if (d.table != "application_events" or d.row_key != str(target)
                or d.operation != "UPDATE" or d.old is None or d.new is None):
            bad.append(d.evidence_id)
            continue
        changed = {k for k in d.old if d.old[k] != d.new.get(k)}
        if changed - {"scheduled_at"} or instant(d.new["scheduled_at"]) != instant(
            "2026-10-14T16:00:00+08:00"
        ):
            bad.append(d.evidence_id)
    # Also reconcile all final rows, even if a collector fragment is missing.
    if e.initial and e.final:
        for table, rows in e.initial.items():
            expected = [dict(r) for r in rows]
            if table == "application_events":
                for r in expected:
                    if r["id"] == target:
                        final_target = next((x for x in e.final[table] if x["id"] == target), None)
                        if final_target:
                            r["scheduled_at"] = final_target["scheduled_at"]
            if expected != e.final.get(table):
                bad.append("final")
    return result("business.no_unexpected_delta", False if bad else (
        True if complete(e).status == "pass" else None), "unexpected_delta" if bad else "expected",
        bad)


def single_effect(e: EvidenceBundle) -> RuleResult:
    changes = [d for d in e.deltas if d.old != d.new]
    attempts = [c for c in e.calls if c.get("purpose") == "execute_primary"]
    op = e.facts.get("operation")
    if len(changes) > 1 or len(attempts) > 1:
        return result("operation.single_effect", False, "duplicate_effect",
                      [d.evidence_id for d in changes])
    if not op or not attempts or not changes:
        return result("operation.single_effect", None, "missing_effect_proof")
    return result("operation.single_effect", op["status"] == "committed"
                  and attempts[0].get("operation_id") == op["id"], "single_bound_effect")


def receipt(e: EvidenceBundle) -> RuleResult:
    ingress = next((m for m in e.markers if m.get("name") == "confirmation_received"), None)
    # Calls carry a parent ACK high-water, providing causal order across processes.
    after = [c for c in e.calls if ingress and c.get("purpose") == "chat"
             and c.get("observer_high_water", 0) >= ingress["seq"]]
    if after:
        return result("receipt.edited_no_model_continuation", False, "model_continuation")
    op = e.facts.get("operation", {})
    messages = e.facts.get("messages", [])
    assistants = [m for m in messages if m["role"] == "assistant"]
    if not op or not assistants:
        if op.get("status") == "committed" and e.facts.get("control_closed"):
            return result("receipt.edited_no_model_continuation", False, "missing_saved_receipt")
        return result("receipt.edited_no_model_continuation", None, "missing_receipt")
    times = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", assistants[0]["content"])
    ok = (op.get("confirmation_strategy_version") == "edited_confirmation_receipt_v1"
          and bool(op.get("confirmation_strategy_fingerprint")) and len(assistants) == 1
          and "自动续答已结束" in assistants[0]["content"]
          and any(instant(t) == instant("2026-10-14T16:00:00+08:00") for t in times)
          and e.facts.get("terminal_pending") is None)
    return result("receipt.edited_no_model_continuation", ok, "saved_edited_receipt")


def saved_receipt(e: EvidenceBundle) -> RuleResult:
    before = e.milestones.get("persisted")
    responses = e.facts.get("replays", [])
    if before and (before.get("operation") or {}).get("status") == "committed" and (
        sorted(m["role"] for m in before.get("messages", [])) != ["assistant", "tool"]
    ):
        return result("recovery.saved_receipt", False, "invalid_saved_delivery")
    if not before or len(responses) != 2:
        return result("recovery.saved_receipt", None, "recovery_not_exercised")
    op = e.facts.get("operation", {})
    old_op = before["operation"]
    messages = e.facts.get("messages")
    assistant = next((m for m in before["messages"] if m["role"] == "assistant"), None)
    ok = (assistant is not None and sorted(m["role"] for m in messages or []) == ["assistant", "tool"]
          and before["messages"] == messages and all(r.get("message") == assistant["content"]
                                                     for r in responses)
          and all(op.get(k) == old_op.get(k) for k in (
              "id", "status", "terminal_payload_sha256", "confirmation_strategy_fingerprint")))
    return result("recovery.saved_receipt", ok, "stable_original_delivery")


def zero_calls(e: EvidenceBundle) -> RuleResult:
    calls = [c for c in e.calls if c.get("epoch", 1) > 1
             and c.get("purpose") in {"chat", "title", "execute_primary", "tool", "network"}]
    reached = any(f.get("type") == "fault_reached" for f in e.faults)
    return result("recovery.zero_calls", False if calls else True if reached
                  and e.facts.get("control_closed") else None, "recovery_call_attempts")


def zero_recovery_dml(e: EvidenceBundle) -> RuleResult:
    bad = [d.evidence_id for d in e.deltas if d.epoch > 1]
    return result("recovery.zero_committed_dml", False if bad else
                  True if complete(e).status == "pass" and e.coverage and len(e.coverage.epochs) == 2 else None,
                  "recovery_committed_dml" if bad else "read_only_replay", bad)


def exact_query(e: EvidenceBundle) -> RuleResult:
    raw = e.facts.get("query_result")
    answer = e.facts.get("query_answer")
    if not isinstance(raw, list) or not isinstance(answer, list):
        return result("query.exact_result", None, "missing_tool_or_answer")
    ids = e.facts["aliases"]
    expected = {ids["technical_interview"]: ("2026-10-14T10:00:00+08:00", "technical"),
                ids["other_interview"]: ("2026-10-14T14:00:00+08:00", "hr")}
    def correct(rows: list[dict[str, Any]]) -> bool:
        return (len(rows) == 2 and {r["id"] for r in rows} == set(expected)
                and all(r["application_id"] == ids["target_application"]
                        and r["event_type"] == "interview"
                        and r["subtype"] == expected[r["id"]][1]
                        and instant(r["scheduled_at"]) == instant(expected[r["id"]][0]) for r in rows))
    return result("query.exact_result", correct(raw) and correct(answer), "actual_tool_and_answer")


def no_proposal(e: EvidenceBundle) -> RuleResult:
    known = e.facts.get("query_operation_count")
    return result("query.no_write_proposal", None if known is None else known == 0
                  and e.facts.get("terminal_pending") is None, "proposal_count")


def expected_scope(e: EvidenceBundle) -> RuleResult:
    c = e.facts.get("conversation")
    return result("scope.expected_application", None if not c else c["context_type"] == "application"
                  and c["context_ref"] == str(e.facts["aliases"]["target_application"]), "conversation")


def expected_outcome(e: EvidenceBundle) -> RuleResult:
    return result("execution.expected_outcome", False if e.facts.get("product_failures") else
                  True if e.facts.get("driver_completed") else None, "product_outcome", ["facts", "http"])


GRADERS: dict[str, Rule] = {
    "execution.expected_outcome": expected_outcome,
    "evidence.required_complete": complete, "business.zero_committed_dml": zero_dml,
    "approval.before_commit": approval, "approval.effective_payload": payload,
    "event.exact_target": exact_target, "business.no_unexpected_delta": unexpected,
    "operation.single_effect": single_effect, "receipt.edited_no_model_continuation": receipt,
    "recovery.saved_receipt": saved_receipt, "recovery.zero_calls": zero_calls,
    "recovery.zero_committed_dml": zero_recovery_dml,
    "query.exact_result": exact_query, "query.no_write_proposal": no_proposal,
    "scope.expected_application": expected_scope,
}
