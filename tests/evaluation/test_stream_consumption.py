import gzip
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from scripts.evaluation_subject.core_stream import observe_stream


class Stream(httpx.SyncByteStream):
    def __init__(self, parts, error=None):
        self.parts, self.error, self.closed = parts, error, False

    def __iter__(self):
        yield from self.parts
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


def observe(response, *, fail_capture=False):
    events = []

    def emit(kind, payload, ack):
        if fail_capture and payload["category"] == "usage":
            raise ValueError("CAPTURE-SECRET")
        events.append(payload)

    identity = dict(model_call_id="call", provider_attempt_id="attempt", segment_id="segment")
    observe_stream(SimpleNamespace(emit=emit), response, "http-9", identity,
                   time.monotonic(), "2026-10-29T00:00:00Z")
    return events, identity


@pytest.mark.parametrize("compressed", [False, True])
def test_fragmented_sse_yields_original_decoded_bytes_with_eof_and_done(compressed):
    raw = b'data: {"usage":{"total_tokens":12},"id":"id","model":"model"}\n\ndata: [DONE]\n\n'
    data = gzip.compress(raw) if compressed else raw
    stream = Stream([data[:9], data[9:40], data[40:]])
    response = httpx.Response(200, stream=stream,
        headers={"content-encoding": "gzip"} if compressed else {})
    events, identity = observe(response)
    assert b"".join(response.iter_bytes()) == raw
    response.close()
    terminal = [e for e in events if e["category"] == "provider_stream_terminal"]
    assert len(terminal) == 1
    record = terminal[0]
    assert record["natural_eof"] and record["sse_done_seen"] and record["usage_seen"]
    assert record["decoded_bytes"] == len(raw) and record["partial_line_bytes"] == 0
    assert record["identity"] == identity and record["attempt"] == "http-9"
    assert record["observation_complete"] and not record["raw_content_archived"]
    assert not record["consumer_early_close"]
    assert 0 <= record["first_byte_seconds"] <= record["last_byte_seconds"] <= record["elapsed_seconds"]


def test_done_then_sdk_close_does_not_invent_transport_eof_or_usage():
    response = httpx.Response(200, stream=Stream([b'data: [DONE]\n\n', b'data: trailing\n']))
    events, _ = observe(response)
    iterator = response.iter_bytes()
    assert next(iterator) == b'data: [DONE]\n\n'
    response.close()
    iterator.close()
    record = events[-1]
    assert record["terminal_reason"] == "consumer_closed"
    assert record["sse_done_seen"] and not record["natural_eof"]
    assert not record["usage_seen"] and not record["consumer_early_close"]
    assert len(events) == 1


@pytest.mark.parametrize("started", [False, True])
def test_early_close_is_distinct_from_eof(started):
    response = httpx.Response(200, stream=Stream([b'data: {"partial":', b'"secret"}\n']))
    events, _ = observe(response)
    iterator = response.iter_bytes()
    if started:
        next(iterator)
    response.close()
    iterator.close()
    record = events[-1]
    assert record["consumer_early_close"] and not record["natural_eof"]
    assert not record["sse_done_seen"] and not record["usage_seen"]
    assert record["partial_line_bytes"] == (17 if started else 0)
    assert "secret" not in json.dumps(events)


def test_read_error_after_headers_keeps_original_exception_and_partial_scope():
    error = httpx.ReadTimeout("PROVIDER-SECRET")
    error.__cause__ = OSError("CAUSE-SECRET")
    response = httpx.Response(200, stream=Stream([b'data: {"partial":'], error))
    events, _ = observe(response)
    with pytest.raises(httpx.ReadTimeout) as caught:
        list(response.iter_bytes())
    response.close()
    assert caught.value is error
    record = events[-1]
    assert record["terminal_reason"] == "read_error" and record["status"] == 200
    assert record["exception_chain_types"] == ["ReadTimeout", "OSError"]
    assert not record["natural_eof"] and not record["sse_done_seen"]
    assert not record["usage_seen"] and not record["consumer_early_close"]
    assert "SECRET" not in json.dumps(events)
    assert len(events) == 1


def test_capture_error_cannot_replace_read_error_or_claim_complete_observation():
    error = httpx.ReadError("PROVIDER-SECRET")
    response = httpx.Response(200, stream=Stream([b'data: {"usage":{"total_tokens":1}}\n'], error))
    events, _ = observe(response, fail_capture=True)
    with pytest.raises(httpx.ReadError) as caught:
        list(response.iter_bytes())
    assert caught.value is error
    record = events[-1]
    assert record["capture_errors"] == ["ValueError"] and not record["observation_complete"]
    assert record["usage_seen"] and record["exception_chain_types"] == ["ReadError"]
    response.close()


def test_oversized_observation_line_preserves_entire_response_and_records_gap():
    raw = b'data: "' + b'x' * (1024 * 1024 + 1) + b'"\n\ndata: [DONE]\n\n'
    response = httpx.Response(200, stream=Stream([raw]))
    events, _ = observe(response)
    assert b"".join(response.iter_bytes()) == raw
    record = events[-1]
    assert record["natural_eof"] and not record["observation_complete"]
    assert record["capture_errors"] == ["sse_line_capture_limit"]
    assert not record["sse_done_seen"] and not record["usage_seen"]
    response.close()


@pytest.mark.parametrize("fault", ["missing", "duplicate", "identity", "capture"])
def test_missing_or_invalid_stream_terminal_cannot_claim_complete_coverage(fault):
    from offerpilot.evaluation.core_runner import _stream_coverage
    response = dict(category="provider_response", epoch=2, attempt="http-9", wire_stream=True,
                    identity={"model_call_id": "call"})
    terminal = dict(category="provider_stream_terminal", epoch=2, attempt="http-9",
                    identity={"model_call_id": "call"}, observation_complete=True)
    rows = [response]
    if fault != "missing":
        rows.append(terminal | ({"identity": None} if fault == "identity" else
                               {"observation_complete": False} if fault == "capture" else {}))
    if fault == "duplicate":
        rows.append(terminal)
    assert not _stream_coverage(rows)["complete"]
