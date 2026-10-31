"""Async request admission, task attribution and cancellable observation; no paid calls."""
from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Lock, Thread
from types import SimpleNamespace

import httpx
import pytest

from offerpilot.ai.control import AgentLoopControlError
from offerpilot.ai.provider_stream import _io_loop, interruptible_stream
from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
from offerpilot.context_projector.projector import ModelSurfaceProjector
from scripts.evaluation_subject.core_transport import EXECUTION_IDENTITY, SEND_PERMISSION, observe


class Channel:
    def __init__(self, path):
        self.config = dict(data_dir=str(path), trial_key=dict(execution_item_id="E01.runtime_read"))
        self.events = []
        self.seq = self.attempts = 0
        self.lock = Lock()
        self.entered, self.release = Event(), Event()
        self.release.set()
        self.deny = False
        self.response_entered, self.response_release = Event(), Event()
        self.response_release.set()
        self.block_response = False

    def emit(self, kind, payload, ack=False):
        with self.lock:
            self.seq += 1
            self.events.append(dict(type=kind, **payload))
        if self.block_response and payload.get("category") == "provider_response":
            self.response_entered.set()
            assert self.response_release.wait(5), "test did not release response observation"

    def attempt(self, purpose, **metadata):
        self.emit("call_attempt", dict(purpose=purpose, **metadata))

    def provider_attempt(self, **metadata):
        self.entered.set()
        assert self.release.wait(5), "test did not release observation ACK"
        if self.deny:
            raise PermissionError("synthetic budget refused")
        with self.lock:
            self.seq += 1
            self.attempts += 1
            return self.seq


