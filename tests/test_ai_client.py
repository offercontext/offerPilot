from typing import Any

import pytest

from offerpilot.ai import client as ai_client
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.tool_runtime.contracts import ProviderToolContract
from offerpilot.ai.types import Message, ToolCall
from offerpilot.config import AIProviderProfile, Config


def test_legacy_anthropic_config_routes_through_litellm(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "needs confirmation"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="anthropic-key", base_url="https://api.anthropic.com", model="claude-test")
    )

    assistant = client.complete([Message(role="user", content="change status")], [])

    assert captured["model"] == "anthropic/claude-test"
    assert captured["api_key"] == "anthropic-key"
    assert "api_base" not in captured
    assert assistant.content == "needs confirmation"


def test_reasoning_content_round_trips_for_thinking_models(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "looked up the application",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "list_applications",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(api_key="sk-test", model="deepseek-reasoner"))

    assistant = client.complete(
        [
            Message(
                role="assistant",
                content="",
                provider_blocks={"reasoning_content": "previous thinking"},
                tool_calls=[ToolCall(id="previous", name="list_applications", args="{}")],
            )
        ],
        [],
    )

    assert captured["messages"][0]["reasoning_content"] == "previous thinking"
    assert assistant.provider_blocks == {"reasoning_content": "looked up the application"}


def test_client_preserves_provider_tool_contract_envelope(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(api_key="sk-test"))
    tool = {
        "type": "function",
        "function": {
            "name": "submit_analysis",
            "description": "Submit analysis.",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    contract = ProviderToolContract(
        payload=tool,
        name="submit_analysis",
        description="Submit analysis.",
        parameters={"type": "object", "properties": {}},
    )

    client.complete([Message(role="user", content="analyse")], [contract])

    assert captured["tools"] == [tool]


def test_client_passes_response_format_only_to_explicitly_capable_provider(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            providers=[
                AIProviderProfile(
                    id="capable",
                    api_key="sk-test",
                    supports_json_schema=True,
                )
            ],
            active_provider_id="capable",
        )
    )

    client.complete(
        [Message(role="user", content="return JSON")],
        [],
        response_format={"type": "json_schema", "json_schema": {"name": "review"}},
    )

    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "review"},
    }


def test_client_omits_response_format_for_provider_without_explicit_capability(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            providers=[
                AIProviderProfile(
                    id="unconfigured",
                    api_key="sk-test",
                    supports_json_schema=False,
                )
            ],
            active_provider_id="unconfigured",
        )
    )

    client.complete(
        [Message(role="user", content="return JSON")],
        [],
        response_format={"type": "json_schema", "json_schema": {"name": "review"}},
    )

    assert "response_format" not in captured


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "openai/deepseek-v4-pro"])
def test_readonly_deepseek_v4_draft_disables_thinking_but_chat_is_unchanged(monkeypatch, model):
    captured: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"choices": [{"message": {"content": "draft"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(
        providers=[AIProviderProfile(
            id="deepseek", provider="openai_compatible", api_key="sk-test",
            base_url="https://api.deepseek.com/v1", model=model,
        )],
        active_provider_id="deepseek",
    ))

    client.complete_readonly_draft([Message(role="user", content="prepare")], timeout_seconds=5)
    assert captured[-1]["extra_body"] == {"thinking": {"type": "disabled"}}

    client.complete([Message(role="user", content="chat")], [])
    assert "extra_body" not in captured[-1]


@pytest.mark.parametrize(("base_url", "model"), [
    ("https://provider.example/v1", "deepseek-v4-flash"),
    ("https://api.deepseek.com/v1", "gpt-4o"),
])
def test_readonly_draft_does_not_disable_thinking_for_other_provider_or_model(monkeypatch, base_url, model):
    captured: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"choices": [{"message": {"content": "draft"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(
        providers=[AIProviderProfile(
            id="provider", provider="openai_compatible", api_key="sk-test",
            base_url=base_url, model=model,
        )],
        active_provider_id="provider",
    ))

    client.complete_readonly_draft([Message(role="user", content="prepare")], timeout_seconds=5)
    assert "extra_body" not in captured[-1]
