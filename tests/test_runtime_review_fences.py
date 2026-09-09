from __future__ import annotations

import asyncio
import json
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.ai.types import Assistant, ToolCall
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Conversation, PilotExecution, PilotTurnRecord, WriteOperation
from offerpilot.pilot_runtime.contracts import AssistantMessageEvent
from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager, RuntimeTurnNotFound
from offerpilot.pilot_timeline import PilotTimelineRepository
from offerpilot.runtime_transport import runtime_subscription_response


class _CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        return Assistant(content="已收到。")


class _PendingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("wrong-turn rejection must not call the provider")
        return Assistant(
            tool_calls=[
                ToolCall(
                    "runtime-action-1",
                    "update_application_status",
                    json.dumps({"id": 1, "status": "interview"}),
                )
            ]
        )


class _BlockingModel:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.entered.set()
        assert self.release.wait(10), "test model was not released"
        return Assistant(content="完成。")


class _SequenceModel:
    def __init__(self) -> None:
        self.responses = iter(("A response", "B response"))

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        return Assistant(content=next(self.responses))


def _wait_for_status(
    client: TestClient,
    turn_id: str,
    terminal: set[str],
    *,
    timeout: float = 30,
) -> dict:
    deadline = monotonic() + timeout
    latest: dict = {}
    while monotonic() < deadline:
        response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get("state") in terminal:
            return latest
        sleep(0.05)
    raise AssertionError(f"runtime did not reach {terminal}: {latest}")


@pytest.mark.parametrize("source", ["conversation", "application"])
def test_runtime_status_events_and_snapshot_hide_removed_sources(tmp_path, source) -> None:  # type: ignore[no-untyped-def]
    model = _CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    with TestClient(app) as client:
        application_id: int | None = None
        if source == "application":
            application = client.post(
                "/api/applications",
                json={"company_name": "Runtime", "position_name": "Backend"},
            )
            assert application.status_code == 201, application.text
            application_id = application.json()["id"]
            body = {
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "context_type": "application",
                "context_ref": str(application_id),
                "message": "读取投递上下文",
            }
        else:
            body = {
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "message": "读取对话上下文",
            }
        submitted = client.post("/api/pilot/runtime/v1/turns", json=body)
        assert submitted.status_code == 202, submitted.text
        payload = submitted.json()
        turn_id = payload["turn_id"]
        conversation_id = payload["conversation_id"]
        completed = _wait_for_status(client, turn_id, {"completed"})
        assert completed["terminal"]["response"]["message"] == "已收到。"

        if source == "application":
            deleted = client.delete(f"/api/applications/{application_id}")
        else:
            deleted = client.delete(f"/api/chat/conversations/{conversation_id}")
        assert deleted.status_code == 200, deleted.text

        for suffix in ("", "/events", "/snapshot"):
            response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}{suffix}")
            assert response.status_code == 404, response.text
            assert response.json()["error_code"] in {
                "turn_not_found",
                "conversation_not_found",
            }


def test_durable_result_unknown_overrides_a_still_running_manager(tmp_path) -> None:
    model = _BlockingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    factory = session_factory_for_data_dir(tmp_path)
    with TestClient(app) as client:
        submitted = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "message": "等待持久化围栏",
            },
        )
        assert submitted.status_code == 202, submitted.text
        turn_id = submitted.json()["turn_id"]
        assert model.entered.wait(10)
        _wait_for_status(client, turn_id, {"running"})

        with factory() as session:
            execution = session.get(PilotExecution, (turn_id, 1))
            assert execution is not None
            execution.state = "result_unknown"
            session.commit()

        status = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["state"] == "result_unknown"
        assert body["execution"]["state"] == "result_unknown"
        assert body["terminal"] == {}
        model.release.set()

    assert model.release.is_set()