def harness(tmp_path, monkeypatch, handler, *, wire_stream=False, early=False,
            endpoint="https://provider.test/v1", transport=None):
    channel = Channel(tmp_path)
    state = dict(overrides={}, url=endpoint + "/chat/completions", cancel=Event())
    client = httpx.AsyncClient(transport=transport if transport is not None else
                              httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(ModelSurfaceProjector, "project", lambda self, request: request.surface)

    def invoke(self, candidate, surface, *args, **kwargs):
        content = json.dumps([dict(id=surface.model_call_id, version=1,
                                  content="source-" + surface.model_call_id)])
        payload = dict(model="test", max_tokens=64, temperature=0,
                       thinking=dict(type="disabled"), messages=[dict(role="user", content=content)])
        if wire_stream:
            payload.update(stream=True, stream_options=dict(include_usage=True))
        payload.update(state["overrides"])

        async def create():
            request = client.build_request("POST", state["url"], json=payload)
            response = await client.send(request, stream=wire_stream)

            async def chunks():
                try:
                    if wire_stream:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    else:
                        yield response.content
                finally:
                    await response.aclose()

            return chunks()

        def check():
            if state["cancel"].is_set():
                raise AgentLoopControlError("synthetic cancellation")

        stream = interruptible_stream(create, check)
        if early:
            first = next(stream)
            stream.close()
            return [first]
        return list(stream)

    monkeypatch.setattr(SingleCandidateAgentTransport, "complete_one",
                        SingleCandidateAgentTransport.complete_one)
    monkeypatch.setattr(SingleCandidateAgentTransport, "stream_one", invoke)
    io = observe(channel, endpoint,
                 dict(model="test", max_input_bytes=10000, max_output_tokens=64), async_client=client)

    def bound(label="A"):
        EXECUTION_IDENTITY.value = dict(request_id=label)
        surface = SimpleNamespace(model_call_id=label, runtime_surface_fingerprint="surface-" + label)
        source = SimpleNamespace(kind="confirmed_memory", revision_identity="source-" + label,
            content_revision_fingerprint="version-1", canonical_content=json.dumps([
                dict(id=label, version=1, content="source-" + label)]).encode())
        ModelSurfaceProjector().project(SimpleNamespace(surface=surface,
            optional_sources=SimpleNamespace(sources=[source], contributors=[]),
            provider_surface_build_identity=SimpleNamespace(segment_id="segment-" + label)))
        try:
            return SingleCandidateAgentTransport(None, None).stream_one(None, surface,
                invocation_identity=SimpleNamespace(segment_id="segment-" + label,
                                                   surface_fingerprint="surface-" + label),
                provider_attempt_id="attempt-" + label, candidate_ordinal=1)
        finally:
            EXECUTION_IDENTITY.value = None

    def close():
        channel.release.set()
        channel.response_release.set()
        asyncio.run_coroutine_threadsafe(client.aclose(), _io_loop()).result(5)
        io.close()

    return SimpleNamespace(channel=channel, state=state, client=client, bound=bound, close=close)


def test_async_fixture_undo_restores_all_product_methods(tmp_path):
    targets = [(SingleCandidateAgentTransport, "complete_one"),
               (SingleCandidateAgentTransport, "stream_one"),
               (ModelSurfaceProjector, "project"), (httpx.Client, "send")]
    originals = [getattr(owner, name) for owner, name in targets]
    patch = pytest.MonkeyPatch()
    try:
        h = harness(tmp_path, patch, lambda request: httpx.Response(200, json={}))
        h.close()
    finally:
        patch.undo()
    for (owner, name), original in zip(targets, originals):
        assert getattr(owner, name) is original, name


def test_async_final_body_source_and_permission_are_isolated_between_tasks(tmp_path, monkeypatch):
    received, late, tasks = [], [], []
    both = asyncio.Event()
    finish = asyncio.Event()

    async def handler(request):
        payload = json.loads(request.content)
        label = json.loads(payload["messages"][0]["content"])[0]["id"]
        permission = SEND_PERMISSION.attempt
        received.append(label)
        if len(received) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        assert SEND_PERMISSION.permitted and SEND_PERMISSION.attempt == permission

        async def inherited():
            await finish.wait()
            assert SEND_PERMISSION.permitted is False
            with pytest.raises(PermissionError):
                await h.client.post("https://provider.test/v1/chat/completions", json=payload)
            late.append(label)

        tasks.append(asyncio.create_task(inherited()))
        return httpx.Response(200, json=dict(model="test", usage=dict(total_tokens=7)))

    h = harness(tmp_path, monkeypatch, handler)
    errors = []

    def worker(label):
        try:
            h.bound(label)
        except BaseException as error:
            errors.append(error)

    workers = [Thread(target=worker, args=(label,)) for label in ("A", "B")]
    try:
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join(5)
        assert not any(t.is_alive() for t in workers) and not errors
        _io_loop().call_soon_threadsafe(finish.set)

        async def drain():
            await asyncio.gather(*tasks)

        asyncio.run_coroutine_threadsafe(drain(), _io_loop()).result(5)
        assert sorted(received) == sorted(late) == ["A", "B"]
        assert h.channel.attempts == 2
        inputs = [e for e in h.channel.events if e.get("category") == "final_input"]
        assert len(inputs) == 2
        for row in inputs:
            label = row["identity"]["model_call_id"]
            assert row["identity"]["execution"]["request_id"] == label
            assert [source["source_id"] for source in row["observations"]] == ["source-" + label]
    finally:
        h.close()


@pytest.mark.parametrize("mutation", ["endpoint", "output", "temperature", "budget"])
def test_async_denial_precedes_physical_send(tmp_path, monkeypatch, mutation):
    received = []

    async def handler(request):
        received.append(request)
        return httpx.Response(200, json={})

    h = harness(tmp_path, monkeypatch, handler)
    if mutation == "endpoint":
        h.state["url"] = "https://other.test/v1/chat/completions"
    elif mutation == "output":
        h.state["overrides"]["max_tokens"] = 65
    elif mutation == "temperature":
        h.state["overrides"]["temperature"] = 1
    else:
        h.channel.deny = True
    try:
        with pytest.raises((PermissionError, ValueError)):
            h.bound()
        assert received == [] and h.channel.attempts == 0
    finally:
        h.close()


def test_cancel_during_observation_ack_keeps_io_responsive_and_never_sends(tmp_path, monkeypatch):
    received, errors = [], []

    async def handler(request):
        received.append(request)
        return httpx.Response(200, json={})

    h = harness(tmp_path, monkeypatch, handler)
    h.channel.release.clear()

    def worker():
        try:
            h.bound()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=worker)
    thread.start()
    try:
        assert h.channel.entered.wait(3)
        responsive = Event()
        _io_loop().call_soon_threadsafe(responsive.set)
        assert responsive.wait(1)
        h.state["cancel"].set()
        thread.join(2)
        assert not thread.is_alive() and len(errors) == 1
        assert isinstance(errors[0], AgentLoopControlError) and received == []
    finally:
        h.channel.release.set()
        thread.join(5)
        h.close()
    assert received == []


