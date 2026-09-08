import json
from types import SimpleNamespace
from typing import Any

import pytest

from offerpilot.ai import client as ai_client
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.tool_runtime.contracts import ProviderToolContract
from offerpilot.ai.types import Message
from offerpilot.config import AIProviderProfile, Config


def _provider_tool(name: str) -> ProviderToolContract:
    parameters = {"type": "object"}
    description = name
    payload = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
    return ProviderToolContract(
        payload=payload,
        name=name,
        description=description,
        parameters=parameters,
    )


def test_client_audits_request_metadata_without_prompt_or_secret(monkeypatch, tmp_path):
    audit_path = tmp_path / "provider-request-audit.jsonl"
    monkeypatch.setenv("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", str(audit_path))

    def fake_completion(**kwargs: Any) -> Any:
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            providers=[
                AIProviderProfile(
                    id="diagnostic",
                    provider="openai_compatible",
                    api_key="sk-secret",
                    base_url="https://provider.example/v1",
                    model="model-secret",
                    supports_json_schema=True,
                )
            ],
            active_provider_id="diagnostic",
        )
    )

    client.complete(
        [Message(role="user", content="private prompt")],
        [],
        response_format={"type": "json_schema", "json_schema": {"name": "contract"}},
    )

    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    record = next(item for item in records if item["kind"] == "provider_request_metadata")
    assert record["kind"] == "provider_request_metadata"
    assert record["provider_id"] == "diagnostic"
    assert record["provider_type"] == "openai_compatible"
    assert record["endpoint"] == {"scheme": "https", "host": "provider.example", "port": 443}
    assert record["model"] == "model-secret"
    assert record["litellm_model"] == "openai/model-secret"
    assert record["response_mode"] == "json_schema"
    assert record["explicit_max_tokens"] is None
    assert record["explicit_timeout_seconds"] is None
    assert record["request_body_bytes"] > 0
    assert len(record["input_fingerprint_sha256"]) == 64
    assert len(record["schema_fingerprint_sha256"]) == 64
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "private prompt" not in audit_text
    assert "sk-secret" not in audit_text


def test_provider_request_audit_failure_does_not_block_provider_call(monkeypatch, tmp_path):
    audit_path = tmp_path / "missing" / "provider-request-audit.jsonl"
    monkeypatch.setenv("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", str(audit_path))
    calls = 0

    def fake_completion(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-test", base_url="https://provider.example/v1", model="model")
    )

    result = client.complete([Message(role="user", content="prompt")], [])

    assert result.content == "ok"
    assert calls == 1


def test_client_routes_openai_compatible_calls_through_litellm(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "update_application_status",
                                    "arguments": json.dumps({"id": 1}),
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-test", base_url="http://localhost:4000/v1", model="gpt-4o")
    )

    assistant = client.complete(
        [Message(role="user", content="hello")],
        [_provider_tool("update_application_status")],
    )

    assert captured["model"] == "openai/gpt-4o"
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "http://localhost:4000/v1"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["tools"][0]["function"]["name"] == "update_application_status"
    assert captured["tool_choice"] == "auto"
    assert assistant.content == "ok"
    assert assistant.tool_calls[0].name == "update_application_status"
    assert assistant.tool_calls[0].args == json.dumps({"id": 1})


def test_client_uses_active_provider_profile(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done"))])

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            active_provider_id="anthropic",
            providers=[
                AIProviderProfile(
                    id="openai",
                    label="OpenAI",
                    provider="openai",
                    api_key="sk-openai",
                    model="gpt-4o",
                ),
                AIProviderProfile(
                    id="anthropic",
                    label="Anthropic",
                    provider="anthropic",
                    api_key="sk-anthropic",
                    model="claude-sonnet-4",
                ),
            ],
        )
    )

    assistant = client.complete([Message(role="user", content="hello")], [])

    assert captured["model"] == "anthropic/claude-sonnet-4"
    assert captured["api_key"] == "sk-anthropic"
    assert "api_base" not in captured
    assert assistant.content == "done"


def test_client_requires_active_provider_key():
    with pytest.raises(ValueError, match="AI is not configured"):
        ConfiguredAIClient(Config(providers=[AIProviderProfile(id="default", api_key="")]))


def test_client_streams_content_deltas_through_litellm(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))]),
        ]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(api_key="sk-test", model="gpt-4o"))
    deltas: list[str] = []

    assistant = client.stream_complete([Message(role="user", content="hello")], [], deltas.append)

    assert captured["stream"] is True
    assert deltas == ["你", "好"]
    assert assistant.content == "你好"
    assert assistant.tool_calls == []


def test_streaming_provider_request_audit_failure_does_not_block_provider_call(
    monkeypatch, tmp_path
):
    audit_path = tmp_path / "missing" / "provider-request-audit.jsonl"
    monkeypatch.setenv("OFFERPILOT_PROVIDER_AUDIT_FILE", str(audit_path))
    monkeypatch.setenv("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", str(audit_path))
    calls = 0

    def fake_completion(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-test", base_url="https://provider.example/v1", model="model")
    )

    result = client.stream_complete([Message(role="user", content="prompt")], [], lambda _: None)

    assert result.content == "ok"
    assert calls == 1


