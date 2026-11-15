"""Private OS pipes, concurrent SDK calls and streaming lifetime; MockTransport only."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import threading
import time

import httpx
from litellm import completion
from openai import OpenAI
import pytest

from scripts.real_ai_control.archive import EvidenceArchive
from scripts.real_ai_control.channel import Binding, ParentBroker, PipeClient
from scripts.real_ai_control.journal import ReservationJournal
from scripts.real_ai_control.transport import ControlledTransport, completion_wrapper
from scripts.real_ai_control.wire import WirePolicy

KEY = "sk-offline-channel-test"
BINDING = Binding("offline-pipe-test", *(["1" * 64] * 5))


@contextmanager
def pipes(root, *, before_allow=lambda: None, timeout=1, limit=64):
    c_read, p_write = os.pipe()
    p_read, c_write = os.pipe()
    reader, writer = os.fdopen(c_read, "rb", buffering=0), os.fdopen(c_write, "wb", buffering=0)
    parent_reader, parent_writer = (
        os.fdopen(p_read, "rb", buffering=0),
        os.fdopen(p_write, "wb", buffering=0),
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    policy = WirePolicy(64000, 8192, "2", "8", expires)
    errors = []
    with ReservationJournal(
        root / "reservations",
        plan_sha256=BINDING.plan_sha256,
        max_requests=limit,
        max_cny="12.39",
        expires_at=expires,
    ) as journal:
        broker = ParentBroker(
            parent_reader,
            parent_writer,
            binding=BINDING,
            policy=policy,
            key=KEY,
            journal=journal,
            before_allow=before_allow,
        )

        def serve():
            try:
                broker.serve()
            except BaseException as error:
                errors.append(type(error).__name__)
            finally:
                parent_writer.close()
                parent_reader.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = PipeClient(
            reader, writer, binding=BINDING, policy=policy, key=KEY, ack_timeout=timeout
        )
        try:
            yield client, journal, broker, errors
        finally:
            if not writer.closed:
                writer.close()
            thread.join(timeout=2)
            assert not thread.is_alive()
            client.thread.join(timeout=2)
            assert not client.thread.is_alive()
            reader.close()


def body(message="offline"):
    return dict(
        model="deepseek-flash",
        messages=[dict(role="user", content=message)],
        max_tokens=1024,
        temperature=0,
        thinking={"type": "disabled"},
    )


def request(message="offline"):
    return httpx.Request(
        "POST",
        "https://api.deepseek.com/v1/chat/completions",
        json=body(message),
        headers={"Authorization": "Bearer " + KEY},
    )


def test_parallel_waiters_have_unique_bound_durable_permissions(tmp_path):
    with pipes(tmp_path) as (client, journal, broker, errors):

        def run(number):
            req = request(str(number))
            permit = client.permission(req, "completion-" + format(number, "032x"))
            estimate = client.policy.validate(req, expected_key=KEY, now=datetime.now(timezone.utc))
            assert permit.consume(estimate.request_sha256) == permit.row_hash
            with pytest.raises(PermissionError):
                permit.consume(estimate.request_sha256)
            return permit.row_hash

        with ThreadPoolExecutor(max_workers=4) as pool:
            hashes = list(pool.map(run, range(1, 9)))
        assert len(set(hashes)) == 8 and journal.requests == 8
        assert journal.verify()["allowed_requests"] == 8
        client.finish("a" * 64, native_exit=1)
    assert broker.complete is True and errors == []


def test_pipe_eof_and_parent_death_do_not_allow_a_cached_permission(tmp_path):
    with pipes(tmp_path) as (client, journal, broker, errors):
        permit = client.permission(request(), "completion-" + "1" * 32)
        client.writer.close()
        client.thread.join(timeout=2)
        assert client.dead.is_set()
        with pytest.raises(PermissionError):
            permit.consume(permit.request_sha256)
        assert journal.requests == 1  # ACK without dispatch remains charged.
    assert broker.complete is False


def test_ack_deadline_does_not_resend_or_discard_unknown_reservation(tmp_path):
    def slow():
        time.sleep(0.15)

    with pipes(tmp_path, before_allow=slow, timeout=0.03) as (client, journal, broker, errors):
        with pytest.raises(TimeoutError):
            client.permission(request(), "completion-" + "1" * 32)
        with pytest.raises(PermissionError):
            client.permission(request(), "completion-" + "2" * 32)
        time.sleep(0.2)
        assert journal.requests == 1 and client.dead.is_set()


@pytest.mark.parametrize("kind", ["ok", "role", "timeout", "parse"])
def test_real_sdk_with_private_pipe_never_physically_repeats_a_failed_call(tmp_path, kind):
    sends = []

    def respond(req):
        sends.append(req.content)
        if kind == "timeout":
            raise httpx.ReadTimeout("offline transport timeout", request=req)
        if kind == "role":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Last message must have role `user`",
                        "type": "invalid_request_error",
                    }
                },
            )
        if kind == "parse":
            return httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )
        return httpx.Response(
            200,
            json={
                "id": "offline-id",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "offline reply"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with pipes(tmp_path, timeout=5) as (channel, journal, broker, errors):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
        controlled = ControlledTransport(httpx.MockTransport(respond), channel, archive)
        http = httpx.Client(
            # Keep the in-memory boundary explicit to the offline network guard.
            transport=httpx.MockTransport(controlled.handle_request),
            trust_env=False,
        )
        sdk = OpenAI(
            api_key=KEY, base_url="https://api.deepseek.com/v1", max_retries=0, http_client=http
        )
        call = completion_wrapper(completion, archive, sdk)
        try:
            if kind == "ok":
                result = call(
                    model="openai/deepseek-flash",
                    messages=[dict(role="user", content="offline")],
                    api_key=KEY,
                    api_base="https://api.deepseek.com/v1",
                )
                assert result.choices[0].message.content == "offline reply"
            else:
                with pytest.raises(Exception):
                    call(
                        model="openai/deepseek-flash",
                        messages=[dict(role="user", content="offline")],
                        api_key=KEY,
                        api_base="https://api.deepseek.com/v1",
                    )
            assert len(sends) == 1 and journal.requests == 1
        finally:
            sdk.close()
            http.close()
            controlled.close()
            assert controlled.closed is True
        channel.finish(archive.summary()["events_sha256"], native_exit=1 if kind != "ok" else 0)


def test_lazy_stream_close_before_first_iteration_closes_original_and_retains_incomplete(tmp_path):
    class Lazy:
        closed = False

        def __iter__(self):
            yield {"choices": []}

        def close(self):
            self.closed = True

    original = Lazy()
    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    from types import SimpleNamespace

    client = SimpleNamespace(api_key=KEY, base_url="https://api.deepseek.com/v1", max_retries=0)
    stream = completion_wrapper(lambda **kw: original, archive, client)(
        stream=True,
        api_key=KEY,
        api_base="https://api.deepseek.com/v1",
        model="openai/deepseek-flash",
    )
    stream.close()
    assert original.closed is True
    assert archive.summary()["complete"] is False


@pytest.mark.parametrize("mode", ["complete", "done_marker", "close", "timeout"])
def test_actual_sdk_streaming_scope_retains_prefix_and_never_sends_twice(tmp_path, mode):
    sends = []
    actual_eof = {"seen": False}

    class Source(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"id":"s1","object":"chat.completion.chunk","created":1,"model":"deepseek-flash","choices":[{"index":0,"delta":{"role":"assistant","content":"first"},"finish_reason":null}]}\n\n'
            if mode == "timeout":
                raise httpx.ReadTimeout("offline half-stream disconnect")
            reason = "null" if mode == "done_marker" else '"stop"'
            yield (
                'data: {"id":"s1","object":"chat.completion.chunk","created":1,"model":"deepseek-flash","choices":[{"index":0,"delta":{"content":"second"},"finish_reason":'
                + reason
                + "}]}\n\n"
            ).encode()
            yield b"data: [DONE]\n\n"
            actual_eof["seen"] = True

        def close(self):
            pass

    def respond(request):
        sends.append(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Source())

    with pipes(tmp_path, timeout=5) as (channel, journal, broker, errors):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
        controlled = ControlledTransport(httpx.MockTransport(respond), channel, archive)
        http = httpx.Client(
            transport=httpx.MockTransport(controlled.handle_request),
            trust_env=False,
        )
        sdk = OpenAI(
            api_key=KEY, base_url="https://api.deepseek.com/v1", max_retries=0, http_client=http
        )
        call = completion_wrapper(completion, archive, sdk)
        stream = call(
            model="openai/deepseek-flash",
            api_base="https://api.deepseek.com/v1",
            api_key=KEY,
            messages=[dict(role="user", content="offline")],
            stream=True,
        )
        try:
            if mode in {"complete", "done_marker"}:
                chunks = list(stream)
                assert (
                    "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
                    == "firstsecond"
                )
            elif mode == "timeout":
                with pytest.raises(Exception):
                    list(stream)
            else:
                next(stream)
                stream.close()
            assert len(sends) == 1 and journal.requests == 1
            assert archive.summary()["active_bodies"] == 0
            sdk_end = [r for r in archive.events if r["kind"] == "sdk-stream-end"][-1]
            assert sdk_end["sdk_semantic_complete"] is (mode in {"complete", "done_marker"})
            assert sdk_end["raw_eof_proven"] is actual_eof["seen"]
            assert archive.summary()["complete"] is actual_eof["seen"]
            assert any(row["kind"] == "sdk-parsed-chunk" for row in archive.events)
        finally:
            stream.close()
            sdk.close()
            http.close()
            controlled.close()
            assert controlled.closed is True
        channel.finish(
            archive.summary()["events_sha256"], native_exit=0 if mode == "complete" else 1
        )


def test_parent_fsync_or_policy_failure_never_produces_a_send_permission(tmp_path):
    def fail():
        raise OSError("offline parent durability failure")

    with pipes(tmp_path, before_allow=fail) as (client, journal, broker, errors):
        with pytest.raises(EOFError):
            client.permission(request(), "completion-" + "1" * 32)
        assert journal.requests == 0 and client.dead.is_set()
    assert errors == ["OSError"] and broker.complete is False


def test_a_shorter_product_timeout_or_output_limit_is_never_relaxed(tmp_path):
    from types import SimpleNamespace

    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    sdk = SimpleNamespace(api_key=KEY, base_url="https://api.deepseek.com/v1", max_retries=0)
    observed = []

    def original(**kwargs):
        observed.append(kwargs)
        return {"choices": []}

    completion_wrapper(original, archive, sdk)(
        model="openai/deepseek-flash",
        api_base="https://api.deepseek.com/v1",
        api_key=KEY,
        timeout=3,
        max_tokens=17,
    )
    assert observed[0]["timeout"] == 3 and observed[0]["max_tokens"] == 17


def test_ack_latency_cannot_extend_the_original_logical_budget_or_send_after_expiry(tmp_path):
    from scripts.real_ai_control.transport import LogicalCall, logical_scope

    sends = []
    with pipes(tmp_path, before_allow=lambda: time.sleep(0.05)) as (
        channel,
        journal,
        broker,
        errors,
    ):
        archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
        transport = ControlledTransport(
            httpx.MockTransport(lambda r: sends.append(r)), channel, archive
        )
        scope = LogicalCall(deadline=time.monotonic() + 0.01)
        req = request()
        req.extensions["timeout"] = {k: 1 for k in ("read", "write", "connect", "pool")}
        with logical_scope(scope), pytest.raises(TimeoutError):
            transport.handle_request(req)
        assert sends == [] and journal.requests == 1
        assert not any(row["kind"] == "provider-dispatch" for row in archive.events)
        channel.finish(archive.summary()["events_sha256"], native_exit=1)


def test_valid_budget_denial_stops_sending_but_keeps_terminal_control_usable(tmp_path):
    from scripts.real_ai_control.protocol import SendPermissionDenied

    with pipes(tmp_path, limit=1) as (channel, journal, broker, errors):
        prior = channel.permission(request(), "completion-" + "1" * 32)
        with pytest.raises(SendPermissionDenied):
            channel.permission(request(), "completion-" + "2" * 32)
        assert not channel.dead.is_set()
        with pytest.raises(PermissionError):
            prior.consume(prior.request_sha256)
        with pytest.raises(PermissionError):
            channel.permission(request(), "completion-" + "3" * 32)
        assert journal.verify()["rows"] == 2 and journal.requests == 1
        channel.finish("a" * 64, native_exit=1)
    assert broker.complete is True and errors == []


def test_concurrent_requests_already_in_flight_at_denial_fail_closed_with_no_extra_allowance(
    tmp_path,
):
    gate = threading.Event()
    with pipes(tmp_path, limit=1, timeout=5, before_allow=lambda: gate.wait(2)) as (
        channel,
        journal,
        broker,
        errors,
    ):

        def reserve(index):
            try:
                return channel.permission(
                    request(str(index)), "completion-" + format(index, "032x")
                )
            except Exception as error:
                return type(error).__name__

        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(reserve, n) for n in range(1, 5)]
            until = time.monotonic() + 1
            while channel.seq < 4 and time.monotonic() < until:
                time.sleep(0.001)
            assert channel.seq == 4
            gate.set()
            results = [future.result(timeout=3) for future in pending]
        assert journal.requests == 1 and journal.verify()["rows"] == 2
        assert channel.dead.wait(1) and broker.complete is False
        assert errors == ["InvalidControlFrame"]
        assert any(isinstance(result, str) for result in results)


def test_capture_hook_exception_closes_response_at_logical_failure_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts.real_ai_control.channel import Permit
    from scripts.real_ai_control.transport import LogicalCall, logical_scope
    import scripts.real_ai_control.transport as module
    from scripts.real_ai_control.archive import ArchiveIncomplete

    closed = []

    class Source(httpx.SyncByteStream):
        def __iter__(self):
            yield b"body"

        def close(self):
            closed.append("response")

    policy = WirePolicy(64000, 8192, "2", "8", datetime.now(timezone.utc) + timedelta(hours=1))

    def permission(req, logical):
        value = policy.validate(req, expected_key=KEY, now=datetime.now(timezone.utc))
        return Permit(
            value.request_sha256, "a" * 64, live=lambda: True, expires_at=time.monotonic() + 5
        )

    channel = SimpleNamespace(policy=policy, key=KEY, permission=permission)
    archive = EvidenceArchive(tmp_path / "archive", secrets=(KEY,))
    transport = ControlledTransport(
        httpx.MockTransport(lambda r: httpx.Response(200, stream=Source())), channel, archive
    )

    def fail(*args, **kwargs):
        raise ArchiveIncomplete("offline capture hook failure")

    monkeypatch.setattr(module, "captured_response", fail)
    req = request()
    req.extensions["timeout"] = {name: 1 for name in ("read", "write", "connect", "pool")}
    with logical_scope(LogicalCall()), pytest.raises(ArchiveIncomplete):
        transport.handle_request(req)
    assert closed == ["response"]


def test_transport_close_attempts_every_response_and_inner_after_first_failure(tmp_path):
    calls = []

    class Source(httpx.SyncByteStream):
        def __init__(self, name):
            self.name = name

        def __iter__(self):
            yield b""

        def close(self):
            calls.append(self.name)
            if self.name == "first":
                raise OSError("offline first close failure")

    class Inner(httpx.MockTransport):
        def close(self):
            calls.append("inner")

    archive = EvidenceArchive(tmp_path / "archive", secrets=())
    transport = ControlledTransport(Inner(lambda r: httpx.Response(200)), None, archive)
    transport.responses = [
        httpx.Response(200, stream=Source("first")),
        httpx.Response(200, stream=Source("second")),
    ]
    with pytest.raises(OSError):
        transport.close()
    assert calls == ["first", "second", "inner"]
    assert archive.summary()["complete"] is False
