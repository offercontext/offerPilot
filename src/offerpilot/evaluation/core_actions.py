"""Finite user script. Model text never supplies approval or arbitrary API paths."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from offerpilot.evaluation.adapters import DriverError, HTTPDriver, ProductFailure
from offerpilot.evaluation.graders.p0 import instant
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import operation_facts
from scripts.evaluation_subject import core_observer as observer

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "stopped", "result_unknown"}


class CoreHTTPDriver(HTTPDriver):
    def __init__(
        self,
        address: str,
        token: str,
        deadline: float,
        observations: list[dict[str, Any]],
        replies: int,
        reply_counter: list[int] | None = None,
    ) -> None:
        super().__init__(address, token, deadline, observations)
        self.max_replies = replies
        self.reply_counter = reply_counter if reply_counter is not None else [0]

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        allowed: tuple[int, ...] = (200, 202),
    ) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("trial watchdog")
        if method == "POST":
            self.reply_counter[0] += 1
            self.script_replies = self.reply_counter[0]
            if self.reply_counter[0] > self.max_replies:
                raise TimeoutError("script reply watchdog")
        response = self.client.request(method, path, json=body, timeout=min(30, remaining))
        try:
            payload = response.json()
        except ValueError as exc:
            self.observations.append(
                dict(
                    method=method,
                    path=path,
                    body=body,
                    status=response.status_code,
                    response=response.text[:524288],
                )
            )
            if response.status_code not in allowed:
                raise ProductFailure(f"HTTP {response.status_code} on {path}") from exc
            raise DriverError("unhandled non-JSON response") from exc
        self.observations.append(
            dict(method=method, path=path, body=body, status=response.status_code, response=payload)
        )
        if response.status_code not in allowed:
            raise ProductFailure(f"HTTP {response.status_code} on {path}")
        if not isinstance(payload, dict):
            raise DriverError("unhandled non-object response")
        return payload


def delivery_lease_expiry(value: Any) -> datetime:
    if type(value) is not int:
        raise ValueError("delivery lease must use the actual Unix-seconds contract")
    return datetime.fromtimestamp(value, tz=timezone.utc)


def stream_cursor(line: str) -> str | None:
    if line.startswith("id:"):
        return line[3:].strip() or None
    if line.startswith("data:"):
        try:
            value = json.loads(line[5:])
        except ValueError:
            return None
        if isinstance(value, dict) and isinstance(value.get("cursor"), str):
            return value["cursor"] or None
    return None


def wait(driver: HTTPDriver, turn: str, states: set[str], facts: dict[str, Any]) -> dict[str, Any]:
    while time.monotonic() < driver.deadline:
        value = driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn}")
        if value.get("budget"):
            facts.setdefault("budget_snapshots", []).append(value["budget"])
        if value["state"] in states | TERMINAL:
            return value
        time.sleep(0.05)
    raise TimeoutError("product status did not converge")


def drive(
    item: str,
    driver: CoreHTTPDriver,
    subject: Any,
    private: Path,
    e: dict[str, Any],
    restart: Callable[[], tuple[Any, CoreHTTPDriver]],
) -> tuple[Any, CoreHTTPDriver]:
    f = e["facts"]
    aliases = f["aliases"]
    data = private / "data.db"
    core = json.loads(
        (Path(__file__).resolve().parents[3] / "evals/fixtures/synthetic-core-v1.json").read_bytes()
    )
    message = (
        "列出云岚数据测试开发在2026年10月的面试，只查询"
        if item.startswith(("E01.", "E07.", "E08.runtime_deadline"))
        else "将云岚数据测试开发2026年10月技术面试从10点改到15点"
    )
    if item.startswith("E02."):
        message = "把云岚数据测试开发2026年10月的一场面试改到15点。请先澄清是哪一场。"
    if item.startswith("E09."):
        message = "只读回答：" + core["current_format"] + "给出面试准备建议，不做任何写入。"
    if item.startswith("E10."):
        message = core["question"]
    body = {
        "conversation_id": 0,
        "context_type": "application",
        "context_ref": str(aliases["target_application"]),
        "message": message,
        "page_context": {
            "view": "calendar",
            "label": "面试日程",
            "entity": {
                "kind": "application",
                "id": str(aliases["target_application"]),
                "label": "云岚数据／测试开发",
            },
        },
    }
    legacy = "legacy" in item
    if not legacy:
        body["request_id"] = str(uuid4())
    submitted = driver.request(
        "POST", "/api/chat" if legacy else "/api/pilot/runtime/v1/turns", body
    )
    conversation = submitted["conversation_id"]
    turn = str(submitted.get("turn_id") or "")
    f.update(
        conversation_id=conversation,
        turn_id=turn,
        submit_ids=[body.get("request_id")],
        generation=submitted.get("execution_generation"),
    )
    if item == "E07.runtime_reattach":
        with driver.client.stream("GET", submitted["links"]["events_url"], timeout=30) as stream:
            lines = stream.iter_lines()
            captured = []
            for line in lines:
                captured.append(line)
                cursor = stream_cursor(line)
                if cursor:
                    f["disconnect_cursor"] = cursor
                if f.get("disconnect_cursor") and any(
                    x.get("type") == "fault_reached" for x in subject.events
                ):
                    break
            e["milestones"]["disconnected_sse"] = captured
        f["reattached"] = False
        barrier = next(
            v
            for v in subject.events
            if v.get("type") == "fault_reached"
            and v.get("boundary") in {"model_entered", "http_before_send"}
        )
        f["disconnect_state"] = driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn}")[
            "state"
        ]
        observer.marker(
            data,
            "stream_disconnected_before_release",
            {"control_seq": barrier["seq"], "state": f["disconnect_state"]},
        )
        subject.send({"command": "ack", "seq": barrier["seq"]})
    if item == "E08.runtime_deadline":
        second = driver.request(
            "POST", "/api/pilot/runtime/v1/turns", body | {"request_id": str(uuid4())}
        )
        queued = driver.request(
            "POST", "/api/pilot/runtime/v1/turns", body | {"request_id": str(uuid4())}
        )
        f["queued_admission"] = queued
        f["queued_probe"] = wait(driver, queued["turn_id"], TERMINAL, {})
        f["queue_occupiers"] = [submitted["turn_id"], second["turn_id"]]
        barriers = [
            v
            for v in subject.events
            if v.get("type") == "fault_reached" and v.get("boundary") == "model_entered"
        ]
        f["deadline_model_barriers"] = len(barriers)
        for barrier in barriers:
            subject.send({"command": "ack", "seq": barrier["seq"]})
    current = submitted if legacy else wait(driver, turn, {"waiting_confirmation"}, f)
    response = current if legacy else current.get("terminal", {}).get("response", {})
    f.update(
        terminal_state=current.get("state"),
        answer=response.get("message", ""),
        pending=response.get("pending_action"),
    )
    if item == "E02.runtime_clarify":
        f["ambiguous_pending"] = f["pending"]
        f["clarification"] = f["answer"]
        e["milestones"]["before_clarification"] = observer.read(data)
        if f["pending"]:
            raise ProductFailure("ambiguous request produced a pending write")
        answer = body | {
            "conversation_id": conversation,
            "request_id": str(uuid4()),
            "message": "明确选择技术面试，改到15点。",
        }
        submitted = driver.request("POST", "/api/pilot/runtime/v1/turns", answer)
        turn = submitted["turn_id"]
        f["turn_id"] = turn
        current = wait(driver, turn, {"waiting_confirmation"}, f)
        response = current.get("terminal", {}).get("response", {})
        f["pending"] = response.get("pending_action")
    if item.startswith(
        (
            "E01.",
            "E07.",
            "E08.runtime_deadline",
            "E08.runtime_model_budget",
            "E08.runtime_tool_budget",
            "E09.",
            "E10.",
        )
    ):
        if item == "E07.runtime_reattach":
            cursor = f.get("disconnect_cursor")
            if not cursor:
                raise ValueError("no actual SSE cursor observed")
            with driver.client.stream(
                "GET", submitted["links"]["events_url"], params={"cursor": cursor}, timeout=30
            ) as stream:
                raw = "\n".join(stream.iter_lines())
                e["milestones"]["reattached_sse"] = raw
                f["reattached"] = stream.status_code == 200
        if item == "E09.memory_withdraw":
            before = observer.marker(
                data, "fixture_control_decided", {"memory_id": aliases["memory_id"]}
            )
            change = {
                "mutation_id": str(uuid4()),
                "action": "withdraw",
                "expected_version": aliases["memory_version"],
                "confirmed": True,
                "content": "",
            }
            result = driver.request("POST", f"/api/confirmed-memory/{aliases['memory_id']}", change)
            end = observer.marker(
                data, "fixture_control_completed", {"memory_id": aliases["memory_id"]}
            )
            f["control_writes"] = [
                {
                    "start": before,
                    "end": end,
                    "actor": "fixture_control",
                    "memory_id": aliases["memory_id"],
                    "mutation_id": change["mutation_id"],
                    "expected_version": change["expected_version"],
                    "wire_hash": __import__("hashlib")
                    .sha256(
                        driver.client.build_request(
                            "POST", f"/api/confirmed-memory/{aliases['memory_id']}", json=change
                        ).content
                    )
                    .hexdigest(),
                    "result": result,
                    "action": "withdraw",
                }
            ]
            f["withdraw_control_seq"] = subject.events[-1]["seq"]
            f["withdraw_result"] = result
            fresh = driver.request(
                "POST",
                "/api/pilot/runtime/v1/turns",
                body | {"conversation_id": conversation, "request_id": str(uuid4())},
            )
            current = wait(driver, fresh["turn_id"], TERMINAL, f)
            response = current.get("terminal", {}).get("response", {})
            f.update(answer=response.get("message", ""), pending=response.get("pending_action"))
        f["terminal_state"] = current.get("state")
        if f["terminal_state"] in {"result_unknown", "interrupted"} and item.startswith("E08."):
            f["termination_source"] = "product_budget"
        elif f["terminal_state"] != "completed":
            raise ProductFailure("unexpected product terminal")
        f["driver_completed"] = True
        return subject, driver
    pending = f["pending"]
    if not pending:
        raise ProductFailure("expected pending write missing")
    args = pending.get("args", pending.get("arguments"))
    args = json.loads(args) if isinstance(args, str) else args
    f["proposed_id"] = args.get("id")
    if (
        pending.get("tool_name") != "update_application_event"
        or args.get("id") != aliases["technical_interview"]
        or instant(args.get("scheduled_at")) != instant("2026-10-14T15:00:00+08:00")
    ):
        raise ProductFailure("wrong proposal; no automatic correction")
    operation = pending["operation_id"]
    f["operation_id"] = operation
    effective_time = (
        "2026-10-14T16:00:00+08:00" if item.startswith("E04.") else "2026-10-14T15:00:00+08:00"
    )
    confirm = {
        "conversation_id": conversation,
        "operation_id": operation,
        "confirmation_token": pending["confirmation_token"],
        "approved": item != "E03.runtime_reject",
    }
    if item.startswith("E04."):
        confirm["edited_args"] = {"scheduled_at": effective_time}
    if turn:
        confirm["request_id"] = str(uuid4())
    atomic_json(private / "confirm-request.json", confirm)
    decision = observer.marker(
        data,
        "approval_decided",
        {"request_hash": digest(confirm), "file_hash": file_hash(private / "confirm-request.json")},
    )
    # HTTPDriver uses JSON encoding; hash exact serialized bytes as sent by httpx.
    request = driver.client.build_request("POST", "/", json=confirm)
    import hashlib

    if confirm["approved"]:
        f["approvals"] = [
            {
                "decision_seq": decision,
                "wire_hash": hashlib.sha256(request.content).hexdigest(),
                "effective_time": effective_time,
                "operation_id": operation,
            }
        ]
    else:
        f["reject_high_water"] = decision
    path = "/api/chat/confirm" if legacy else f"/api/pilot/runtime/v1/turns/{turn}/confirm"
    if item.startswith("E06."):
        errors = []

        def blocked_confirm() -> None:
            try:
                driver.request("POST", path, confirm)
            except Exception as exc:
                errors.append(type(exc).__name__)

        worker = threading.Thread(target=blocked_confirm)
        worker.start()
        barrier = subject.wait("fault_reached", 45)
        e["faults"].append(barrier)
        premise = operation_facts(data, operation, conversation)
        e["milestones"]["fault_premise"] = premise
        if premise["operation"]["status"] != "committed":
            raise ProductFailure("fault did not follow committed business outcome")
        if "missing" in item and premise["messages"]:
            raise ProductFailure("missing-delivery premise already has messages")
        if "saved" in item and len(premise["messages"]) != 2:
            raise ProductFailure("saved-delivery premise incomplete")
        subject.stop(crash=True)
        worker.join(5)
        if worker.is_alive() or not subject.closed:
            raise TimeoutError("original writer not joined")
        driver.close()
        subject, driver = restart()
        if legacy:
            if "missing" in item:
                response = driver.request("POST", path, confirm, allowed=(200, 202, 409))
                f["active_lease_pending"] = (
                    response.get("error_code") == "operation_delivery_pending"
                )
                lease = premise["operation"].get("delivery_lease_expires_at")
                if not lease:
                    raise ValueError("real product delivery lease absent")
                expiry = delivery_lease_expiry(lease)
                while datetime.now(timezone.utc) <= expiry:
                    if time.monotonic() + 1 > driver.deadline:
                        raise TimeoutError("natural lease exceeds frozen allowance")
                    time.sleep(
                        min(0.25, max(0.01, (expiry - datetime.now(timezone.utc)).total_seconds()))
                    )
                f["natural_expiry_observed"] = True
            f["replays"] = [
                driver.request("POST", path, confirm),
                driver.request("POST", path, confirm),
            ]
        else:
            f["restart_reads"] = [
                driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn}"),
                driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn}/snapshot"),
            ]
            f["after_restart_state"] = f["restart_reads"][0]["state"]
        f.update(operation_facts(data, operation, conversation))
    else:
        accepted = driver.request("POST", path, confirm)
        f["generation"] = accepted.get("execution_generation")
        terminal = wait(driver, turn, TERMINAL, f)
        f["terminal_state"] = terminal["state"]
        f["answer"] = terminal.get("terminal", {}).get("response", {}).get("message", "")
        if item == "E08.runtime_unknown":
            f["primary_barrier"] = any(
                x.get("boundary") == "accepted_primary_tool" for x in subject.events
            )
            for barrier in subject.events:
                if (
                    barrier.get("type") == "fault_reached"
                    and barrier.get("boundary") == "accepted_primary_tool"
                ):
                    subject.send({"command": "ack", "seq": barrier["seq"]})
            time.sleep(3)
            f["late_state"] = driver.request("GET", f"/api/pilot/runtime/v1/turns/{turn}")["state"]
        elif item == "E05.runtime_replay":
            f["replays"] = [
                driver.request("POST", path, confirm),
                driver.request("POST", path, confirm),
            ]
        elif item == "E05.runtime_conflict":
            changed = confirm | {"edited_args": {"scheduled_at": "2026-10-14T17:00:00+08:00"}}
            f["conflict_body"] = driver.request("POST", path, changed, allowed=(200, 202, 409))
            f["conflict_code"] = e["http"][-1]["status"]
        elif terminal["state"] != "completed":
            raise ProductFailure("confirmation did not complete")
        f.update(operation_facts(data, operation, conversation))
    f["driver_completed"] = True
    return subject, driver
