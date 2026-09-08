from __future__ import annotations

import json

import pytest

from offerpilot.ai.interview_knowledge_capture import (
    SAFE_EMPTY_PREVIEW,
    InterviewKnowledgePreviewError,
    InterviewKnowledgeProviderError,
    generate_interview_knowledge_preview,
    validate_interview_knowledge_preview,
)
from offerpilot.ai.types import Assistant
from offerpilot.knowledge.interview_capture import CanonicalFragment


FRAGMENTS = [
    CanonicalFragment("fragment_001", "/questions", 0, 6, "设计一个缓存"),
    CanonicalFragment("fragment_002", "/self_reflection", 0, 8, "我解释了淘汰策略"),
]


class FakeModel:
    supports_json_schema = False

    def __init__(self, responses, error: Exception | None = None):
        self.responses = list(responses)
        self.error = error
        self.calls = 0
        self.response_formats = []
        self.messages = []

    def complete(self, messages, tools, response_format=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.messages.append(messages)
        self.response_formats.append(response_format)
        if self.error is not None:
            raise self.error
        response = self.responses.pop(0)
        return Assistant(content=response if isinstance(response, str) else json.dumps(response, ensure_ascii=False))


def valid_preview() -> dict[str, object]:
    return {
        "title": "缓存设计复盘",
        "blocks": [
            {
                "block_id": "block_001",
                "text": "我解释了淘汰策略",
                "evidence_refs": [
                    {"fragment_id": "fragment_002", "excerpt": "我解释了淘汰策略"}
                ],
            }
        ],
    }


def test_invalid_first_preview_is_repaired_once_with_machine_failure_category() -> None:
    model = FakeModel([{"title": "bad", "blocks": [{"text": "缺 id"}]}, valid_preview()])
    result = generate_interview_knowledge_preview(model, FRAGMENTS)
    assert result == valid_preview()
    assert model.calls == 2
    assert "unexpected_field" in model.messages[1][1].content
    assert "设计一个缓存" in model.messages[1][1].content


def test_two_invalid_outputs_return_strict_safe_empty_without_model_text() -> None:
    model = FakeModel([{"title": "secret", "blocks": [{"text": "secret"}]}, {"unexpected": "raw"}])
    result = generate_interview_knowledge_preview(model, FRAGMENTS)
    assert result == SAFE_EMPTY_PREVIEW
    assert model.calls == 2


def test_provider_failure_is_not_retried() -> None:
    model = FakeModel([], error=TimeoutError("candidate secret"))
    with pytest.raises(InterviewKnowledgeProviderError):
        generate_interview_knowledge_preview(model, FRAGMENTS)
    assert model.calls == 1


def test_unknown_fragment_reference_is_rejected() -> None:
    payload = valid_preview()
    payload["blocks"][0]["evidence_refs"][0]["fragment_id"] = "fragment_999"  # type: ignore[index]
    with pytest.raises(InterviewKnowledgePreviewError, match="unknown_evidence_ref"):
        validate_interview_knowledge_preview(payload, FRAGMENTS)


def test_json_schema_capability_is_passed_only_when_explicitly_supported() -> None:
    model = FakeModel([valid_preview()])
    model.supports_json_schema = True
    generate_interview_knowledge_preview(model, FRAGMENTS)
    assert model.response_formats[0]["type"] == "json_schema"
