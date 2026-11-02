"""Visibility failure is unavailable, never permission to expose retained output."""

from contextlib import contextmanager
import logging
import sqlite3
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.context_projector.loader import ContextSourceLoader, SourceTemporarilyUnavailable


PRIVATE_TEXT = "synthetic-private-terminal-do-not-expose"


class Model:
    def complete(self, messages, tools):
        return Assistant(content=PRIVATE_TEXT)


@contextmanager
def completed_turn(tmp_path, source="conversation"):
    with TestClient(create_app(data_dir=tmp_path, chat_model=Model())) as client:
        application_id = None
        body = {"request_id": str(uuid4()), "message": "synthetic source visibility"}
        if source == "application":
            created = client.post(
                "/api/applications", json={"company_name": "Synthetic", "position_name": "Role"}
            )
            assert created.status_code == 201, created.text
            application_id = created.json()["id"]
            body.update(context_type="application", context_ref=str(application_id))
        submitted = client.post("/api/pilot/runtime/v1/turns", json=body)
        assert submitted.status_code == 202, submitted.text
        turn = submitted.json()
        url = f"/api/pilot/runtime/v1/turns/{turn['turn_id']}"
        deadline = monotonic() + 15
        while monotonic() < deadline:
            response = client.get(url)
            if (
                response.status_code == 503
                and response.json().get("error_code") == "runtime_source_unavailable"
            ):
                # Same original deadline; never retry absence, permission
                # failures, or unrelated service errors during setup.
                sleep(0.02)
                continue
            assert response.status_code == 200, response.text
            status = response.json()
            if status["state"] == "completed" and status["worker_done"]:
                assert status["terminal"]["response"]["message"] == PRIVATE_TEXT
                break
            sleep(0.02)
        else:
            pytest.fail("synthetic turn did not complete")
        yield client, turn, body, url, application_id


def is_visibility_read(read):
    return "_runtime_execution_readable.<locals>.read" in read.__qualname__


