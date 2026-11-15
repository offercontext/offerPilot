"""A confirmed AI date change must not clear optional fields omitted by the model."""
from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from offerpilot.ai.types import Assistant, ToolCall
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import ApplicationEvent


class EventUpdateModel:
    def __init__(self) -> None:
        self.args: dict = {}
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 2:
            return Assistant(content="面试时间已调整。")
        if self.calls > 2:
            raise AssertionError("Unexpected call beyond proposal and normal confirmation follow-up")
        return Assistant(tool_calls=[ToolCall("event-presence", "update_application_event",
                                              json.dumps(self.args))])


class TitleModel:
    def complete(self, messages, tools):
        return Assistant(content="日程调整")


def wait_for_state(client: TestClient, turn_id: str, states: set[str]) -> dict:
    deadline = time.monotonic() + 30
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/pilot/runtime/v1/turns/{turn_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["state"] in states:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"Runtime did not reach {states}: {latest}")


@pytest.mark.parametrize("transport", ["sync", "runtime"])
@pytest.mark.parametrize("change", ["omitted", "explicit_clear", "edited_clear", "concurrent_notes"])
def test_confirmed_ai_date_update_preserves_omitted_optional_fields(
    tmp_path: Path, transport: str, change: str
) -> None:
    model = EventUpdateModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model, title_model=TitleModel())) as client:
        app = client.post("/api/applications", json={"company_name": "日程公司", "position_name": "测试开发"})
        assert app.status_code == 201
        application_id = app.json()["id"]
        seeded = client.post("/api/application-events", json={
            "application_id": application_id, "event_type": "interview", "subtype": "technical",
            "scheduled_at": "2026-10-14T10:00:00+08:00", "duration_minutes": 60,
            "tags": ["onsite", "backend"], "round": 2, "location": "会议室 A",
            "notes": "保留准备材料", "remind_at": "2026-10-14T09:00:00+08:00", "status": "scheduled",
        })
        assert seeded.status_code == 201, seeded.text
        event_id = seeded.json()["id"]
        path = f"/api/application-events/{event_id}"
        before = client.get(path).json()
        model.args = {"id": event_id, "application_id": application_id, "event_type": "interview",
                      "scheduled_at": "2026-10-14T15:00:00+08:00", "duration_minutes": 60}
        clear_args = {"subtype": "", "tags": [], "round": 0, "location": "", "notes": "",
                      "remind_at": "", "status": "todo"}
        if change == "explicit_clear":
            model.args.update(clear_args)
        body = {"request_id": str(uuid4()), "conversation_id": 0,
                "context_type": "application", "context_ref": str(application_id),
                "page_context": {"view": "calendar", "label": "面试日程",
                                 "entity": {"kind": "application", "id": str(application_id),
                                            "label": "日程公司／测试开发"}},
                "message": "只把技术面的时间改到2026年10月14日15点（UTC+08:00）。"}
        if change == "explicit_clear":
            body["message"] += "同时清空子类型、标签、轮次、地点、备注与提醒，状态改为todo。"
        if transport == "runtime":
            submitted = client.post("/api/pilot/runtime/v1/turns", json=body)
            assert submitted.status_code == 202, submitted.text
            turn_id = submitted.json()["turn_id"]
            waiting = wait_for_state(client, turn_id, {"waiting_confirmation", "failed", "completed"})
            assert waiting["state"] == "waiting_confirmation", waiting
            response = waiting["terminal"]["response"]
        else:
            submitted = client.post("/api/chat", json=body)
            assert submitted.status_code == 200, submitted.text
            response = submitted.json()
        pending = response["pending_action"]
        assert pending["args"] == model.args
        assert client.get(path).json() == before  # HITL still blocks all writes before approval.
        expected = {**before, "scheduled_at": "2026-10-14T07:00:00Z"}
        if change == "concurrent_notes":
            changed = client.put(path, json={**before, "notes": "确认前独立补充的备注"})
            assert changed.status_code == 200, changed.text
            expected["notes"] = "确认前独立补充的备注"
        if change == "explicit_clear":
            expected.update(clear_args)
            expected.pop("remind_at")  # Existing API omits nullable fields when null.
        decision = {"request_id": str(uuid4()), "conversation_id": response["conversation_id"],
                    "operation_id": pending["operation_id"],
                    "confirmation_token": pending["confirmation_token"], "approved": True}
        if change == "edited_clear":
            decision["edited_args"] = {"notes": "", "remind_at": "", "round": 0}
            expected.update({"notes": "", "round": 0})
            expected.pop("remind_at")
        confirm_path = (f"/api/pilot/runtime/v1/turns/{turn_id}/confirm"
                        if transport == "runtime" else "/api/chat/confirm")
        confirmed = client.post(confirm_path, json=decision)
        assert confirmed.status_code in {200, 202}, confirmed.text
        if transport == "runtime":
            finished = wait_for_state(client, turn_id, {"completed", "failed", "result_unknown"})
            assert finished["state"] == "completed", finished
        after = client.get(path).json()
        assert after == expected
        if change in {"explicit_clear", "edited_clear"}:
            with session_factory_for_data_dir(tmp_path)() as session:
                stored = session.get(ApplicationEvent, event_id)
                assert stored is not None and stored.remind_at is None
        # Edited approval intentionally ends automatic continuation, per the current contract.
        assert model.calls == (1 if change == "edited_clear" else 2)


def test_rest_event_create_and_put_keep_their_existing_default_and_replacement_semantics(
    app_client: TestClient,
) -> None:
    application = app_client.post(
        "/api/applications", json={"company_name": "直接编辑", "position_name": "开发"}
    ).json()
    required = {"application_id": application["id"], "event_type": "interview",
                "scheduled_at": "2026-10-14T10:00:00+08:00", "duration_minutes": 60}
    rich = app_client.post("/api/application-events", json={
        **required, "subtype": "technical", "tags": ["onsite"], "round": 2,
        "location": "A", "notes": "original", "remind_at": "2026-10-14T09:00:00+08:00",
        "status": "scheduled",
    })
    assert rich.status_code == 201
    replaced = app_client.put(f"/api/application-events/{rich.json()['id']}", json=required)
    created = app_client.post("/api/application-events", json=required)
    assert replaced.status_code == 200 and created.status_code == 201
    for payload in [replaced.json(), created.json()]:
        assert payload["subtype"] == payload["notes"] == payload["location"] == ""
        assert payload["tags"] == [] and payload["round"] == 0
        assert payload["status"] == "todo" and "remind_at" not in payload
