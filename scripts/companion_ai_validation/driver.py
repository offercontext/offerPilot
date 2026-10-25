"""Synthetic week-like acceptance through the real loopback HTTP product.

Only successful ordinary-turn text is returned for human tone review. Command
receipts are mechanism evidence, not model samples. The caller owns the empty
workspace and a single-request stage which installs/revokes a loopback-only
provider configuration. No credential, user configuration, or provider error
body is inspected here. There are exactly twelve ordinary turns and no retries.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

import httpx
from sqlalchemy import func, select

# Prevent import-time price downloads, including in standalone offline runs.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from offerpilot.companion.context import companion_context_message, read_companion_context
from offerpilot.companion.models import CompanionChatEvidence, CompanionEvent
from offerpilot.companion.repository import companion_publish_guard
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import (
    Application, ApplicationEvent, ChatMessage, KnowledgeNote, MockInterviewAttempt,
    Offer, PilotTurnMessage, PilotTurnRecord, Resume, WriteOperation,
)
from offerpilot.proactive.models import ProactiveJob

OPERATIONS = (
    "independent_play", "shared_wait_after_absence", "actual_color_choice",
    "unpressured_leisure", "paused_gap", "serious_mixed_work",
    "work_finish_continuation", "boat_specific_memory", "cloud_specific_memory",
    "same_type_ambiguity", "deleted_source_memory", "withdrawal_and_boundaries",
)
Stage = Callable[[str], AbstractContextManager[Any]]
ROOT = Path(__file__).resolve().parents[2]
PROCESS_TIMEOUT = 180
RESPONSE_BYTES = 65536
RESULT_BYTES = 131072
CLOCK_START = 1792576800.0  # Synthetic 2026-10-21 10:00 UTC, not provider/budget time.
CLOCK_FILE = "synthetic-companion-clock"


class MatrixFailure(RuntimeError):
    """Fixed labels only: never include process output, config, or error bodies."""

    def __init__(self, operation: str, check: str):
        self.operation, self.check = operation, check
        super().__init__(f"{operation}: {check}")


def _require(condition: Any, operation: str, check: str) -> None:
    if not condition:
        raise MatrixFailure(operation, check)


def child_environment(data_dir: Path) -> dict[str, str]:
    """Construct an allowlist; never inherit secrets/proxies/tracing or HOME."""
    home, temporary = data_dir / "process-home", data_dir / "process-tmp"
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary),
        "LANG": "C.UTF-8", "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "OFFERPILOT_DATA": str(data_dir), "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DO_NOT_TRACK": "1", "NO_PROXY": "127.0.0.1,localhost,::1",
    }


def _capture(stream: BinaryIO, buffer: bytearray) -> None:
    while chunk := stream.read(8192):
        buffer.extend(chunk[:max(0, 65536 - len(buffer))])


def _serve(data_dir: Path, port: int) -> None:
    """Child-only clock seam. Do not monkeypatch the shared time module."""
    from types import SimpleNamespace
    import uvicorn
    import offerpilot.companion.chat as chat
    import offerpilot.companion.repository as repository

    clock = SimpleNamespace(time=lambda: float((data_dir / CLOCK_FILE).read_text()))
    repository.time = clock  # type: ignore[assignment, attr-defined]
    chat.time = clock  # type: ignore[assignment, attr-defined]
    uvicorn.run("offerpilot.api:create_app", factory=True, host="127.0.0.1", port=port,
                log_level="critical", access_log=False)


@contextmanager
def _api(data_dir: Path, operation: str = "matrix") -> Iterator[httpx.Client]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = (
        "import tests._offline_network_guard; from pathlib import Path; "
        "from scripts.companion_ai_validation.driver import _serve; "
        f"_serve(Path({str(data_dir)!r}), {port})"
    )
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", command], cwd=ROOT, env=child_environment(data_dir),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError:
        raise MatrixFailure(operation, "api_process_failed") from None
    buffers = [bytearray(), bytearray()]
    threads = []
    try:
        for stream, buffer in zip((process.stdout, process.stderr), buffers):
            if stream is not None:
                thread = threading.Thread(target=_capture, args=(stream, buffer), daemon=True)
                thread.start()
                threads.append(thread)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                          timeout=PROCESS_TIMEOUT, follow_redirects=False) as client:
            deadline = time.monotonic() + 60
            while True:
                _require(process.poll() is None, operation, "api_startup_failed")
                try:
                    if client.get("/api/health", timeout=0.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                _require(time.monotonic() < deadline, operation, "api_startup_timeout")
                time.sleep(0.05)
            yield client
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        for buffer in buffers:
            buffer.clear()


def _request(client: httpx.Client, operation: str, method: str, path: str,
             payload: dict[str, Any] | None = None, *, status: int = 200) -> Any:
    try:
        with client.stream(method, path, json=payload) as response:
            _require(response.status_code == status, operation,
                     f"http_status_{response.status_code}_expected_{status}")
            body = bytearray()
            for chunk in response.iter_bytes():
                _require(len(body) + len(chunk) <= RESPONSE_BYTES, operation, "http_body_bound")
                body.extend(chunk)
        return json.loads(body)
    except httpx.HTTPError:
        raise MatrixFailure(operation, "http_transport_failed") from None
    except (ValueError, UnicodeError):
        raise MatrixFailure(operation, "http_json_contract") from None


class _Matrix:
    def __init__(self, data_dir: Path, client: httpx.Client, stage: Stage,
                 record_response: Callable[[dict[str, Any]], None] | None = None):
        self.data_dir, self.client, self.stage = data_dir, client, stage
        self.record_response = record_response
        self.sessions = session_factory_for_data_dir(data_dir)
        self.now = CLOCK_START
        self.responses: list[dict[str, Any]] = []
        self.controls: list[dict[str, Any]] = []
        self.checks: dict[str, bool] = {}
        self.presence_id, self.presence_sequence = str(uuid4()), 0

    def advance(self, seconds: float) -> None:
        _require(seconds > 0, "clock", "forward_only")
        self.now += seconds
        clock = self.data_dir / CLOCK_FILE
        replacement = clock.with_suffix(".next")
        replacement.write_text(str(self.now))
        replacement.replace(clock)

    def view(self) -> dict[str, Any]:
        return cast(dict[str, Any], _request(self.client, "state", "GET", "/api/companion"))

    def event(self, event: str) -> dict[str, Any]:
        view = self.view()
        activity = view["state"]["activity"]
        payload = {"identity": view["identity"], "expected_revision": view["revision"],
                   "command_id": str(uuid4()), "event": event,
                   "activity_id": activity["id"] if activity else None}
        result = _request(self.client, event, "POST", "/api/companion/events", payload)
        _require(result["identity"] == view["identity"], event, "same_identity")
        _require(result["revision"] == view["revision"] + int(result["changed"]),
                 event, "single_revision")
        return cast(dict[str, Any], result)

    def settings(self, **changes: Any) -> dict[str, Any]:
        view = self.view()
        return cast(dict[str, Any], _request(self.client, "settings", "PUT", "/api/companion/settings", {
            "identity": view["identity"], "expected_revision": view["revision"],
            "command_id": str(uuid4()), "settings": {**view["settings"], **changes},
        }))

    def presence(self, **flags: bool) -> None:
        self.presence_sequence += 1
        _request(self.client, "presence", "POST", "/api/companion/presence", {
            "identity": self.view()["identity"], "client_id": self.presence_id,
            "sequence": self.presence_sequence, "visible": True, **flags,
        })

    def guard(self) -> bool:
        with self.sessions() as session:
            return companion_publish_guard(session, self.now)

    def context(self, query: str, conversation_id: int) -> dict[str, Any] | None:
        with self.sessions() as session:
            connection = session.connection()
            return read_companion_context(cast(sqlite3.Connection, connection.connection.driver_connection), query,
                                          context_type="workspace", conversation_id=conversation_id)

    def chat(self, operation: str, message: str, conversation_id: int = 0,
             *, control_action: str | None = None) -> dict[str, Any]:
        before = self.view()
        payload = {"request_id": str(uuid4()), "conversation_id": conversation_id,
                   "message": message, "context_type": "workspace", "mode": "general"}
        if control_action is None:
            _require(len(self.responses) < len(OPERATIONS), operation, "ordinary_turn_cap")
            _require(operation == OPERATIONS[len(self.responses)], operation, "operation_order")
            expected = self.context(message, conversation_id)
            with self.stage(operation):
                result = _request(self.client, operation, "POST", "/api/chat", payload)
        else:
            expected = None
            # No stage is open. Unsupported controls must fail, never spend a call.
            result = _request(self.client, operation, "POST", "/api/chat", payload)
        _require(isinstance(result, dict) and not result.get("pending_action")
                 and not result.get("pending_clarification"), operation, "no_tool_or_pending_action")
        with self.sessions() as session:
            turn = session.get(PilotTurnRecord, result.get("turn_id"))
            _require(turn is not None and turn.state == "completed", operation, "completed_turn")
            assert turn is not None
            source = session.get(ChatMessage, turn.user_message_id)
            _require(source is not None and source.role == "user" and source.content == message
                     and source.conversation_id == result["conversation_id"], operation, "real_user_source")
            assert source is not None
            messages = session.scalars(select(ChatMessage).join(
                PilotTurnMessage, PilotTurnMessage.message_id == ChatMessage.id,
            ).where(PilotTurnMessage.turn_id == turn.id).order_by(ChatMessage.id)).all()
            _require(len(messages) == 2 and messages[1].role == "assistant"
                     and all(not item.tool_calls and not item.tool_call_id for item in messages),
                     operation, "one_plain_assistant_no_tools")
            evidence = session.scalar(select(CompanionChatEvidence).where(
                CompanionChatEvidence.turn_id == turn.id))
            provenance = {"request_id": payload["request_id"], "turn_id": turn.id,
                          "conversation_id": result["conversation_id"],
                          "user_message_id": source.id, "assistant_message_id": messages[1].id}
            after = self.view()
            _require(after["identity"] == before["identity"], operation, "same_identity")
            if control_action is not None:
                _require(evidence is not None and evidence.action == control_action
                         and evidence.message_id == source.id
                         and evidence.conversation_id == source.conversation_id,
                         operation, "bounded_control_provenance")
                assert evidence is not None
                _require(after["revision"] == before["revision"] + 1,
                         operation, "single_control_revision")
                event = session.get(CompanionEvent, evidence.event_id)
                _require(event is not None and event.source == "user_chat"
                         and event.category == "actual" and event.activity_id == evidence.activity_id
                         and event.revision == after["revision"], operation, "actual_chat_event")
                assert event is not None
                self.controls.append({"operation": operation, "action": control_action,
                    "event_category": event.category, "event_source": event.source,
                    "occurred_at": event.occurred_at,
                    "activity_id": evidence.activity_id, "participation_id": evidence.participation_id,
                    "event_id": evidence.event_id, "step_kind": evidence.step_kind,
                    "value": evidence.value, "revision": after["revision"], **provenance})
                if operation in {"wait_color", "withdraw_shared"}:
                    replay = _request(self.client, operation, "POST", "/api/chat", payload)
                    _require(replay.get("type") == "turn_recovered"
                             and replay.get("turn_id") == turn.id
                             and self.view()["revision"] == after["revision"],
                             operation, "control_retry_does_not_reexecute")
                    self.checks[operation + "_replay_is_read_only"] = True
            else:
                _require(evidence is None and after["revision"] == before["revision"]
                         and after["state"] == before["state"], operation, "ordinary_turn_read_only")
                text = messages[1].content
                _require(isinstance(text, str) and bool(text.strip())
                         and len(text.encode("utf-8")) <= 8192, operation, "bounded_ordinary_text")
                envelope = companion_context_message(expected)
                projected = envelope.content if envelope else None
                self.responses.append({"operation": operation, "user_input": message,
                    "assistant_text": text, "expected_context": projected,
                    "source_facts_before_projection": expected,
                    "snapshot": self.snapshot(after), "provenance": provenance,
                    "ordinary_turn_read_only": True, "source_verified": True})
                entry = self.responses[-1]
                _require(len(json.dumps(entry, ensure_ascii=False).encode()) <= 32768,
                         operation, "response_record_bound")
                if self.record_response is not None:
                    try:
                        # Only completed ordinary text and verified synthetic source/state.
                        # Copy prevents a writer from changing the live assertion evidence.
                        self.record_response(json.loads(json.dumps(entry)))
                    except Exception:
                        raise MatrixFailure(operation, "response_record_failed") from None
        return provenance

    @staticmethod
    def snapshot(view: dict[str, Any]) -> dict[str, Any]:
        return {key: view[key] for key in (
            "identity", "revision", "settings", "state", "shared_participation",
        )}

    def until_template(self, template: str) -> None:
        # Every wake is explicit and advances at most one step; no state forging.
        for _ in range(16):
            before = self.view()["state"]["activity"]
            if before["template_id"] == template and before["status"] == "active":
                return
            self.advance(1801 if before["status"] == "completed" else 301)
            after = self.event("app_open")["state"]["activity"]
            if after["id"] == before["id"]:
                _require(0 <= after["step_index"] - before["step_index"] <= 1,
                         "lifecycle", "bounded_progress")
        raise MatrixFailure("lifecycle", "template_not_reached")

    def name(self, name: str) -> dict[str, Any]:
        waited = self.chat("wait_name", "等我回来一起起个名字", control_action="wait")
        return self.chat("answer_name", f"叫{name}吧", waited["conversation_id"],
                         control_action="answer")

    def checked(self, name: str, condition: Any) -> None:
        _require(condition, "invariants", name)
        self.checks[name] = True

    def run(self) -> dict[str, Any]:
        first = self.event("app_open")
        boat_id = first["state"]["activity"]["id"]
        self.checked("independent_start_no_wish", first["state"]["wish"] is None
                     and first["shared_participation"] == [])
        self.presence()
        self.checked("read_and_presence_do_not_advance", self.view()["revision"] == first["revision"])
        self.chat(OPERATIONS[0], "你在干嘛？")
        waited = self.chat("wait_color", "等我回来一起选颜色", control_action="wait")
        waiting = self.view()
        self.advance(3 * 86400)
        after_gap = self.event("app_open")
        self.checked("waiting_gap_does_not_advance", after_gap["state"] == waiting["state"]
                     and after_gap["revision"] == waiting["revision"])
        self.chat(OPERATIONS[1], "这几天没来，你的小日常怎么样了？我们约好的一步呢？",
                  waited["conversation_id"])
        self.chat("rejoin_color", "我回来了，我们一起选颜色吧", waited["conversation_id"],
                  control_action="join")
        color = self.chat("answer_color", "蓝色", waited["conversation_id"], control_action="answer")
        colored = self.view()
        self.checked("color_only_completes_shared_step", colored["state"]["activity"]["id"] == boat_id
                     and colored["state"]["activity"]["step_index"] == 0
                     and colored["state"]["activity"]["status"] == "active"
                     and len(colored["shared_participation"]) == 1
                     and colored["shared_participation"][0]["status"] == "completed")
        self.chat(OPERATIONS[2], "我们的小船用了什么颜色？")
        actual = self.responses[-1]["source_facts_before_projection"]["recent_shared_choice"]
        self.checked("color_exact_source", actual["value"] == "蓝色"
                     and actual["source_message_id"] == color["user_message_id"]
                     and actual["source_turn_id"] == color["turn_id"])
        self.advance(301)
        self.event("app_open")
        self.presence()
        self.checked("publish_eligible_before_quiet", self.guard())
        self.event("quiet_session")
        self.presence()
        self.checked("quiet_blocks_publish", not self.guard())
        preserved = self.view()["state"]
        self.settings(proactivity="off")
        self.checked("off_preserves_state_and_blocks_publish", self.view()["state"] == preserved
                     and not self.guard() and self.context("你在干嘛？", 0) is not None)
        self.chat(OPERATIONS[3], "今天什么正事也不想做，就想闲聊发会儿呆。你想玩什么？")
        self.event("resume_session")
        self.settings(proactivity="default")
        self.chat("pause_boat", "暂停你的折纸活动", control_action="pause")
        paused = self.view()
        self.advance(86400)
        self.event("app_open")
        self.checked("paused_gap_does_not_advance", self.view()["state"] == paused["state"])
        self.chat(OPERATIONS[4], "又隔了一天，你的小船暂停后怎么样了？")
        self.chat("resume_boat", "继续折纸吧", control_action="resume")
        self.presence(work_running=True, typing=True)
        blockers = self.view()["presence"]["blockers"]
        self.checked("work_presence_blocks_publish", not self.guard()
                     and "work_running" in blockers and "typing" in blockers)
        work = self.chat(OPERATIONS[5], "先帮我看看这段简历哪里还不清楚，纸小船晚点再聊。\n"
                         "以下全是虚构练习资料：小林，某示例大学计算机专业，本科，毕业时间未填。"
                         "校园预约系统：参与开发，起止时间、本人负责的部分和效果都还没写。"
                         "只根据这些文字指出缺失信息，不要替我补经历，也不要保存或调用工具。")
        self.checked("mixed_work_excludes_life_context", self.responses[-1]["expected_context"] is None)
        self.presence()
        before_work_end = self.view()["state"]["activity"]
        self.advance(301)
        after_work_end = self.event("work_completed")["state"]["activity"]
        self.checked("work_finish_one_step_same_activity", after_work_end["id"] == boat_id
                     and after_work_end["step_index"] == before_work_end["step_index"] + 1)
        self.chat(OPERATIONS[6], "刚才那段已经看完了，不用再谈它。你的小船现在做到哪一步了？",
                  work["conversation_id"])
        boat_name = self.name("棉花糖号")
        self.until_template("cloud_catalog")
        cloud_name = self.name("软糖云")
        self.chat(OPERATIONS[7], "还记得我们的小船叫什么吗？")
        choice = self.responses[-1]["source_facts_before_projection"]["recent_shared_choice"]
        self.checked("boat_not_newer_cloud", choice["value"] == "棉花糖号"
                     and choice["activity_id"] == boat_id
                     and choice["source_message_id"] == boat_name["user_message_id"])
        self.chat(OPERATIONS[8], "我们的云朵叫什么名字？")
        choice = self.responses[-1]["source_facts_before_projection"]["recent_shared_choice"]
        self.checked("cloud_exact_source", choice["value"] == "软糖云"
                     and choice["source_message_id"] == cloud_name["user_message_id"])
        self.until_template("paper_boat")
        second_boat_name = self.name("小月亮号")
        self.chat(OPERATIONS[9], "我们的小船叫什么？")
        ambiguous = self.responses[-1]["source_facts_before_projection"]
        self.checked("same_type_needs_clarification", ambiguous["recall_status"] == "ambiguous_activity"
                     and "recent_shared_choice" not in ambiguous)
        for conversation in (waited, boat_name, second_boat_name):
            _request(self.client, "delete_source", "DELETE",
                     f"/api/chat/conversations/{conversation['conversation_id']}")
        self.chat(OPERATIONS[10], "还记得我们的小船叫什么吗？")
        deleted = self.responses[-1]["source_facts_before_projection"]
        self.checked("deleted_source_not_reconstructed", deleted["recall_status"] == "source_unavailable"
                     and "recent_shared_choice" not in deleted)
        with self.sessions() as session:
            erased_ids = [item["conversation_id"] for item in (waited, boat_name, second_boat_name)]
            self.checked("deleted_source_rows_erased", session.scalar(select(func.count()).select_from(
                CompanionChatEvidence).where(CompanionChatEvidence.conversation_id.in_(erased_ids))) == 0)
        withdrawal = self.chat("wait_withdrawal", "等我回来一起选颜色", control_action="wait")
        self.chat("pause_withdrawal", "暂停你的折纸活动", withdrawal["conversation_id"],
                  control_action="pause")
        before_withdrawal = self.view()
        self.chat("withdraw_shared", "这步你自己做吧", withdrawal["conversation_id"],
                  control_action="withdraw_shared")
        withdrawn = self.view()
        latest_part = next(p for p in withdrawn["shared_participation"]
                           if p["activity_id"] == withdrawn["state"]["activity"]["id"])
        self.checked("withdrawal_not_completion_or_debt", latest_part["status"] == "abandoned"
                     and latest_part["completed_at"] is None
                     and withdrawn["state"]["activity"]["status"] == "paused"
                     and withdrawn["state"]["activity"]["step_index"]
                     == before_withdrawal["state"]["activity"]["step_index"]
                     and withdrawn["state"]["wish"] is None)
        self.advance(86400)
        self.event("app_open")
        self.checked("withdrawal_remains_paused", self.view()["state"] == withdrawn["state"])
        self.chat(OPERATIONS[11], "这一步我不参加了。你的小日常现在怎么样？"
                  "我不在时你会自己看视频、每天练习吗？我今天不想变成熟，也不想被催着变厉害。",
                  withdrawal["conversation_id"])
        self.chat("resume_after_withdrawal", "继续折纸吧", withdrawal["conversation_id"],
                  control_action="resume")
        self.checked("withdrawal_normal_delay", not self.event("app_open")["changed"])
        self.advance(301)
        self.checked("withdrawal_independent_continuation", self.event("app_open")["changed"])
        final = self.view()
        self.settings(enabled=False)
        self.checked("disabled_omits_context", self.context("你在干嘛？", 0) is None and not self.guard())
        before_disabled_wake = self.view()
        self.advance(86400)
        self.event("app_open")
        self.checked("disabled_wake_preserves_state", self.view()["state"] == before_disabled_wake["state"])
        self.checked("exactly_twelve_ordinary_responses", len(self.responses) == 12)
        with self.sessions() as session:
            self.checked("no_tool_records", session.scalar(select(func.count()).select_from(ChatMessage).where(
                (ChatMessage.role == "tool") | (ChatMessage.tool_calls != ""))) == 0)
            self.checked("bounded_queue_evidence", (session.scalar(
                select(func.count()).select_from(ProactiveJob)) or 0) <= 64)
            queue = [{"id": job.id, "state": job.state, "published": job.published_at is not None,
                      "source_digest": job.source_digest, "reason": job.reason}
                     for job in session.scalars(select(ProactiveJob).limit(64))]
            self.checked("no_unrequested_publish", all(not job["published"] for job in queue))
            self.checked("no_business_records_or_writes", all(
                session.scalar(select(func.count()).select_from(model)) == 0
                for model in (Application, ApplicationEvent, Resume, Offer,
                              KnowledgeNote, MockInterviewAttempt, WriteOperation)
            ))
        result = {"passed": True, "pass_scope": "mechanism_only", "schema_version": 1, "synthetic_only": True, "transport": "uvicorn:/api/chat",
                  "clock_scope": "isolated companion repository/chat only; Pilot/provider clocks unchanged",
                  "ordinary_count": len(self.responses), "tone_review_required": True,
                  "responses": self.responses, "controls": self.controls, "assertions": self.checks,
                  "queue": queue, "final_snapshot_before_disable": self.snapshot(final)}
        _require(len(json.dumps(result, ensure_ascii=False).encode()) <= RESULT_BYTES,
                 "matrix", "result_bound")
        return result


def run_matrix(data_dir: Path, stage: Stage, *,
               record_response: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Run in a fresh caller-owned directory; leave cleanup ownership to caller."""
    data_dir = data_dir.resolve()
    _require(data_dir.is_dir() and not any(data_dir.iterdir()), "matrix", "fresh_data_dir")
    (data_dir / CLOCK_FILE).write_text(str(CLOCK_START))
    with _api(data_dir) as client:
        matrix = _Matrix(data_dir, client, stage, record_response)
        try:
            return matrix.run()
        finally:
            matrix.sessions.kw["bind"].dispose()
