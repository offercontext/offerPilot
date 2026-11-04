"""Four authorized experience samples; setup never opens a provider ticket.

A/B/C share history; D is fresh. Frozen persona binding is audit metadata, not
proof of expression: off turns retain it but must not project persona packages.
Tone and grounding still require independent review of actual model responses.
"""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from offerpilot.models import ChatMessage, PilotTurnRecord
from scripts.companion_ai_validation import driver as base
from scripts.companion_ai_validation import experience_driver as experience

OPERATIONS = ("a_default_off", "b_enabled_chat", "c_disabled_work", "d_disabled_identity")
TEXTS = (
    "我今天有点累，只想随便聊两句。",
    "今天只想放空一会儿。你的小日常怎么样了？",
    "帮我看看“参与校园图书管理系统”这句话还缺哪些信息，只分析，不保存。",
    "你叫什么？平时喜欢什么？",
)
PERSONA = {
    "display_name": "晴岚", "traits": ["沉静", "直接"], "verbosity": "brief",
    "interests_text": "天文", "fictional_background": "住在虚构的星空书屋",
    "speaking_style": "沉静直接、简短。", "additional_style_notes": "工作时认真可靠。",
    "enabled_activity_templates": ["paper_boat"],
}


def cases() -> tuple[dict[str, str], ...]:
    return tuple({"id": operation, "group": "fourcase", "intended_entry": entry,
                  "text": text, "review": "post_run_independent_review_required"}
                 for operation, text, entry in zip(OPERATIONS, TEXTS,
                                                   ("pilot", "haru", "pilot", "pilot"), strict=True))


class _FourCase(experience._Experience):
    def ordinary(self, index: int, cid: int = 0) -> int:
        before = self.view()
        enabled = before["settings"]["enabled"]
        result = super().ordinary(index, cid)
        row = self.responses[-1]
        with self.sessions() as session:
            turn = session.get(PilotTurnRecord, row["provenance"]["turn_id"])
            assert turn is not None
            versions = json.loads(turn.source_versions_json)
            self.checked(OPERATIONS[index] + "_frozen_switch",
                         json.loads(versions["companion_enabled_v1"]) is enabled)
        self.checked(OPERATIONS[index] + "_context_policy",
                     (row["expected_context"] is not None) is (index == 1))
        return result

    def run(self) -> dict[str, Any]:
        before = self.view()
        self.checked("fresh_default_off", before["settings"]["enabled"] is False
                     and before["state"]["activity"] is None)
        persona = self.persona(**PERSONA)
        self.checked("saved_persona_does_not_enable", self.view()["settings"]["enabled"] is False)
        self.checked("off_app_open_does_not_initialize", self.event("app_open")["changed"] is False
                     and self.view()["state"]["activity"] is None)
        cid = self.ordinary(0)
        self.settings(enabled=True)
        self.event("app_open")
        activity = self.view()["state"]["activity"]
        self.checked("enabled_paper_step1", activity is not None
                     and activity["template_id"] == "paper_boat"
                     and activity["step_index"] == 0 and activity["status"] == "active"
                     and self.view()["shared_participation"] == [])
        self.checked("b_same_conversation", self.ordinary(1, cid) == cid)
        saved_state = self.view()["state"]
        self.settings(enabled=False)
        self.checked("disable_preserves_life", self.view()["state"] == saved_state)
        preserved = base._request(self.client, "persona", "GET", "/api/companion/persona")
        self.checked("disable_preserves_persona", preserved == persona)
        with self.sessions() as session:
            assistants = list(session.scalars(select(ChatMessage).where(
                ChatMessage.conversation_id == cid, ChatMessage.role == "assistant",
            )))
            self.checked("disabled_history_retained", len(assistants) == 2)
        self.checked("c_same_conversation", self.ordinary(2, cid) == cid)
        self.checked("d_fresh_conversation", self.ordinary(3) != cid)
        self.checked("remains_disabled", self.view()["settings"]["enabled"] is False)
        self.business_empty("fourcase")
        self.checked("exact_four_order", tuple(row["operation"] for row in self.responses) == OPERATIONS)
        return {"group": "fourcase", "passed": True, "responses": self.responses,
                "controls": self.controls, "assertions": self.checks}


def run_matrix(root_dir: Path, stage_for_data_dir: experience.StageFactory, *,
               record_response: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Use exactly one empty caller-owned workspace and four single-call stages."""
    base._require(root_dir.is_dir() and not root_dir.is_symlink()
                  and not any(root_dir.iterdir()), "matrix", "fresh_root_required")
    data_dir = root_dir / "fourcase"
    data_dir.mkdir(mode=0o700)
    (data_dir / base.CLOCK_FILE).write_text(str(base.CLOCK_START))
    with base._api(data_dir) as client:
        matrix = _FourCase(data_dir, client, stage_for_data_dir(data_dir), cases(), record_response)
        try:
            group = matrix.run()
        finally:
            matrix.sessions.kw["bind"].dispose()
    result = {"schema_version": 1, "passed": True, "pass_scope": "mechanism_only",
              "synthetic_only": True, "web_executed": False,
              "transport": "uvicorn:/api/pilot/runtime/v1/turns", "ordinary_count": 4,
              "tone_review_required": True, "semantic_review": "post_run_independent_review_required",
              "context_observations": [{"operation": row["operation"],
                  "actual_context_present": row["expected_context"] is not None,
                  "fixture_reference_present": False} for row in group["responses"]],
              "groups": [group]}
    base._require(len(json.dumps(result, ensure_ascii=False).encode()) <= 262144,
                  "matrix", "result_bound")
    return result
