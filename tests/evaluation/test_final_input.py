import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
from offerpilot.context_sources import loader
from scripts.evaluation_subject.core_transport import observe, SEND_PERMISSION


def test_final_wire_body_links_unicode_memory_units_without_opaque_copy(tmp_path, monkeypatch):
    text = '偏好"短句"；保留中文。'
    source = SimpleNamespace(
        kind="confirmed_memory",
        revision_identity="memory-revision",
        content_revision_fingerprint="version-fingerprint",
        canonical_content=json.dumps(
            [dict(id="memory-1", version=2, preference=text)], ensure_ascii=False
        ).encode(),
    )
    monkeypatch.setattr(
        loader, "load_optional_sources", lambda: SimpleNamespace(sources=[source], contributors=[])
    )
    for name in ["complete_one", "stream_one"]:
        monkeypatch.setattr(
            SingleCandidateAgentTransport, name, getattr(SingleCandidateAgentTransport, name)
        )
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)

    class Channel:
        seq = 0
        config = {
            "data_dir": str(tmp_path),
            "trial_key": {"execution_item_id": "E09.memory_override"},
        }
        events = []

        def emit(self, kind, payload, ack=False):
            self.seq += 1
            self.events.append(payload | {"type": kind})

        def attempt(self, purpose, **metadata):
            self.emit("call_attempt", dict(purpose=purpose, **metadata))

        def provider_attempt(self, **metadata):
            self.attempt("provider_send", **metadata)
            assigned = self.seq
            # A concurrent budget event may advance the shared counter after ACK.
            self.seq += 1
            return assigned

    channel = Channel()
    observe(
        channel,
        "https://provider.test/v1",
        dict(model="test", max_input_bytes=10000, max_output_tokens=64),
    )
    loader.load_optional_sources()

    def response(request):
        assert SEND_PERMISSION.permitted
        return httpx.Response(200, json={"model": "actual-test", "usage": {"total_tokens": 12}})

    payload = dict(
        model="test",
        max_tokens=64,
        temperature=0,
        thinking=dict(type="disabled"),
        messages=[dict(role="user", content=text + "\n" + text)],
    )
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        client.post("https://provider.test/v1/chat/completions", json=payload)
        for mutation in (
            {"n": 2},
            {"max_completion_tokens": 100000},
            {"max_tokens": None},
            {"temperature": 1},
            {"temperature": True},
            {"n": True},
            {"thinking": {"type": "enabled"}},
            {"stream_options": {"include_usage": True}},
            {"stream": True, "stream_options": {"include_usage": False}},
            {"stream": True, "stream_options": {"include_usage": True, "unknown": True}},
            {"extra_unbounded_field": True},
        ):
            with pytest.raises(ValueError):
                client.post("https://provider.test/v1/chat/completions", json=payload | mutation)
        with pytest.raises(PermissionError):
            client.post("https://other.test/v1/chat/completions", json=payload)
    assert not SEND_PERMISSION.permitted
    record = next(e for e in channel.events if e.get("category") == "final_input")
    assert record["attempt"] == "http-2"
    assert next(e for e in channel.events if e.get("category") == "usage")["attempt"] == "http-2"
    raw = (tmp_path / record["request_file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["payload_hash"]
    field = json.loads(raw)["messages"][0]["content"].encode()
    ranges = record["observations"][0]["ranges"]
    assert len(ranges) == 2
    assert all(field[r["start"] : r["end"]].decode() == text for r in ranges)
    assert all(r["unit_identity"] == {"id": "memory-1", "version": 2} for r in ranges)
    assert all(r["coordinate_space"] == "decoded-field-utf8" for r in ranges)


@pytest.mark.parametrize("wire_stream", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_transport_streaming_json_and_decoded_gzip_sse_keep_usage(
    tmp_path, monkeypatch, wire_stream, compressed
):
    import gzip

    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(loader, "load_optional_sources", loader.load_optional_sources)
    for name in ["complete_one", "stream_one"]:
        monkeypatch.setattr(
            SingleCandidateAgentTransport, name, getattr(SingleCandidateAgentTransport, name)
        )

    class Channel:
        seq = 0
        config = dict(data_dir=str(tmp_path), trial_key=dict(execution_item_id="E01.runtime_read"))
        events = []

        def emit(self, kind, payload, ack=False):
            self.seq += 1
            self.events.append(payload | dict(type=kind))

        def attempt(self, purpose, **metadata):
            self.emit("call_attempt", dict(purpose=purpose, **metadata))

        def provider_attempt(self, **metadata):
            self.attempt("provider_send", **metadata)
            return self.seq

    channel = Channel()
    observe(
        channel,
        "https://provider.test",
        dict(model="test", max_input_bytes=10000, max_output_tokens=64),
    )
    value = dict(
        id="completion-id",
        model="reported-version",
        usage=dict(prompt_tokens=10, completion_tokens=2, total_tokens=12),
    )
    raw = (
        ("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode()
        if wire_stream
        else json.dumps(value).encode()
    )
    headers = {"content-type": "text/event-stream" if wire_stream else "application/json"}
    if compressed:
        raw = gzip.compress(raw)
        headers["content-encoding"] = "gzip"

    def reply(request):
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(raw))

    payload = dict(
        model="test",
        max_tokens=64,
        messages=[dict(role="user", content="synthetic")],
        stream=wire_stream,
        temperature=0,
        thinking=dict(type="disabled"),
    )
    if wire_stream:
        payload["stream_options"] = dict(include_usage=True)
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        # The HTTP SDK flag deliberately differs from the JSON wire mode.
        with client.stream(
            "POST", "https://provider.test/chat/completions", json=payload
        ) as response:
            if wire_stream:
                assert "data: [DONE]" in list(response.iter_lines())
            else:
                assert response.json() == value
    usage = [v for v in channel.events if v.get("category") == "usage"]
    assert len(usage) == 1 and usage[0]["actual_model"] == "reported-version"
    assert usage[0]["usage"]["total_tokens"] == 12
    returned = next(v for v in channel.events if v.get("category") == "provider_response")
    assert returned["request_id_hash"] is None
    assert returned["transport_stream"] and returned["wire_stream"] == wire_stream
