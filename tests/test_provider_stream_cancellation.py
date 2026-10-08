"""Real localhost HTTP transport cancellation; no paid model or credentials."""
from __future__ import annotations

import json
import select
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import AIProviderProfile, Config, save_config


@contextmanager
def streaming_provider(mode):
    entered, disconnected, release = Event(), Event(), Event()
    counters = {"requests": 0, "frames": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["stream"] is True
            counters["requests"] += 1
            entered.set()
            try:
                if mode != "delayed_headers":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.flush()
                while not release.wait(0.01):
                    if select.select([self.connection], [], [], 0)[0]:
                        if self.connection.recv(1, socket.MSG_PEEK) == b"":
                            disconnected.set()
                            return
                    if mode == "continuous":
                        chunk = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0,
                                 "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": None,
                                 "delta": {"content": "synthetic cancellation chunk"}}]}
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                        self.wfile.flush()
                        counters["frames"] += 1
                if mode == "delayed_headers":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, entered, disconnected, release, counters
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ["continuous", "silent", "delayed_headers"])
@pytest.mark.parametrize("endpoint", ["/api/chat/turns", "/api/pilot/runtime/v1/turns"])
def test_interrupt_closes_actual_provider_even_without_next_chunk(tmp_path, mode, endpoint, monkeypatch):
    # Ignore unrelated SOCKS mounts unsupported by this test environment.
    # All provider traffic below is strictly the fixture's loopback address.
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    with streaming_provider(mode) as (port, entered, disconnected, release, counters):
        config = Config(active_provider_id="synthetic", providers=[AIProviderProfile(
            id="synthetic", provider="openai_compatible", api_key="synthetic-local-key",
            base_url=f"http://127.0.0.1:{port}/v1", model="deepseek-flash",
            context_window=262144, max_output_tokens=4096,
        )])
        save_config(tmp_path, config)
        with TestClient(create_app(data_dir=tmp_path)) as client:
            try:
                submission = client.post("/api/pilot/runtime/v1/turns", json={
                    "request_id": str(uuid4()), "message": "Synthetic cancellation test. Do not use tools.",
                })
                assert submission.status_code == 202, submission.text
                turn = submission.json()
                assert entered.wait(10), "provider was not reached"
                if mode == "continuous":
                    deadline = monotonic() + 3
                    while counters["frames"] < 2 and monotonic() < deadline:
                        sleep(0.01)
                    assert counters["frames"] >= 2
                started = monotonic()
                command = {"command_id": str(uuid4()), "expected_generation": turn["execution_generation"]}
                response = client.post(f"{endpoint}/{turn['turn_id']}/interrupt", json=command)
                assert response.status_code == 200, response.text
                result = response.json().get("interrupt", response.json())
                assert result["status"] == "stopped"
                assert disconnected.wait(2), "Stop did not close the live provider HTTP connection"
                assert monotonic() - started < 3
                assert counters["requests"] == 1
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    status = client.get(f"/api/pilot/runtime/v1/turns/{turn['turn_id']}").json()
                    if status.get("worker_done"):
                        break
                    sleep(0.01)
                assert status["worker_done"] is True
                assert status["actual_worker_alive"] is False
                assert status["state"] == "stopped"
                assert client.post(f"{endpoint}/{turn['turn_id']}/interrupt", json=command).status_code == 200
                assert counters["requests"] == 1
                messages = client.get(f"/api/chat/conversations/{turn['conversation_id']}").json()
                assert all(message["role"] != "assistant" for message in messages)
            finally:
                release.set()


def test_stream_bridge_keeps_callbacks_local_and_has_no_per_chunk_delay():
    import asyncio
    from threading import get_ident
    from offerpilot.ai.provider_stream import interruptible_stream

    caller = get_ident()
    checked = []
    closed = Event()

    class Stream:
        def __init__(self):
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.index += 1
            if self.index > 200:
                raise StopAsyncIteration
            return self.index

        async def aclose(self):
            await asyncio.sleep(0)
            closed.set()

    async def create():
        return Stream()

    started = monotonic()
    assert list(interruptible_stream(create, lambda: checked.append(get_ident()))) == list(range(1, 201))
    assert monotonic() - started < 1.0, "bounded backpressure must not impose a frame pacing delay"
    assert checked and set(checked) == {caller}
    assert closed.is_set()


