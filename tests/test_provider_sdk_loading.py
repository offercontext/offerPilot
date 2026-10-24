"""Cold startup must not load the SDK; first use keeps the raw call boundary."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType

import pytest


BOUNDARIES = ("offerpilot.ai.client", "offerpilot.knowledge.provider")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module_name", (*BOUNDARIES, "offerpilot.api"))
def test_cold_product_import_does_not_load_provider_sdk(tmp_path, module_name):
    bridge = tmp_path / "import_probe.py"
    bridge.write_text(
        """import importlib, importlib.abc, json, sys
from fixed_evaluation_source_launcher import policy_proof
sdk_roots = {'litellm', 'openai', 'anthropic'}
assert not sdk_roots.intersection(sys.modules)
class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in sdk_roots:
            raise AssertionError('provider SDK loaded during product import: ' + fullname)
sys.meta_path.insert(0, NoSDK())
importlib.import_module(MODULE_NAME)
if MODULE_NAME == 'offerpilot.ai.client':
    from offerpilot.ai.client import ConfiguredAIClient
    from offerpilot.config import Config
    client = ConfiguredAIClient(Config(api_key='synthetic-no-send-key'))
    try:
        client.complete_readonly_draft([], timeout_seconds=0)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid request budget was accepted')
assert not any(name.split('.')[0] in sdk_roots for name in sys.modules)
assert policy_proof()['guard_active']
print(json.dumps({'module': MODULE_NAME, 'sdk_loaded': False}))
""".replace("MODULE_NAME", repr(module_name)),
        encoding="utf-8",
    )
    argv = [
        sys.executable, "-I", "-B", "-X", "utf8", "-X",
        "pycache_prefix=" + str(tmp_path / "fresh-prefix"),
        str(ROOT / "scripts/evaluation_subject/source_launcher.py"),
        json.dumps({"checkout": str(ROOT), "evaluator": str(ROOT), "bridge": str(bridge)}),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"module": module_name, "sdk_loaded": False}
    assert not (tmp_path / "fresh-prefix").exists()


@pytest.mark.parametrize("module_name", BOUNDARIES)
def test_first_completion_forwards_payload_result_and_context(monkeypatch, module_name):
    boundary = importlib.import_module(module_name)
    authority = ContextVar("test_authority")
    token_value = object()
    payload = {"messages": [{"role": "user", "content": "test"}], "stream": False,
               "timeout": 3.5, "num_retries": 0, "client": object()}
    positional = object()
    response = object()
    calls = []

    def sdk_completion(*args, **kwargs):
        assert authority.get() is token_value
        calls.append((args, kwargs))
        return response

    sdk = ModuleType("litellm")
    monkeypatch.setattr(sdk, "completion", sdk_completion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", sdk)
    token = authority.set(token_value)
    try:
        assert boundary.completion(positional, **payload) is response
    finally:
        authority.reset(token)
    assert len(calls) == 1
    assert calls[0][0] == (positional,)
    assert calls[0][1] == payload
    assert all(calls[0][1][key] is value for key, value in payload.items())


@pytest.mark.parametrize("module_name", BOUNDARIES)
def test_first_stream_is_not_consumed_and_preserves_iteration_error(monkeypatch, module_name):
    boundary = importlib.import_module(module_name)
    error = RuntimeError("synthetic stream failure")
    consumed = []
    chunk = object()

    def chunks():
        consumed.append(True)
        yield chunk
        raise error

    stream = chunks()
    sdk = ModuleType("litellm")
    monkeypatch.setattr(sdk, "completion", lambda **kwargs: stream, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", sdk)
    result = boundary.completion(stream=True)
    assert result is stream and not consumed
    assert next(result) is chunk
    with pytest.raises(RuntimeError) as failure:
        next(result)
    assert failure.value is error


@pytest.mark.parametrize("module_name", BOUNDARIES)
def test_first_completion_error_is_not_retried_or_replaced(monkeypatch, module_name):
    boundary = importlib.import_module(module_name)
    error = TimeoutError("synthetic first-call timeout")
    attempts = []

    def sdk_completion(**kwargs):
        attempts.append(kwargs)
        raise error

    sdk = ModuleType("litellm")
    monkeypatch.setattr(sdk, "completion", sdk_completion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", sdk)
    with pytest.raises(TimeoutError) as failure:
        boundary.completion(timeout=0.25, num_retries=0)
    assert failure.value is error
    assert attempts == [{"timeout": 0.25, "num_retries": 0}]


@pytest.mark.parametrize("module_name", BOUNDARIES)
def test_first_sdk_import_failure_propagates(monkeypatch, module_name):
    boundary = importlib.import_module(module_name)
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(ModuleNotFoundError) as failure:
        boundary.completion(timeout=0.25, num_retries=0)
    assert failure.value.name == "litellm"


def test_configured_and_knowledge_clients_use_deferred_sdk(monkeypatch):
    from offerpilot.ai.client import ConfiguredAIClient
    from offerpilot.ai.types import Message
    from offerpilot.config import Config
    from offerpilot.knowledge.provider import build_knowledge_brief_provider_client

    calls = []

    def sdk_completion(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([{"choices": [{"delta": {"content": "ok"}}]}])
        return {"choices": [{"message": {"content": "ok"}}]}

    sdk = ModuleType("litellm")
    monkeypatch.setattr(sdk, "completion", sdk_completion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", sdk)
    client = ConfiguredAIClient(Config(api_key="synthetic-no-send-key", model="test"))
    message = Message(role="user", content="test")
    assert client.complete_readonly_draft([message], timeout_seconds=3.5).content == "ok"
    assert calls[0]["timeout"] == 3.5 and calls[0]["num_retries"] == 0
    deltas = []
    assert client.stream_complete([message], [], deltas.append).content == "ok"
    assert deltas == ["ok"]
    assert build_knowledge_brief_provider_client().complete_once(timeout=4, num_retries=0) == {
        "choices": [{"message": {"content": "ok"}}],
    }
    assert len(calls) == 3
    assert calls[2] == {"timeout": 4, "num_retries": 0}
