from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.db import init_database, session_factory_for_data_dir
from offerpilot.models import PilotExecution, PilotTurnRecord
from offerpilot.pilot_control import PilotControlRepository
from offerpilot.pilot_runtime.turn_control import TurnControlRegistry
from offerpilot.pilot_timeline import PilotTimelineRepository


class _CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        return Assistant(content="已收到。")


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_legacy_pending_keeps_turn_and_execution_states_consistent(tmp_path, endpoint):
    model = _CountingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        application = client.post("/api/applications", json={
            "company_name": "状态验证", "position_name": "工程师",
        }).json()
        response = client.post(endpoint, json={
            "message": "保存 JD：职位：后端工程师", "conversation_id": 0,
            "context_type": "application", "context_ref": str(application["id"]),
        })
        assert response.status_code == 200, response.text
        assert client.get("/api/chat/conversations").json()[0]["pending_action"] is not None
        factory = session_factory_for_data_dir(tmp_path)
        with factory() as session:
            execution = session.scalar(select(PilotExecution))
            assert execution is not None and execution.state == "waiting_confirmation"
            turn = session.get(PilotTurnRecord, execution.turn_id)
            assert turn is not None and turn.state == "started"
    assert model.calls == 0


def _wait_for_terminal(client: TestClient, turn_id: str) -> dict:
    deadline = monotonic() + 30
    latest: dict = {}
    while monotonic() < deadline:
        response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get("state") in {"completed", "failed", "result_unknown"}:
            return latest
        sleep(0.05)
    raise AssertionError(f"runtime did not become terminal: {latest}")


def test_runtime_post_after_registry_close_leaves_no_running_execution(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    original_create = TurnControlRegistry.create

    def close_before_create(self, lease=None):  # type: ignore[no-untyped-def]
        self.close()
        return original_create(self, lease)

    monkeypatch.setattr(TurnControlRegistry, "create", close_before_create)
    model = _CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    with TestClient(app) as client:
        response = client.post(
            "/api/pilot/runtime/v1/turns",
            json={
                "request_id": str(uuid4()),
                "conversation_id": 0,
                "message": "关闭后仍尝试接纳",
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["error_code"] == "runtime_admission_failed"

    factory = session_factory_for_data_dir(tmp_path)
    with factory() as session:
        executions = list(session.scalars(select(PilotExecution)))
        turns = list(session.scalars(select(PilotTurnRecord)))
    assert len(executions) == 1
    assert executions[0].state == "failed"
    assert len(turns) == 1
    assert turns[0].state == "failed"
    assert model.calls == 0


def test_same_runtime_post_replays_identity_when_queue_capacity_is_reserved(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    model = _CountingModel()
    app = create_app(data_dir=tmp_path, chat_model=model)
    with TestClient(app) as client:
        body = {
            "request_id": str(uuid4()),
            "conversation_id": 0,
            "message": "相同请求并发重试",
        }
        first = client.post("/api/pilot/runtime/v1/turns", json=body)
        assert first.status_code == 202, first.text
        original = first.json()
        _wait_for_terminal(client, original["turn_id"])

        manager = app.state.runtime_manager
        manager.max_queue = 1
        deadline = monotonic() + 5
        while manager.queued_count and monotonic() < deadline:
            sleep(0.01)
        assert manager.queued_count == 0

        # Occupy the only admission slot.  A duplicate request must be
        # resolved from the durable request identity before trying to reserve
        # this slot, even when two retries arrive together.
        with manager.reserve_admission():
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.post(
                            "/api/pilot/runtime/v1/turns", json=body
                        ),
                        range(2),
                    )
                )
        assert [response.status_code for response in responses] == [200, 200]
        for response in responses:
            payload = response.json()
            assert payload["replayed"] is True
            assert payload["turn_id"] == original["turn_id"]
            assert payload["conversation_id"] == original["conversation_id"]

    factory = session_factory_for_data_dir(tmp_path)
    with factory() as session:
        executions = list(session.scalars(select(PilotExecution)))
        turns = list(session.scalars(select(PilotTurnRecord)))
    assert len(executions) == 1
    assert len(turns) == 1
    assert model.calls == 1


def test_runtime_epoch_reconcile_fences_only_old_runtime_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = init_database(tmp_path / "runtime-epoch.db")
    now = [1_000_000]
    controls = PilotControlRepository(factory, now_ms=lambda: now[0])
    turns = PilotTimelineRepository(factory)

    old = turns.admit(str(uuid4()), {"message": "旧进程", "conversation_id": 0})
    old_lease = controls.claim_start(
        old.turn_id,
        protocol="pilot-runtime-v1",
        runtime_epoch="old-epoch",
        submission_key="old-submission",
        submission_request_id=str(uuid4()),
    )
    current = turns.admit(str(uuid4()), {"message": "新进程", "conversation_id": 0})
    current_lease = controls.claim_start(
        current.turn_id,
        protocol="pilot-runtime-v1",
        runtime_epoch="current-epoch",
        submission_key="current-submission",
        submission_request_id=str(uuid4()),
    )
    legacy = turns.admit(str(uuid4()), {"message": "旧协议", "conversation_id": 0})
    legacy_lease = controls.claim_start(legacy.turn_id)

    assert controls.reconcile_runtime_epoch("current-epoch") == 1
    assert controls.get_runtime_execution(old.turn_id)["state"] == "result_unknown"
    assert controls.get_runtime_execution(current.turn_id)["state"] == "running"
    assert controls.get_execution(legacy.turn_id)["state"] == "running"

    with factory() as session:
        old_turn = session.get(PilotTurnRecord, old.turn_id)
        current_turn = session.get(PilotTurnRecord, current.turn_id)
        legacy_turn = session.get(PilotTurnRecord, legacy.turn_id)
    assert old_turn is not None and old_turn.state == "incomplete"
    assert current_turn is not None and current_turn.state == "started"
    assert legacy_turn is not None and legacy_turn.state == "started"

    # Keep the leases referenced so this test also verifies that reconciliation
    # fences durable ownership rather than merely dropping local objects.
    assert old_lease.generation == current_lease.generation == legacy_lease.generation == 1
