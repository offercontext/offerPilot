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
        config = {"data_dir": str(tmp_path)}
        events = []

        def emit(self, kind, payload, ack=False):
            self.seq += 1
            self.events.append(payload | {"type": kind})

        def attempt(self, purpose, **metadata):
            self.emit("call_attempt", dict(purpose=purpose, **metadata))

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
        model="test", max_tokens=64, messages=[dict(role="user", content=text + "\n" + text)]
    )
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        client.post("https://provider.test/v1/chat/completions", json=payload)
        with pytest.raises(PermissionError):
            client.post("https://other.test/v1/chat/completions", json=payload)
    assert not SEND_PERMISSION.permitted
    record = next(e for e in channel.events if e.get("category") == "final_input")
    raw = (tmp_path / record["request_file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["payload_hash"]
    field = json.loads(raw)["messages"][0]["content"].encode()
    ranges = record["observations"][0]["ranges"]
    assert len(ranges) == 2
    assert all(field[r["start"] : r["end"]].decode() == text for r in ranges)
    assert all(r["unit_identity"] == {"id": "memory-1", "version": 2} for r in ranges)
    assert all(r["coordinate_space"] == "decoded-field-utf8" for r in ranges)
