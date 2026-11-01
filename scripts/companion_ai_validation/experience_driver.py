"""Twelve synthetic experience turns through the actual runtime HTTP endpoint.

This is a bounded backend experience smoke, not Web execution or a semantic
judge. Five isolated workspaces preserve multi-turn history only within a case
family. The caller owns the one-ticket broker and all provider capabilities.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from pathlib import Path
import time
from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from scripts.companion_ai_validation import driver as base
from offerpilot.companion.context import companion_context_message
from offerpilot.companion.models import CompanionChatEvidence, CompanionEvent
from offerpilot.models import (
    Application, ApplicationEvent, ChatMessage, KnowledgeNote, MockInterviewAttempt,
    Offer, PilotTurnMessage, PilotTurnRecord, Resume, WriteOperation,
)
from offerpilot.proactive.models import ProactiveJob

OPERATIONS = tuple(f"x{i:02d}" for i in range(1, 13))
GROUPS = ("a", "b", "c", "d", "e")
CASE_PATH = base.ROOT / "experiments/companion_experience/cases.json"
StageFactory = Callable[[Path], Callable[[str], AbstractContextManager[Any]]]
BUSINESS = (Application, ApplicationEvent, Resume, Offer, KnowledgeNote,
            MockInterviewAttempt, WriteOperation)


def cases() -> tuple[dict[str, str], ...]:
    raw = json.loads(CASE_PATH.read_text())
    base._require(set(raw) == {"schema_version", "scope", "semantics", "cases"}
                  and raw["schema_version"] == 1
                  and raw["scope"] == "runtime_backend_experience_smoke"
                  and raw["semantics"] == "post_run_review_only", "cases", "contract")
    rows = raw["cases"]
    base._require(isinstance(rows, list) and len(rows) == 12, "cases", "count")
    expected_groups = ("a", "a", "a", "b", "b", "c", "c", "c", "d", "d", "e", "e")
    for operation, group, row in zip(OPERATIONS, expected_groups, rows, strict=True):
        base._require(isinstance(row, dict)
                      and set(row) == {"id", "group", "intended_entry", "text", "review"}
                      and row["id"] == operation and row["group"] == group
                      and row["intended_entry"] in {"pilot", "haru"}
                      and all(isinstance(value, str) and 0 < len(value) < 4096
                              for value in row.values()), "cases", "row")
    return tuple(rows)


class _Experience(base._Matrix):
    def __init__(self, data_dir: Path, client: httpx.Client, stage: base.Stage,
                 rows: tuple[dict[str, str], ...],
                 record_response: Callable[[dict[str, Any]], None] | None):
        super().__init__(data_dir, client, stage, record_response)
        self.rows = rows
        self.fixture_reference: dict[str, Any] | None = None

    def business_empty(self, operation: str) -> None:
        with self.sessions() as session:
            base._require(all(session.scalar(select(func.count()).select_from(model)) == 0
                              for model in BUSINESS), operation, "no_business_records_or_writes")
            base._require(session.scalar(select(func.count()).select_from(ChatMessage).where(
                (ChatMessage.role == "tool") | (ChatMessage.tool_calls != ""))) == 0,
                operation, "no_tool_records")
            base._require(session.scalar(select(func.count()).select_from(ProactiveJob).where(
                ProactiveJob.published_at.is_not(None))) == 0,
                operation, "no_unrequested_publish")

    def persona(self, **changes: Any) -> dict[str, Any]:
        current = base._request(self.client, "persona", "GET", "/api/companion/persona")
        before = self.snapshot(self.view())
        value = base._request(self.client, "persona", "POST", "/api/companion/persona/mutations", {
            "operation": "update", "profile_id": current["profile_id"],
            "expected_revision": current["revision"], "command_id": str(uuid4()),
            "confirmed": True, "spec": {**current["spec"], **changes},
        })
        base._require(value["profile_id"] == current["profile_id"]
                      and value["revision"] == current["revision"] + 1
                      and self.snapshot(self.view()) == before,
                      "persona", "mutation_preserves_life")
        self.business_empty("persona")
        return cast(dict[str, Any], value)

    def turn(self, text: str, cid: int = 0, *, case: dict[str, str] | None = None,
             action: str | None = None) -> dict[str, Any]:
        operation = case["id"] if case is not None else "control_" + str(action)
        base._require((case is None) == (action is not None), operation, "turn_kind")
        if case is not None:
            base._require(len(self.responses) < len(self.rows)
                          and case == self.rows[len(self.responses)], operation, "operation_order")
        before = self.view()
        self.business_empty(operation)
        expected = self.context(text, cid) if case is not None else None
        persona = base._request(self.client, operation, "GET", "/api/companion/persona")
        expected_binding = {key: persona[key] for key in ("profile_id", "revision", "content_hash")}
        payload = {"request_id": str(uuid4()), "conversation_id": cid,
                   "message": text, "context_type": "workspace", "mode": "general"}

        def submit() -> tuple[dict[str, Any], dict[str, Any]]:
            admitted = base._request(self.client, operation, "POST", "/api/pilot/runtime/v1/turns",
                                     payload, status=202)
            deadline = time.monotonic() + base.PROCESS_TIMEOUT
            while True:
                state = base._request(self.client, operation, "GET",
                                      f"/api/pilot/runtime/v1/turns/{admitted['turn_id']}")
                if state.get("state") in {"completed", "failed", "interrupted", "result_unknown",
                                          "waiting_confirmation", "waiting_clarification"}:
                    break
                base._require(time.monotonic() < deadline, operation, "runtime_timeout")
                time.sleep(0.05)
            base._require(state.get("state") == "completed", operation, "completed_runtime")
            budget = state.get("budget", {})
            base._require(budget.get("model_calls") == int(case is not None)
                          and budget.get("title_model_calls") == 0
                          and budget.get("tool_calls") == 0,
                          operation, "exact_model_budget")
            return cast(dict[str, Any], admitted), cast(dict[str, Any], state)

        if case is not None:
            with self.stage(operation):
                admitted, status = submit()
        else:
            # No ticket is open; a misclassified control cannot spend a call.
            admitted, status = submit()
        after = self.view()
        base._require(after["identity"] == before["identity"], operation, "same_identity")
        with self.sessions() as session:
            turn = session.get(PilotTurnRecord, admitted["turn_id"])
            base._require(turn is not None and turn.state == "completed", operation, "persisted_turn")
            assert turn is not None
            source = session.get(ChatMessage, turn.user_message_id)
            base._require(source is not None and source.role == "user" and source.content == text
                          and source.conversation_id == admitted["conversation_id"],
                          operation, "real_user_source")
            assert source is not None
            messages = session.scalars(select(ChatMessage).join(
                PilotTurnMessage, PilotTurnMessage.message_id == ChatMessage.id,
            ).where(PilotTurnMessage.turn_id == turn.id).order_by(ChatMessage.id)).all()
            base._require(len(messages) == 2 and messages[1].role == "assistant"
                          and all(not item.tool_calls and not item.tool_call_id for item in messages),
                          operation, "one_plain_assistant_no_tools")
            binding = json.loads(json.loads(turn.source_versions_json)["companion_persona_v1"])
            base._require(binding == expected_binding, operation, "accepted_persona_binding")
            provenance = {"request_id": payload["request_id"], "turn_id": turn.id,
                          "conversation_id": admitted["conversation_id"],
                          "user_message_id": source.id, "assistant_message_id": messages[1].id,
                          "persona_binding": binding,
                          "runtime_budget": {key: status["budget"][key] for key in
                                             ("model_calls", "title_model_calls", "tool_calls")},
                          "transport": "runtime_http_backend",
                          "intended_ui_entry": case["intended_entry"] if case else "control",
                          "web_executed": False, "snapshot_before": self.snapshot(before),
                          "fixture_reference_not_projected": self.fixture_reference}
            evidence = session.scalar(select(CompanionChatEvidence).where(
                CompanionChatEvidence.turn_id == turn.id))
            if case is None:
                base._require(evidence is not None and evidence.action == action
                              and evidence.message_id == source.id
                              and evidence.conversation_id == source.conversation_id
                              and after["revision"] == before["revision"] + 1,
                              operation, "control_provenance")
                assert evidence is not None
                event = session.get(CompanionEvent, evidence.event_id)
                base._require(event is not None and event.source == "user_chat"
                              and event.category == "actual" and event.revision == after["revision"],
                              operation, "actual_control_event")
                self.controls.append({"action": action, "event_id": evidence.event_id,
                                      "participation_id": evidence.participation_id,
                                      "value": evidence.value, **provenance})
            else:
                base._require(evidence is None and self.snapshot(after) == self.snapshot(before),
                              operation, "ordinary_turn_read_only")
                answer = messages[1].content
                base._require(isinstance(answer, str) and bool(answer.strip())
                              and len(answer.encode()) <= 8192, operation, "bounded_ordinary_text")
                envelope = companion_context_message(expected)
                entry = {"operation": operation, "user_input": text, "assistant_text": answer,
                         "expected_context": envelope.content if envelope else None,
                         "source_facts_before_projection": expected,
                         "snapshot": self.snapshot(after), "provenance": provenance,
                         "ordinary_turn_read_only": True, "source_verified": True}
                base._require(len(json.dumps(entry, ensure_ascii=False).encode()) <= 32768,
                              operation, "response_record_bound")
                self.responses.append(entry)
                if self.record_response is not None:
                    try:
                        self.record_response(json.loads(json.dumps(entry)))
                    except Exception:
                        raise base.MatrixFailure(operation, "response_record_failed") from None
        self.business_empty(operation)
        return provenance

    def ordinary(self, index: int, cid: int = 0) -> int:
        row = self.rows[index]
        return int(self.turn(row["text"], cid, case=row)["conversation_id"])

    def control(self, text: str, action: str, cid: int = 0) -> int:
        return int(self.turn(text, cid, action=action)["conversation_id"])

    def run_group(self, group: str) -> dict[str, Any]:
        self.event("app_open")
        if group == "a":
            cid = self.ordinary(0)
            cid = self.ordinary(1, cid)
            self.ordinary(2, cid)
        elif group == "b":
            self.persona(display_name="晴岚", traits=["沉静", "直接"], verbosity="brief",
                         interests_text="天文", fictional_background="住在虚构的星空书屋",
                         speaking_style="沉静直接、简短。", additional_style_notes="工作时认真可靠。")
            cid = self.ordinary(0)
            self.ordinary(1, cid)
        elif group == "c":
            cid = self.control("等我回来一起选颜色", "wait")
            waiting = self.snapshot(self.view())
            self.advance(3 * 86400)
            self.event("app_open")
            self.checked("wait_survives_gap", self.snapshot(self.view()) == waiting)
            cid = self.ordinary(0, cid)
            self.persona(display_name="晴岚", traits=["冷静"], verbosity="brief",
                         interests_text="天文", enabled_activity_templates=[])
            self.checked("persona_change_preserves_wait", self.snapshot(self.view()) == waiting)
            cid = self.ordinary(1, cid)
            self.control("我回来了，我们一起选颜色吧", "join", cid)
            self.control("绿色", "answer", cid)
            chosen = self.view()
            self.checked("shared_step_not_whole_activity", chosen["state"]["activity"]["status"] == "active"
                         and chosen["state"]["activity"]["step_index"] == 0
                         and chosen["shared_participation"][0]["status"] == "completed")
            # Verify the fixture through an explicit recall query, but never
            # substitute that reference into the actual x08 request or context.
            actual = self.context("我们的小船用了什么颜色？", cid)
            self.checked("actual_green_source", actual is not None
                         and actual.get("recent_shared_choice", {}).get("value") == "绿色")
            self.fixture_reference = actual
            self.ordinary(2, cid)
        elif group == "d":
            self.ordinary(0)
            self.ordinary(1)  # Fresh conversation: no third-party story carryover.
        elif group == "e":
            for _ in range(2):
                self.advance(301)
                self.event("app_open")
            cid = self.control("等我回来一起起个名字", "wait")
            self.control("叫棉花糖号吧", "answer", cid)
            prior = self.context(self.rows[0]["text"], 0)
            self.checked("name_source_available", prior is not None
                         and prior.get("recent_shared_choice", {}).get("value") == "棉花糖号")
            base._request(self.client, "delete_source", "DELETE", f"/api/chat/conversations/{cid}")
            erased = self.context(self.rows[0]["text"], 0)
            self.checked("deleted_source_unavailable", erased is not None
                         and erased.get("recall_status") == "source_unavailable"
                         and "recent_shared_choice" not in erased)
            self.ordinary(0)
            self.until_template("paper_boat")
            cid = self.control("等我回来一起选颜色", "wait")
            self.control("这步你自己做吧", "withdraw_shared", cid)
            self.checked("withdrawal_abandoned", any(
                part["status"] == "abandoned" and part["completed_at"] is None
                for part in self.view()["shared_participation"]))
            self.ordinary(1, cid)
        else:
            raise base.MatrixFailure("group", "unknown_group")
        self.business_empty(group)
        self.checked("all_group_turns_completed", len(self.responses) == len(self.rows))
        return {"group": group, "passed": True, "responses": self.responses,
                "controls": self.controls, "assertions": self.checks}


def run_matrix(root_dir: Path, stage_for_data_dir: StageFactory, *,
               record_response: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Create five fresh synthetic workspaces below an empty caller-owned root."""
    base._require(root_dir.is_dir() and not root_dir.is_symlink()
                  and not any(root_dir.iterdir()), "matrix", "fresh_root_required")
    rows = cases()
    results = []
    for group in GROUPS:
        data_dir = root_dir / group
        data_dir.mkdir(mode=0o700)
        (data_dir / base.CLOCK_FILE).write_text(str(base.CLOCK_START))
        with base._api(data_dir) as client:
            matrix = _Experience(data_dir, client, stage_for_data_dir(data_dir),
                                 tuple(row for row in rows if row["group"] == group), record_response)
            try:
                results.append(matrix.run_group(group))
            finally:
                matrix.sessions.kw["bind"].dispose()
    responses = [entry for group in results for entry in group["responses"]]
    base._require(tuple(entry["operation"] for entry in responses) == OPERATIONS,
                  "matrix", "exact_twelve_order")
    result = {"schema_version": 1, "passed": True, "pass_scope": "mechanism_only",
              "synthetic_only": True, "web_executed": False,
              "transport": "uvicorn:/api/pilot/runtime/v1/turns",
              "ordinary_count": len(responses), "tone_review_required": True,
              "semantic_review": "post_run_independent_review_required",
              "context_observations": [{"operation": row["operation"],
                  "actual_context_present": row["expected_context"] is not None,
                  "fixture_reference_present": row["provenance"]["fixture_reference_not_projected"] is not None}
                  for row in responses], "groups": results}
    base._require(len(json.dumps(result, ensure_ascii=False).encode()) <= 262144,
                  "matrix", "result_bound")
    return result
