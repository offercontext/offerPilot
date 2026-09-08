"""A separate process recovers committed edited writes without repeating work."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from offerpilot.ai.types import ToolCall
from offerpilot.ai.write_operations import WriteOperationCoordinator
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import ChatMessage, WriteOperation
from offerpilot.pilot_runtime.persistence import ChatPersistenceCoordinator
from tests.pilot_runtime.test_confirmation_cutover import _CutoverModel, _application, _propose


class _NoProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("recovery must not call a Provider")


def _recover_in_process(data_dir: str, request: dict[str, Any], pipe: Any) -> None:
    executor_calls = 0

    def deny_execution(*args: object, **kwargs: object) -> object:
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError("recovery must not execute the write again")

    try:
        model = _NoProvider()
        with (
            patch.object(WriteOperationCoordinator, "execute_primary", deny_execution),
            TestClient(create_app(data_dir=Path(data_dir), chat_model=model)) as client,
        ):
            results = []
            for endpoint in ("/api/chat/confirm/stream", "/api/chat/confirm", "/api/chat/confirm"):
                response = client.post(endpoint, json=request)
                results.append((response.status_code, response.text))
            pipe.send({"results": results, "provider_calls": model.calls, "executor_calls": executor_calls})
    except BaseException as exc:
        pipe.send({"error": repr(exc)})
    finally:
        pipe.close()


@pytest.mark.parametrize("boundary", ["before_persist", "after_persist"])
def test_restart_recovers_receipt_at_delivery_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    origin = ToolCall("receipt-restart", "update_application_status", '{"id":1,"status":"interview"}')
    model = _CutoverModel(origin)
    original_persist = ChatPersistenceCoordinator.persist_confirmation_delivery
    intercepted_receipts: list[str] = []

    def interrupt(self: ChatPersistenceCoordinator, **kwargs: Any) -> object:
        continuation = kwargs.get("continuation", ())
        intercepted_receipts.extend(
            message.content for message in continuation
            if message.role == "assistant" and "自动续答已结束" in message.content
        )
        if boundary == "after_persist":
            original_persist(self, **kwargs)
        raise RuntimeError("injected response loss at receipt delivery")

    with TestClient(create_app(data_dir=tmp_path, chat_model=model), raise_server_exceptions=False) as client:
        _application(client, "重启回执虚构公司")
        pending = _propose(client)
        request = {
            "conversation_id": pending["conversation_id"],
            "operation_id": pending["pending_action"]["operation_id"],
            "confirmation_token": pending["pending_action"]["confirmation_token"],
            "approved": True,
            "edited_args": {"status": "offer"},
        }
        with monkeypatch.context() as scoped:
            scoped.setattr(ChatPersistenceCoordinator, "persist_confirmation_delivery", interrupt)
            interrupted_response = client.post("/api/chat/confirm", json=request)
        if boundary == "before_persist":
            assert interrupted_response.status_code in {409, 500, 502, 503}
            assert "自动续答已结束" not in interrupted_response.text
        assert intercepted_receipts, "the interruption must happen after a receipt was constructed"
        assert len(model.calls) == 1

    sessions = session_factory_for_data_dir(tmp_path)
    with sessions() as session:
        operation = session.get(WriteOperation, request["operation_id"])
        assert operation is not None and operation.status == "committed"
        terminal_digest = operation.terminal_payload_sha256
        strategy_fingerprint = operation.confirmation_strategy_fingerprint
        original_messages = list(session.scalars(select(ChatMessage).where(
            ChatMessage.operation_id == operation.id,
        )))
        if boundary == "before_persist":
            assert not original_messages, "tool and receipt must remain atomic"
            session.execute(update(WriteOperation).where(WriteOperation.id == operation.id).values(
                delivery_lease_expires_at=0,
            ))
        else:
            assert sum(message.role == "assistant" for message in original_messages) == 1
        session.commit()

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_recover_in_process, args=(str(tmp_path), request, sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(300), "receipt recovery process did not finish"
        result = receiver.recv()
        process.join(10)
        assert process.exitcode == 0
        assert "error" not in result, result
        assert result["provider_calls"] == 0
        assert result["executor_calls"] == 0
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(10)
    responses = result["results"]
    assert all(status == 200 for status, _ in responses), responses
    assert all("自动续答已结束" in body for _, body in responses)
    assert json.loads(responses[1][1])["message"] == json.loads(responses[2][1])["message"]
    with sessions() as session:
        operation = session.get(WriteOperation, request["operation_id"])
        assert operation is not None
        assert operation.status == "committed"
        assert operation.terminal_payload_sha256 == terminal_digest
        assert operation.confirmation_strategy_fingerprint == strategy_fingerprint
        messages = list(session.scalars(select(ChatMessage).where(
            ChatMessage.operation_id == operation.id,
        )))
        assert sorted(message.role for message in messages) == ["assistant", "tool"]
        receipt = next(message.content for message in messages if message.role == "assistant")
        assert "offer" in receipt
        assert json.loads(responses[1][1])["message"] == receipt
