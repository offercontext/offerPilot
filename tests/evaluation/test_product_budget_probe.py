import time
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.config import Config, save_config


class CountingModel:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, tools, response_format=None):
        self.calls += 1
        return Assistant(content=self.text)


@pytest.mark.parametrize("title_cap", [0, 1])
def test_actual_http_title_budget_is_reserved_before_enabled_title_model(tmp_path, monkeypatch, title_cap):
    save_config(
        tmp_path,
        Config(
            auth_enabled=True,
            auth_token="synthetic-test-token",
            chat_auto_approve_writes=False,
            providers=[],
            fallback_provider_ids=[],
            skills=[],
        ),
    )
    chat, title = CountingModel("仅查询，已完成。"), CountingModel("合成预算标题")
    app = create_app(data_dir=tmp_path, chat_model=chat, title_model=title)
    manager = app.state.runtime_manager
    manager.default_max_model_calls = 2
    manager.default_max_title_model_calls = title_cap
    title_entered, release_title = Event(), Event()
    original_title = manager._run_title_if_allowed

    def pause_before_title(handle):
        title_entered.set()
        if not release_title.wait(30):
            raise TimeoutError("test did not release title work")
        return original_title(handle)

    monkeypatch.setattr(manager, "_run_title_if_allowed", pause_before_title)
    with TestClient(app, headers={"Authorization": "Bearer synthetic-test-token"}) as client:
        response = client.post(
            "/api/pilot/runtime/v1/turns",
            json=dict(
                conversation_id=0,
                context_type="workspace",
                context_ref="",
                request_id=str(uuid4()),
                message="只读回答，不执行任何写入。",
            ),
        )
        assert response.status_code in (200, 202), response.text
        turn = response.json()["turn_id"]
        try:
            assert title_entered.wait(30), response.text
            early = client.get(f"/api/pilot/runtime/v1/turns/{turn}").json()
            # Durable operation completion can precede optional title work.
            # This snapshot is valid, but its budget is not the final worker budget.
            assert early["state"] == "completed" and early["worker_done"] is False, early
            assert early["budget"]["model_calls"] == 1 and title.calls == 0, early
        finally:
            release_title.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = client.get(f"/api/pilot/runtime/v1/turns/{turn}").json()
            if state["state"] in {"completed", "failed", "result_unknown"} and state["worker_done"]:
                break
            time.sleep(0.01)
        assert state["state"] == "completed", state
        assert state["worker_done"] is True, state
        assert title.calls == title_cap and chat.calls == 1
        assert state["budget"]["model_calls"] == 1 + title_cap
        assert state["budget"]["title_model_calls"] == title_cap