def test_wrong_turn_confirmation_rejection_and_terminal_replay_fail_closed(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    model = _PendingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    factory = session_factory_for_data_dir(tmp_path)
    with TestClient(app) as client:
        application = client.post(
            "/api/applications",
            json={"company_name": "Runtime", "position_name": "Backend"},
        )
        assert application.status_code == 201, application.text
        submitted = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "context_type": "application",
                "context_ref": str(application.json()["id"]),
                "message": "请准备一次待确认操作",
            },
        )
        assert submitted.status_code == 202, submitted.text
        first = submitted.json()
        waiting = _wait_for_status(client, first["turn_id"], {"waiting_confirmation"})
        pending = waiting["terminal"]["response"]["pending_action"]
        operation_id = pending["operation_id"]

        second = PilotTimelineRepository(factory).admit(
            str(uuid4()),
            {
                "message": "另一条 Turn 不应接管确认",
                "conversation_id": first["conversation_id"],
            },
        )
        rejection = client.post(
            f"/api/pilot/runtime/v1/turns/{second.turn_id}/confirm",
            json={
                "request_id": str(uuid4()),
                "conversation_id": first["conversation_id"],
                "operation_id": operation_id,
                "confirmation_token": pending["confirmation_token"],
                "approved": False,
            },
        )
        assert rejection.status_code == 409, rejection.text
        assert rejection.json()["error_code"] == "operation_identity_conflict"

        with factory() as session:
            operation = session.get(WriteOperation, operation_id)
            conversation = session.get(Conversation, first["conversation_id"])
            wrong_turn = session.get(PilotTurnRecord, second.turn_id)
            wrong_execution = list(
                session.scalars(
                    select(PilotExecution).where(
                        PilotExecution.turn_id == second.turn_id,
                    )
                )
            )
        assert operation is not None and operation.status == "proposed"
        assert conversation is not None and conversation.pending_operation_id == operation_id
        assert wrong_turn is not None and wrong_turn.state == "accepted"
        assert wrong_execution == []
        assert model.calls == 1

        accepted_rejection = client.post(
            f"/api/pilot/runtime/v1/turns/{first['turn_id']}/confirm",
            json={
                "request_id": str(uuid4()),
                "conversation_id": first["conversation_id"],
                "operation_id": operation_id,
                "confirmation_token": pending["confirmation_token"],
                "approved": False,
            },
        )
        assert accepted_rejection.status_code == 200, accepted_rejection.text

        terminal_replay = client.post(
            f"/api/pilot/runtime/v1/turns/{second.turn_id}/confirm",
            json={
                "request_id": str(uuid4()),
                "conversation_id": first["conversation_id"],
                "operation_id": operation_id,
                "confirmation_token": pending["confirmation_token"],
                "approved": False,
            },
        )
        assert terminal_replay.status_code == 409, terminal_replay.text
        assert terminal_replay.json()["error_code"] == "operation_identity_conflict"
        with factory() as session:
            operation = session.get(WriteOperation, operation_id)
            wrong_execution = list(
                session.scalars(
                    select(PilotExecution).where(
                        PilotExecution.turn_id == second.turn_id,
                    )
                )
            )
        assert operation is not None and operation.status == "rejected"
        assert wrong_execution == []
        assert model.calls == 1


def test_evicted_old_turn_rebuilds_its_own_durable_result(tmp_path) -> None:
    model = _SequenceModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    factory = session_factory_for_data_dir(tmp_path)
    with factory() as session:
        first_conversation = Conversation(title="A 对话")
        session.add(first_conversation)
        session.commit()
        first_conversation_id = first_conversation.id

    with TestClient(app) as client:
        manager = app.state.runtime_manager
        manager.max_retained_runs = 1
        first = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": first_conversation_id,
                "message": "A 请求",
            },
        )
        assert first.status_code == 202, first.text
        first_payload = first.json()
        first_status = _wait_for_status(
            client,
            first_payload["turn_id"],
            {"completed"},
        )
        assert first_status["terminal"]["response"]["message"] == "A response"

        second = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": first_conversation_id,
                "message": "B 请求",
            },
        )
        assert second.status_code == 202, second.text
        second_payload = second.json()
        second_status = _wait_for_status(
            client,
            second_payload["turn_id"],
            {"completed"},
        )
        assert second_status["terminal"]["response"]["message"] == "B response"

        deadline = monotonic() + 5
        while monotonic() < deadline:
            try:
                manager.status(first_payload["turn_id"])
            except RuntimeTurnNotFound:
                break
            sleep(0.01)
        else:
            raise AssertionError("old Runtime handle was not evicted")

        status = client.get(
            f"/api/pilot/runtime/v1/turns/{first_payload['turn_id']}"
        )
        assert status.status_code == 200, status.text
        assert status.json()["terminal"]["response"]["message"] == "A response"
        snapshot = client.get(
            f"/api/pilot/runtime/v1/turns/{first_payload['turn_id']}/snapshot"
        )
        assert snapshot.status_code == 200, snapshot.text
        snapshot_body = snapshot.json()
        assert snapshot_body["terminal"]["response"]["message"] == "A response"
        assert any(
            item.get("content") == "B response"
            for item in snapshot_body["durable"].get("messages", [])
        )


def test_open_sse_stops_before_emitting_private_frames_after_source_loss() -> None:
    manager = RuntimeExecutionManager(run_workers=1, max_queue=2)
    turn_id = str(uuid4())
    request_id = str(uuid4())

    def operation(sink, control, budget):  # type: ignore[no-untyped-def]
        del control, budget
        sink.emit(AssistantMessageEvent(message="public-before-withdrawal"))
        sink.emit(AssistantMessageEvent(message="private-after-withdrawal"))
        return None

    try:
        manager.submit(
            turn_id=turn_id,
            conversation_id=1,
            generation=1,
            request_id=request_id,
            operation=operation,
        )
        assert manager.wait(turn_id, timeout=10)
        subscription = manager.subscribe(turn_id, generation=1)
        read_checks = 0

        def can_read() -> bool:
            nonlocal read_checks
            read_checks += 1
            # The manager history includes two status events before the first
            # assistant event.  The source stays valid through that public
            # event, then becomes invalid between it and the private frame.
            return read_checks < 10

        response = runtime_subscription_response(
            subscription,
            heartbeat_seconds=0.01,
            can_read=can_read,
        )

        async def collect() -> list[str]:
            return [chunk async for chunk in response.body_iterator]

        chunks = asyncio.run(collect())
        wire = "".join(chunks)
        assert "public-before-withdrawal" in wire
        assert "private-after-withdrawal" not in wire
        assert read_checks == 10
    finally:
        manager.close(wait=True)
