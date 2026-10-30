"""A fixed Runtime worker must not retain the first request's causal identity."""

import httpx
import threading

from offerpilot import smoke
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.pilot_runtime.contracts import MessageOutcome
from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager
from scripts.real_ai_control.archive import EvidenceArchive
from scripts.real_ai_control.bootstrap import Isolation
from scripts.real_ai_control.transport import API_REQUEST


def test_two_turns_on_one_real_runtime_worker_keep_distinct_request_binding(tmp_path, monkeypatch):
    for owner, name in (
        (threading.Thread, "start"),
        (smoke, "create_app"),
        (smoke, "_running_server"),
        (smoke, "tempfile"),
        (smoke, "_cleanup_real_ai_smoke_records"),
        (smoke, "_run_unconfigured_chat_smoke"),
        (smoke, "_run_named_real_ai_smoke_stage"),
        (ConfiguredAIClient, "_complete_with_provider"),
        (ConfiguredAIClient, "_stream_with_provider"),
        (httpx.Client, "__init__"),
        (httpx.Client, "send"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    isolation = Isolation(tmp_path / "work", archive)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)), trust_env=False
    )
    manager = RuntimeExecutionManager(run_workers=1)
    observed = []
    try:
        isolation.install(provider_client=client)
        for number in (111, 222):
            token = API_REQUEST.set(number)
            try:

                def operation(*args):
                    observed.append(API_REQUEST.get())
                    return MessageOutcome("offline", conversation_id=1)

                manager.submit(turn_id=f"turn-{number}", conversation_id=1, operation=operation)
                assert manager.wait(f"turn-{number}", timeout=2)
            finally:
                API_REQUEST.reset(token)
        assert observed == [111, 222]
    finally:
        manager.close(wait=True)
        if hasattr(isolation, "restore_runtime_context"):
            isolation.restore_runtime_context()
        for listener in isolation.listeners:
            listener.close()
        for directory in isolation.directories:
            directory.cleanup()
        client.close()


def test_rejected_submission_does_not_bind_later_accepted_turn_to_old_api(tmp_path):
    import pytest
    from scripts.real_ai_control.context import install_runtime_association

    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    restore = install_runtime_association(archive)
    manager = RuntimeExecutionManager(run_workers=1)
    seen = []
    try:
        first = API_REQUEST.set(111)
        try:
            with pytest.raises(TypeError):
                manager.submit(turn_id="retry-admission", conversation_id=1, operation=None)
        finally:
            API_REQUEST.reset(first)
        second = API_REQUEST.set(222)
        try:

            def operation(*args):
                seen.append(API_REQUEST.get())
                return MessageOutcome("offline", conversation_id=1)

            manager.submit(turn_id="retry-admission", conversation_id=1, operation=operation)
            assert manager.wait("retry-admission", timeout=2)
        finally:
            API_REQUEST.reset(second)
        assert seen == [222]
    finally:
        manager.close(wait=True)
        restore()