def assert_unavailable(response):
    assert response.status_code == 503, response.text
    assert response.json() == {
        "error": "任务来源暂时无法核验，请稍后重试。",
        "error_code": "runtime_source_unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert PRIVATE_TEXT not in response.text


def test_real_sqlite_visibility_lock_is_503_then_recovers(tmp_path, monkeypatch, caplog):
    original = ContextSourceLoader.load
    failures = []
    with completed_turn(tmp_path) as (client, turn, body, url, _):

        def locked(self, read, freeze):
            if not is_visibility_read(read):
                return original(self, read, freeze)
            # Acquired after the durable execution read, so the actual source
            # loader encounters contention, not a mocked status result.
            with sqlite3.connect(tmp_path / "data.db", isolation_level=None) as writer:
                writer.execute("BEGIN EXCLUSIVE")
                try:
                    return original(self, read, freeze)
                except SourceTemporarilyUnavailable as exc:
                    failures.append(type(exc).__name__)
                    raise
                finally:
                    writer.rollback()

        with monkeypatch.context() as scoped:
            scoped.setattr(ContextSourceLoader, "load", locked)
            with caplog.at_level(logging.WARNING, logger="offerpilot.api"):
                assert_unavailable(client.get(url))
        assert failures == ["SourceTemporarilyUnavailable"]
        assert "Runtime source visibility unavailable (SourceTemporarilyUnavailable)" in caplog.text
        assert PRIVATE_TEXT not in caplog.text
        recovered = client.get(url)
        assert recovered.status_code == 200
        assert recovered.json()["terminal"]["response"]["message"] == PRIVATE_TEXT
        with sqlite3.connect(tmp_path / "data.db") as connection:
            assert connection.execute(
                "SELECT count(*) FROM conversations WHERE id=?", (turn["conversation_id"],)
            ).fetchone() == (1,)


def test_all_runtime_visibility_entrypoints_return_precise_503(tmp_path, monkeypatch, caplog):
    original = ContextSourceLoader.load
    with completed_turn(tmp_path) as (client, turn, body, url, _):

        def unavailable(self, read, freeze):
            if is_visibility_read(read):
                error = SourceTemporarilyUnavailable()
                error.args = ("SECRET_DIAGNOSTIC_DO_NOT_LOG",)
                raise error
            return original(self, read, freeze)

        monkeypatch.setattr(ContextSourceLoader, "load", unavailable)
        with caplog.at_level(logging.WARNING, logger="offerpilot.api"):
            for suffix in ("", "/snapshot", "/events"):
                assert_unavailable(client.get(url + suffix))
            assert_unavailable(client.get(f"/api/pilot/runtime/v1/requests/{body['request_id']}"))
            assert_unavailable(client.post("/api/pilot/runtime/v1/turns", json=body))
            assert_unavailable(
                client.post(
                    url + "/confirm",
                    json={
                        "request_id": str(uuid4()),
                        "conversation_id": turn["conversation_id"],
                        "approved": True,
                    },
                )
            )
            assert_unavailable(
                client.post(
                    url + "/interrupt",
                    json={
                        "command_id": str(uuid4()),
                        "expected_generation": turn["execution_generation"],
                    },
                )
            )
        assert "SECRET_DIAGNOSTIC_DO_NOT_LOG" not in caplog.text
        assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize("source", ["conversation", "application"])
def test_verified_source_deletion_remains_404(tmp_path, source):
    with completed_turn(tmp_path, source) as (client, turn, body, url, application_id):
        target = (
            f"/api/applications/{application_id}"
            if source == "application"
            else f"/api/chat/conversations/{turn['conversation_id']}"
        )
        assert client.delete(target).status_code == 200
        for suffix in ("", "/snapshot", "/events"):
            response = client.get(url + suffix)
            assert response.status_code == 404
            assert response.json()["error_code"] == "turn_not_found"
            assert PRIVATE_TEXT not in response.text


def test_nontransient_visibility_error_still_fails_closed(tmp_path, monkeypatch, caplog):
    original = ContextSourceLoader.load
    with completed_turn(tmp_path) as (client, _, _, url, _):

        def broken(self, read, freeze):
            if is_visibility_read(read):
                raise ValueError("SECRET_INVALID_SCHEMA")
            return original(self, read, freeze)

        monkeypatch.setattr(ContextSourceLoader, "load", broken)
        with caplog.at_level(logging.WARNING, logger="offerpilot.api"):
            response = client.get(url)
        assert response.status_code == 404
        assert "runtime_source_unavailable" not in response.text
        assert "Runtime source visibility rejected (ValueError)" in caplog.text
        assert "SECRET_INVALID_SCHEMA" not in caplog.text + response.text
        assert PRIVATE_TEXT not in response.text


def test_open_sse_closes_without_retained_content_when_visibility_becomes_unavailable(
    tmp_path, monkeypatch
):
    from threading import Event

    import offerpilot.api as api

    original = ContextSourceLoader.load
    original_response = api.runtime_subscription_response
    reads = []
    admitted_reads = []
    stream_failures = []
    stream_entered = Event()
    with completed_turn(tmp_path) as (client, _, _, url, _):

        def unavailable_after_admission(self, read, freeze):
            if is_visibility_read(read):
                reads.append(True)
                if stream_entered.is_set():
                    stream_failures.append(True)
                    raise SourceTemporarilyUnavailable()
            result = original(self, read, freeze)
            if is_visibility_read(read):
                admitted_reads.append(True)
            return result

        def observe_stream_guard(subscription, **kwargs):
            can_read = kwargs["can_read"]

            def marked_can_read():
                # The original StreamingResponse invokes this after response.start.
                stream_entered.set()
                return can_read()

            kwargs["can_read"] = marked_can_read
            return original_response(subscription, **kwargs)

        monkeypatch.setattr(ContextSourceLoader, "load", unavailable_after_admission)
        monkeypatch.setattr(api, "runtime_subscription_response", observe_stream_guard)
        response = client.get(url + "/events")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert admitted_reads
        assert stream_entered.is_set()
        assert stream_failures
        assert len(reads) >= 2
        assert response.text == ""
        assert PRIVATE_TEXT not in response.text
