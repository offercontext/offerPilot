"""Request/context transport and raw replies, not a real-model quality verdict."""

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant, Message
from offerpilot.api import create_app
from offerpilot.confirmed_memory.repository import ConfirmedMemoryRepository, MemoryMutation
from offerpilot.db import session_factory_for_data_dir
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


class RecordingModel:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> Assistant:
        self.calls.append(list(messages))
        return Assistant(content=self.answer)

    def stream_complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], None],
    ) -> Assistant:
        reply = self.complete(messages, tools)
        middle = len(reply.content) // 2
        on_delta(reply.content[:middle])
        on_delta(reply.content[middle:])
        return reply


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("user_request", [
    "只读回答：用三行短句说明如何准备面试。",
    "Read-only: describe a weekly study plan in three short sentences, one per line.",
    "只读回答：请详细展开一份活动清单，说明这份清单的取舍依据与使用步骤。",
    "只读回答：比较报告梳理和学习计划，两项都要保留。",
], ids=["interview-zh", "study-en", "same-deliverable", "explicit-two-outcomes"])
def test_line_quota_policy_reaches_model_without_rewriting_answer(
    tmp_path: Path, endpoint: str, user_request: str
) -> None:
    # Deliberately malformed output proves that the API does not manufacture a pass.
    raw_answer = "练习介绍项目。\n\n准备问题、排查缺陷并确认时间，再补充复盘和个人介绍。"
    model = RecordingModel(raw_answer)
    app = create_app(data_dir=tmp_path, chat_model=model)
    old_preference = "偏好每次回答使用五条长段落。"
    ConfirmedMemoryRepository(session_factory_for_data_dir(tmp_path)).mutate(
        None, MemoryMutation(mutation_id=uuid4(), action="confirm", expected_version=0,
                             confirmed=True, content=old_preference)
    )
    with TestClient(app) as client:
        response = client.post(
            endpoint,
            json={"request_id": str(uuid4()), "message": user_request, "conversation_id": 0},
        )
        assert response.status_code == 200
        assert len(model.calls) == 1
        messages = model.calls[0]
        assert any(message.role == "system" and message.content for message in messages)
        user_messages = [message.content for message in messages if message.role == "user"]
        assert any(old_preference in (text or "") for text in user_messages)
        assert user_messages[-1] == user_request

        conversations = client.get("/api/chat/conversations").json()
        stored = client.get(f"/api/chat/conversations/{conversations[0]['id']}").json()
        assert [item["content"] for item in stored if item["role"] == "assistant"] == [
            raw_answer
        ]
        if endpoint == "/api/chat":
            assert response.json()["message"] == raw_answer
        else:
            frames: list[tuple[str, dict[str, Any]]] = []
            event = ""
            for line in response.text.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    frames.append((event, json.loads(line[5:])))
            assert [frame["data"]["message"] for kind, frame in frames
                    if kind == "assistant_message"] == [raw_answer]
            assert "".join(frame["data"]["delta"] for kind, frame in frames
                           if kind == "assistant_delta") == raw_answer
            assert [frame["data"]["response"]["message"] for kind, frame in frames
                    if kind == "completed"] == [raw_answer]


@pytest.mark.parametrize("endpoint", [
    "/api/chat", "/api/chat/stream", "/api/pilot/runtime/v1/turns",
])
def test_role_only_scope_and_current_request_reach_model_without_reply_repair(
    tmp_path: Path, endpoint: str
) -> None:
    from tests.test_managed_runtime_api import _wait_for_status

    # A stub witnesses the transport contract. It cannot certify model advice quality.
    answer = (
        "按测试开发常见方向，选择公开 API 练习正常、边界和异常输入，并说明断言依据。\n"
        "用自造的失败例子练习分层排查，列出每层需要的日志与验证步骤。"
    )
    model = RecordingModel(answer)
    app = create_app(data_dir=tmp_path, chat_model=model)
    application = ApplicationsRepository(session_factory_for_data_dir(tmp_path)).create(
        ApplicationCreate(company_name="合成公司 C02", position_name="测试开发")
    )
    user_request = "只读回答：给我两条实际可用的面试准备建议。"
    with TestClient(app) as client:
        response = client.post(endpoint, json=dict(
            request_id=str(uuid4()), conversation_id=0, message=user_request,
            context_type="application", context_ref=str(application.id),
        ))
        if endpoint.endswith("/turns"):
            assert response.status_code == 202, response.text
            terminal = _wait_for_status(
                client, response.json()["turn_id"], terminal={"completed", "failed"}
            )
            assert terminal["state"] == "completed"
            assert terminal["terminal"]["response"]["message"] == answer
            conversation = terminal["conversation_id"]
        else:
            assert response.status_code == 200, response.text
            if endpoint == "/api/chat":
                assert response.json()["message"] == answer
            else:
                frames = [
                    json.loads(line[5:]) for line in response.text.splitlines()
                    if line.startswith("data:")
                ]
                assert [
                    frame["data"]["response"]["message"] for frame in frames
                    if isinstance(frame.get("data", {}).get("response"), dict)
                ] == [answer]
            conversation = client.get("/api/chat/conversations").json()[0]["id"]
        assert len(model.calls) == 1
        visible = "\n".join(m.content or "" for m in model.calls[0])
        assert "合成公司 C02" in visible and "测试开发" in visible
        users = [m.content for m in model.calls[0] if m.role == "user"]
        assert users[-1] == user_request
        stored = client.get(f"/api/chat/conversations/{conversation}").json()
        assert [m["content"] for m in stored if m["role"] == "assistant"] == [answer]
