"""The public chat paths deliver line-format policy without repairing raw replies."""

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
        policy = "".join(
            message.content or ""
            for message in messages
            if message.role == "system"
        )
        assert "你是 OfferPilot" in policy
        assert "用户限定最终回复行数时" in policy
        assert "行间不插空行" in policy
        assert "每行使用简短句子" in policy
        assert "每个短句只保留一个核心意思" in policy
        assert "用取舍删减来缩短" in policy
        assert "附带说明必须明确指向这个成果本身" in policy
        assert "仍围绕同一份清单" in policy
        assert "形成两项可独立使用的成果" in policy
        assert "不限制用户明确要求展开的详细回答" in policy
        assert "用户明确要求的任务应先分配到可用行数" in policy
        assert "不假设用户做过相关项目" in policy
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
