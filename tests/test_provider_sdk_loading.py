"""Cold startup must not load the SDK; first use keeps the raw call boundary."""

from __future__ import annotations

import asyncio
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


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancelled"])
def test_first_async_completion_preserves_payload_context_and_failure(monkeypatch, outcome):
    boundary = importlib.import_module("offerpilot.ai.client")
    authority = ContextVar("async_test_authority")
    token_value = object()
    positional = object()
    response = object()
    payload = {"stream": True, "timeout": 3.5, "num_retries": 0, "client": object()}
    error = TimeoutError("first async timeout") if outcome == "timeout" else asyncio.CancelledError()
    calls = []

    async def sdk_acompletion(*args, **kwargs):
        assert authority.get() is token_value
        calls.append((args, kwargs))
        if outcome != "success":
            raise error
        return response

    sdk = ModuleType("litellm")
    monkeypatch.setattr(sdk, "acompletion", sdk_acompletion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", sdk)
    token = authority.set(token_value)
    try:
        if outcome == "success":
            assert asyncio.run(boundary.acompletion(positional, **payload)) is response
        else:
            with pytest.raises(type(error)) as failure:
                asyncio.run(boundary.acompletion(positional, **payload))
            assert failure.value is error
    finally:
        authority.reset(token)
    assert len(calls) == 1 and calls[0][0] == (positional,)
    assert calls[0][1] == payload
    assert all(calls[0][1][key] is value for key, value in payload.items())


def test_first_async_sdk_import_failure_propagates(monkeypatch):
    boundary = importlib.import_module("offerpilot.ai.client")
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(ModuleNotFoundError) as failure:
        asyncio.run(boundary.acompletion(timeout=0.25, num_retries=0))
    assert failure.value.name == "litellm"


def test_cold_sdk_preparation_does_not_block_shared_io_or_send_after_cancel(tmp_path):
    bridge = tmp_path / "cold_sdk_cancel_probe.py"
    bridge.write_text(r'''
import importlib.abc
import importlib.util
import json
import sys
from threading import Event, Thread, get_ident
from fixed_evaluation_source_launcher import policy_proof

entered, release, cancelled = Event(), Event(), Event()
sdk_calls, import_threads, errors, io_threads = [], [], [], []
main_thread = get_ident()

class SDKLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        assert get_ident() != main_thread, "SDK initialized before a valid worker call"
        import_threads.append(get_ident())
        entered.set()
        assert release.wait(10), "test did not release cold SDK initialization"

        def dispatch(*args, **kwargs):
            sdk_calls.append((args, kwargs))
            raise AssertionError("cancelled preparation must not dispatch a provider request")

        module.completion = dispatch
        module.acompletion = dispatch

class SDKFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "litellm":
            return importlib.util.spec_from_loader(fullname, SDKLoader())

assert "litellm" not in sys.modules
sys.meta_path.insert(0, SDKFinder())
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.control import AgentLoopControlError
from offerpilot.ai.provider_stream import _io_loop, provider_control_scope
from offerpilot.ai.types import Message
from offerpilot.config import Config

loop = _io_loop()
ready = Event()
def record_io_thread():
    io_threads.append(get_ident())
    ready.set()
loop.call_soon_threadsafe(record_io_thread)
assert ready.wait(2), "shared I/O loop did not start"
client = ConfiguredAIClient(Config(api_key="synthetic-no-send-key", model="test"))

def check_active():
    if cancelled.is_set():
        raise AgentLoopControlError("cancelled during SDK preparation")

def run_worker():
    try:
        with provider_control_scope(check_active):
            client.stream_complete([Message(role="user", content="test")], [], lambda _: None)
    except BaseException as error:
        errors.append(type(error).__name__)

worker = Thread(target=run_worker, daemon=True)
worker.start()
other_io_task = Event()
try:
    assert entered.wait(10), "worker did not enter cold SDK initialization"
    loop.call_soon_threadsafe(other_io_task.set)
    assert other_io_task.wait(1), "cold SDK initialization blocked the shared I/O loop"
    cancelled.set()
finally:
    cancelled.set()
    release.set()
    worker.join(8)
assert not worker.is_alive(), "cancelled worker did not exit after SDK initialization"
assert errors == ["AgentLoopControlError"], errors
assert sdk_calls == [], "provider dispatch occurred after preparation was cancelled"
assert import_threads and import_threads[0] != io_threads[0]
assert policy_proof()["guard_active"]
print(json.dumps({"loop_responsive": True, "send_attempts": len(sdk_calls), "worker_finished": True}))
''', encoding="utf-8")
    argv = [
        sys.executable, "-I", "-B", "-X", "utf8", "-X",
        "pycache_prefix=" + str(tmp_path / "fresh-prefix"),
        str(ROOT / "scripts/evaluation_subject/source_launcher.py"),
        json.dumps({"checkout": str(ROOT), "evaluator": str(ROOT), "bridge": str(bridge)}),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "loop_responsive": True, "send_attempts": 0, "worker_finished": True,
    }


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
