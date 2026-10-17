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


@pytest.mark.parametrize("model", ["deepseek-flash", "openai/deepseek-flash", "deepseek-v4-flash", "openai/deepseek-v4-pro"])
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


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("configured,expected", [(0, 4096), (512, 512), (8192, 8192)])
def test_output_budget_is_sent_to_provider(monkeypatch, stream, configured, expected):
    captured = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        if kwargs.get("stream"):
            return [{"choices": [{"delta": {"content": "ok"}}]}]
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(providers=[AIProviderProfile(
        id="default", api_key="sk-test", context_window=32768,
        max_output_tokens=configured,
    )]))
    if stream:
        client.stream_complete([Message(role="user", content="hello")], [], lambda _: None)
    else:
        client.complete([Message(role="user", content="hello")], [])
    assert captured[0]["max_tokens"] == expected


@pytest.mark.parametrize("budget,expected", [(32, 32), (4096, 64)])
def test_connection_probe_is_bounded_and_never_falls_back(monkeypatch, budget, expected):
    captured = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        raise TimeoutError("probe failed")

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(
        active_provider_id="deepseek", fallback_provider_ids=["backup"],
        providers=[
            AIProviderProfile(id="deepseek", api_key="sk-test", model="deepseek-flash",
                              base_url="https://api.deepseek.com/v1",
                              context_window=32768, max_output_tokens=budget),
            AIProviderProfile(id="backup", api_key="sk-backup"),
        ],
    ))
    with pytest.raises(TimeoutError):
        client.test_connection()
    assert len(captured) == 1
    assert captured[0]["max_tokens"] == expected
    assert captured[0]["num_retries"] == 0
    assert captured[0]["timeout"] == 15
    assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "tools" not in captured[0]


def test_deepseek_stream_reasoning_and_tools_round_trip_without_visible_reasoning(monkeypatch):
    captured = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        if not kwargs.get("stream"):
            return {"choices": [{"message": {"content": "done"}}]}
        return [
            {"choices": [{"delta": {"reasoning_content": "looked "}}]},
            {"choices": [{"delta": {"reasoning_content": "up", "tool_calls": [{
                "index": 0, "id": "call-1", "function": {
                    "name": "list_applications", "arguments": '{"status":',
                },
            }]}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": '"offer"}'},
            }]}}]},
        ]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(providers=[AIProviderProfile(
        id="default", api_key="sk-test", model="deepseek-flash",
        base_url="https://api.deepseek.com/v1",
    )]))
    deltas = []
    assistant = client.stream_complete([Message(role="user", content="list")], [], deltas.append)
    assert deltas == []
    assert assistant.provider_blocks == {"reasoning_content": "looked up"}
    assert assistant.tool_calls == [ToolCall(
        id="call-1", name="list_applications", args='{"status":"offer"}',
    )]
    client.complete([
        Message(role="assistant", content=assistant.content, tool_calls=assistant.tool_calls,
                provider_blocks=assistant.provider_blocks),
        Message(role="tool", content="[]", tool_call_id="call-1"),
    ], [])
    assert captured[-1]["messages"][0]["reasoning_content"] == "looked up"
    assert captured[-1]["messages"][0]["tool_calls"][0]["id"] == "call-1"
    assert captured[-1]["messages"][1]["tool_call_id"] == "call-1"
    assert all("extra_body" not in call for call in captured)