def test_streaming_provider_request_metadata_audit_records_request(monkeypatch, tmp_path):
    audit_path = tmp_path / "provider-request-audit.jsonl"
    monkeypatch.setenv("OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE", str(audit_path))

    def fake_completion(**kwargs: Any) -> Any:
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-secret", base_url="https://provider.example/v1", model="model")
    )

    client.stream_complete([Message(role="user", content="private prompt")], [], lambda _: None)

    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metadata = next(item for item in records if item["kind"] == "provider_request_metadata")
    assert metadata["response_mode"] == "text_json"
    assert len(metadata["input_fingerprint_sha256"]) == 64
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "private prompt" not in audit_text
    assert "sk-secret" not in audit_text


def test_client_streams_tool_calls_through_litellm(monkeypatch):
    def fake_completion(**kwargs: Any) -> Any:
        return [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "list_applications",
                                        "arguments": '{"status"',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": ':"offer"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        ]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(Config(api_key="sk-test", model="gpt-4o"))
    deltas: list[str] = []

    assistant = client.stream_complete(
        [Message(role="user", content="hello")],
        [_provider_tool("list_applications")],
        deltas.append,
    )

    assert deltas == []
    assert assistant.content == ""
    assert assistant.tool_calls[0].id == "call_1"
    assert assistant.tool_calls[0].name == "list_applications"
    assert assistant.tool_calls[0].args == '{"status":"offer"}'


def test_client_does_not_fallback_after_streaming_visible_delta(monkeypatch):
    calls: list[str] = []

    def fake_completion(**kwargs: Any) -> Any:
        calls.append(str(kwargs["api_key"]))
        if kwargs["api_key"] == "sk-primary":
            def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="半句"))])
                raise RuntimeError("stream failed")

            return chunks()
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="backup"))])]

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            active_provider_id="primary",
            fallback_provider_ids=["backup"],
            providers=[
                AIProviderProfile(id="primary", api_key="sk-primary", model="gpt-4o"),
                AIProviderProfile(id="backup", api_key="sk-backup", model="gpt-4o-mini"),
            ],
        )
    )
    deltas: list[str] = []

    with pytest.raises(RuntimeError, match="stream failed"):
        client.stream_complete([Message(role="user", content="hello")], [], deltas.append)

    assert deltas == ["半句"]
    assert calls == ["sk-primary"]


def test_client_falls_back_to_configured_provider_after_primary_failure(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs["api_key"] == "sk-primary":
            raise RuntimeError("primary provider unavailable")
        return {"choices": [{"message": {"content": "fallback ok"}}]}

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(
            active_provider_id="primary",
            fallback_provider_ids=["backup"],
            providers=[
                AIProviderProfile(
                    id="primary",
                    label="Primary",
                    provider="openai",
                    api_key="sk-primary",
                    model="gpt-4o",
                ),
                AIProviderProfile(
                    id="backup",
                    label="Backup",
                    provider="openrouter",
                    api_key="sk-backup",
                    model="openai/gpt-4o-mini",
                ),
            ],
        )
    )

    assistant = client.complete([Message(role="user", content="hello")], [])

    assert assistant.content == "fallback ok"
    assert [call["api_key"] for call in calls] == ["sk-primary", "sk-backup"]
    assert calls[1]["model"] == "openai/gpt-4o-mini"


def test_client_emits_redacted_provider_failure_diagnostic(monkeypatch):
    events: list[tuple[str, str]] = []

    def fake_completion(**kwargs: Any) -> Any:
        raise TimeoutError("secret prompt and API key must not be logged")

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-test", base_url="https://provider.example/v1", model="gpt-test"),
        on_provider_event=lambda level, message: events.append((level, message)),
    )

    with pytest.raises(ai_client.ProviderCallError) as caught:
        client.complete([Message(role="user", content="sensitive input")], [])

    assert caught.value.diagnostic["failure_category"] == "network_timeout"
    assert caught.value.diagnostic["http_status"] is None
    assert isinstance(caught.value.diagnostic["elapsed_ms"], int)
    assert caught.value.diagnostic["correlation_id"]
    assert len(events) == 1
    level, message = events[0]
    assert level == "WARNING"
    assert message.startswith("ai_provider_failure ")
    assert "secret prompt" not in message
    assert "sensitive input" not in message
    assert "sk-test" not in message


@pytest.mark.parametrize(
    ("error_factory", "expected_category", "expected_status"),
    [
        (lambda: type("Provider503Error", (RuntimeError,), {"status_code": 503})(), "provider_http_5xx", 503),
        (lambda: type("ProxyError", (RuntimeError,), {})(), "proxy_failure", None),
        (lambda: type("ConnectError", (RuntimeError,), {})(), "proxy_failure", None),
        (lambda: type("RemoteProtocolError", (RuntimeError,), {})(), "response_lost", None),
    ],
)
def test_client_classifies_provider_boundary_failures(
    monkeypatch, error_factory, expected_category, expected_status
):
    events: list[tuple[str, str]] = []

    def fake_completion(**kwargs: Any) -> Any:
        raise error_factory()

    monkeypatch.setattr(ai_client, "completion", fake_completion)
    client = ConfiguredAIClient(
        Config(api_key="sk-test", base_url="https://provider.example/v1", model="gpt-test"),
        on_provider_event=lambda level, message: events.append((level, message)),
    )

    with pytest.raises(ai_client.ProviderCallError):
        client.complete([Message(role="user", content="sensitive input")], [])

    diagnostic = json.loads(events[0][1].removeprefix("ai_provider_failure "))
    assert diagnostic["failure_category"] == expected_category
    assert diagnostic["http_status"] == expected_status
    assert diagnostic["timeout"] is False
    assert diagnostic["elapsed_ms"] >= 0
    assert diagnostic["correlation_id"]
