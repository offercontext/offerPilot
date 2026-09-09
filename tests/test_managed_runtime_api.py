from __future__ import annotations

import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant, ToolCall
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Conversation


class _CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        return Assistant(content="已收到。")


class _PendingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("detached confirmation must not call the provider")
        return Assistant(
            tool_calls=[
                ToolCall(
                    "runtime-action-1",
                    "update_application_status",
                    json.dumps({"id": 1, "status": "interview"}),
                )
            ]
        )


def _workspace_conversations(tmp_path) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    sessions = session_factory_for_data_dir(tmp_path)
    with sessions() as session:
        first = Conversation(title="已有对话")
        second = Conversation(title="另一条对话")
        session.add_all((first, second))
        session.commit()
        return first.id, second.id


def _wait_for_status(
    client: TestClient,
    turn_id: str,
    *,
    terminal: set[str],
    timeout: float = 30.0,
) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get("state") in terminal:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"runtime did not reach {terminal}: {latest}")


def test_runtime_reads_and_replay_keep_existing_conversation_identity(tmp_path) -> None:
    conversation_id, other_conversation_id = _workspace_conversations(tmp_path)
    model = _CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        request_id = str(uuid4())
        body = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "message": "准备面试",
        }
        submitted = client.post("/api/pilot/runtime/v1/turns", json=body)
        assert submitted.status_code == 202, submitted.text
        submission = submitted.json()
        assert submission["conversation_id"] == conversation_id
        assert submission["execution_generation"] == 1

        completed = _wait_for_status(
            client,
            submission["turn_id"],
            terminal={"completed", "failed"},
        )
        assert completed["conversation_id"] == conversation_id
        assert completed["state"] == "completed"
        assert model.calls == 1

        snapshot = client.get(submission["links"]["snapshot_url"])
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["conversation_id"] == conversation_id
        events = client.get(submission["links"]["events_url"])
        assert events.status_code == 200, events.text
        assert "completed" in events.text
        assert model.calls == 1

        replay = client.post("/api/pilot/runtime/v1/turns", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["conversation_id"] == conversation_id
        assert model.calls == 1

        conflicting = client.post(
            "/api/pilot/runtime/v1/turns",
            json={**body, "conversation_id": other_conversation_id},
        )
        assert conflicting.status_code == 409, conflicting.text
        assert conflicting.json()["error_code"] == "turn_conflict"
        assert model.calls == 1


def test_runtime_confirmation_allocates_next_generation_without_provider_retry(tmp_path) -> None:
    model = _PendingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        application = client.post(
            "/api/applications",
            json={"company_name": "Runtime", "position_name": "Backend"},
        ).json()
        submitted = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "context_type": "application",
                "context_ref": str(application["id"]),
                "message": "修改投递状态",
            },
        )
        assert submitted.status_code == 202, submitted.text
        first = submitted.json()
        waiting = _wait_for_status(
            client,
            first["turn_id"],
            terminal={"waiting_confirmation", "failed"},
        )
        assert waiting["state"] == "waiting_confirmation", waiting
        pending = waiting["terminal"]["response"]["pending_action"]

        confirmation_request_id = str(uuid4())
        confirmation_payload = {
            "request_id": confirmation_request_id,
            "conversation_id": first["conversation_id"],
            # Exercise the omitted-operation-id normalization path.  The
            # durable replay key must retain this original shape after the
            # route fills the id from the live Pending.
            "confirmation_token": pending["confirmation_token"],
            "approved": True,
            "edited_args": {"status": "offer"},
        }
        confirmed = client.post(
            f"/api/pilot/runtime/v1/turns/{first['turn_id']}/confirm",
            json=confirmation_payload,
        )
        assert confirmed.status_code == 202, confirmed.text
        second = confirmed.json()
        assert second["conversation_id"] == first["conversation_id"]
        assert second["turn_id"] == first["turn_id"]
        assert second["execution_generation"] == first["execution_generation"] + 1

        replay = client.post(
            f"/api/pilot/runtime/v1/turns/{first['turn_id']}/confirm",
            json=confirmation_payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["execution_generation"] == second["execution_generation"]

        changed = client.post(
            f"/api/pilot/runtime/v1/turns/{first['turn_id']}/confirm",
            json={**confirmation_payload, "edited_args": {"status": "applied"}},
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["error_code"] == "runtime_submission_conflict"

        finished = _wait_for_status(
            client,
            first["turn_id"],
            terminal={"completed", "failed"},
        )
        assert finished["state"] == "completed", finished
        assert finished["execution_generation"] == second["execution_generation"]
        assert model.calls == 1