def test_stream_bridge_does_not_cancel_error_path_cleanup():
    import asyncio
    from offerpilot.ai.provider_stream import interruptible_stream

    closed, interrupted = Event(), Event()

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("synthetic provider failure")

        async def aclose(self):
            try:
                await asyncio.sleep(0.05)
                closed.set()
            except asyncio.CancelledError:
                interrupted.set()
                raise

    async def create():
        return Stream()

    with pytest.raises(ValueError, match="synthetic provider failure"):
        list(interruptible_stream(create, lambda: None))
    assert closed.is_set()
    assert not interrupted.is_set()


def test_stream_bridge_closes_a_backpressured_stream_when_consumer_raises():
    import asyncio
    from offerpilot.ai.provider_stream import interruptible_stream

    closed = Event()

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return "synthetic"

        async def aclose(self):
            await asyncio.sleep(0.01)
            closed.set()

    async def create():
        return Stream()

    stream = interruptible_stream(create, lambda: None)
    assert next(stream) == "synthetic"
    sleep(0.02)  # Fill the size-one queue before closing the consumer.
    stream.close()
    assert closed.is_set()


def test_stream_bridge_cancels_a_request_waiting_for_headers():
    import asyncio
    from offerpilot.ai.control import ChatRunCancelled
    from offerpilot.ai.provider_stream import interruptible_stream

    requested, released = Event(), Event()

    async def create():
        requested.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    def check():
        if requested.is_set():
            raise ChatRunCancelled("synthetic stop")

    with pytest.raises(ChatRunCancelled):
        list(interruptible_stream(create, check))
    assert released.is_set()


def test_stream_bridge_reports_unconfirmed_cleanup_as_failure(monkeypatch):
    import asyncio
    from offerpilot.ai.control import AgentLoopControlError, ChatRunCancelled
    from offerpilot.ai import provider_stream

    requested, finish_close, closed = Event(), Event(), Event()
    monkeypatch.setattr(provider_stream, "_CLOSE_TIMEOUT_SECONDS", 0.03)

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            requested.set()
            await asyncio.Future()

        async def aclose(self):
            while not finish_close.is_set():
                await asyncio.sleep(0.005)
            closed.set()

    async def create():
        return Stream()

    def check():
        if requested.is_set():
            raise ChatRunCancelled("synthetic stop")

    try:
        with pytest.raises(AgentLoopControlError, match="closure is unconfirmed"):
            list(provider_stream.interruptible_stream(create, check))
        assert not closed.is_set()
    finally:
        finish_close.set()
        assert closed.wait(1)


def test_provider_control_scope_is_nested_and_thread_local():
    from concurrent.futures import ThreadPoolExecutor
    from offerpilot.ai.provider_stream import provider_control, provider_control_scope

    def first():
        pass

    def second():
        pass
    assert provider_control() is None
    with provider_control_scope(first):
        assert provider_control() is first
        with pytest.raises(ValueError), provider_control_scope(second):
            assert provider_control() is second
            raise ValueError("synthetic")
        assert provider_control() is first
        with ThreadPoolExecutor() as pool:
            assert pool.submit(provider_control).result() is None
    assert provider_control() is None


def test_stream_bridge_cancellation_is_isolated_between_concurrent_requests():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from offerpilot.ai.control import ChatRunCancelled
    from offerpilot.ai.provider_stream import interruptible_stream

    started = [Event(), Event()]
    cancelled = [Event(), Event()]
    closed = [Event(), Event()]

    class Stream:
        def __init__(self, index):
            self.index = index

        def __aiter__(self):
            return self

        async def __anext__(self):
            started[self.index].set()
            await asyncio.Future()

        async def aclose(self):
            await asyncio.sleep(0)
            closed[self.index].set()

    def run(index):
        async def create():
            return Stream(index)

        def check():
            if cancelled[index].is_set():
                raise ChatRunCancelled(f"synthetic stop {index}")

        with pytest.raises(ChatRunCancelled):
            list(interruptible_stream(create, check))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(run, 0), pool.submit(run, 1)
        try:
            assert all(event.wait(1) for event in started)
            cancelled[0].set()
            first.result(timeout=1)
            assert closed[0].is_set()
            assert not closed[1].is_set()
            assert not second.done()
        finally:
            cancelled[1].set()
            second.result(timeout=1)
        assert closed[1].is_set()