def test_async_sse_preserves_bytes_usage_and_actual_eof(tmp_path, monkeypatch):
    expected = ('data: {"id":"synthetic","model":"test","usage":{"total_tokens":9}}\n\n'
                'data: [DONE]\n\n').encode()
    closed = Event()

    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(expected), 3):
                yield expected[offset:offset + 3]

        async def aclose(self):
            closed.set()

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes())

    h = harness(tmp_path, monkeypatch, handler, wire_stream=True)
    try:
        assert b"".join(h.bound()) == expected
    finally:
        h.close()
    terminal = [e for e in h.channel.events if e.get("category") == "provider_stream_terminal"]
    assert closed.is_set() and len(terminal) == 1
    assert terminal[0]["natural_eof"] and terminal[0]["usage_seen"] and terminal[0]["sse_done_seen"]
    assert terminal[0]["raw_content_archived"] is False


def test_cancel_after_headers_closes_unreturned_actual_socket(tmp_path, monkeypatch):
    disconnected, received = Event(), []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(1)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            self.wfile.flush()
            self.connection.settimeout(5)
            try:
                if self.connection.recv(1) == b"":
                    disconnected.set()
            except ConnectionResetError:
                disconnected.set()
            except socket.timeout:
                pass
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    h = harness(tmp_path, monkeypatch, None, wire_stream=True,
                endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                transport=httpx.AsyncHTTPTransport(retries=0))
    h.channel.block_response = True
    h.channel.response_release.clear()
    errors = []

    def worker():
        try:
            h.bound()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=worker)
    thread.start()
    try:
        assert h.channel.response_entered.wait(3)
        h.state["cancel"].set()
        thread.join(2)
        assert not thread.is_alive() and len(errors) == 1
        assert isinstance(errors[0], AgentLoopControlError)
        assert disconnected.wait(1) and received == [1]
    finally:
        h.channel.response_release.set()
        thread.join(5)
        h.close()
        server.shutdown()
        server.server_close()
        serving.join(2)


def _hostname_guard_probe(path):
    from scripts.evaluation_subject.core_bootstrap import fence

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(1)
            body = b'{"model":"test","usage":{"total_tokens":1}}'
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    patch = pytest.MonkeyPatch()
    endpoint = f"http://provider.test:{server.server_port}/v1"
    h = harness(path, patch, None, endpoint=endpoint,
                transport=httpx.AsyncHTTPTransport(retries=0))
    original_resolve = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        assert host == "provider.test"
        assert SEND_PERMISSION.permitted
        return original_resolve("127.0.0.1", port, *args, **kwargs)

    socket.getaddrinfo = resolve
    fence(h.channel, set(), endpoint)
    seen = []
    resolver = asyncio.BaseEventLoop.getaddrinfo

    async def track(self, host, port, **kwargs):
        seen.append(isinstance(host, bytes))
        return await resolver(self, host, port, **kwargs)

    asyncio.BaseEventLoop.getaddrinfo = track
    try:
        h.bound()
        assert received == [1] and h.channel.attempts == 1 and any(seen)

        async def unpermitted():
            with pytest.raises(PermissionError):
                await asyncio.open_connection("127.0.0.1", server.server_port)

        asyncio.run_coroutine_threadsafe(unpermitted(), _io_loop()).result(5)
        assert received == [1]
    finally:
        h.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        patch.undo()


def test_async_hostname_bytes_dns_permission_and_unmetered_socket_denial(tmp_path):
    # Use a file-backed __main__, like the real subject, for cold dependency imports.
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m",
                             "tests.evaluation.test_async_transport", str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "hostname-scope-closed" in result.stdout


if __name__ == "__main__":
    _hostname_guard_probe(Path(sys.argv[1]))
    sys.__stdout__.write("hostname-scope-closed\n")
    sys.__stdout__.flush()
