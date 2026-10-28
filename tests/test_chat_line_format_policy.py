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
def test_line_quota_policy_reaches_model_without_rewriting_answer(
    tmp_path: Path, endpoint: str
) -> None:
    request = "只读回答：用三行短句说明如何准备面试。"
    # Deliberately malformed output proves that the API does not manufacture a pass.
    raw_answer = "练习介绍项目。\n\n准备问题。"
    model = RecordingModel(raw_answer)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        response = client.post(
            endpoint,
            json={"request_id": str(uuid4()), "message": request, "conversation_id": 0},
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
        assert [message.content for message in messages if message.role == "user"] == [request]

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
