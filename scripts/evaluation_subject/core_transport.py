"""Final HTTP body observer and endpoint fence, confined to a trial process."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlsplit

SEND_PERMISSION = threading.local()
EXECUTION_IDENTITY = threading.local()


def observe(channel: Any, endpoint: str, limits: dict[str, Any]) -> None:
    import httpx
    from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
    from offerpilot.context_sources import loader

    state = threading.local()
    original_source = loader.load_optional_sources

    def sources(*args: Any, **kwargs: Any) -> Any:
        result = original_source(*args, **kwargs)
        state.sources = [
            {
                "kind": s.kind,
                "source_id": s.revision_identity,
                "revision": s.content_revision_fingerprint,
                "content": s.canonical_content.decode("utf-8"),
            }
            for s in result.sources
        ]
        channel.emit(
            "model_observation",
            {
                "category": "sources",
                "sources": state.sources,
                "contributors": [{"name": c.name, "status": c.status} for c in result.contributors],
            },
            ack=True,
        )
        return result

    import sys

    for m in list(sys.modules.values()):
        if m and getattr(m, "load_optional_sources", None) is original_source:
            setattr(m, "load_optional_sources", sources)
    for name in ("complete_one", "stream_one"):
        original = getattr(SingleCandidateAgentTransport, name)

        def wrap(original: Any) -> Any:
            def execute(self: Any, candidate: Any, surface: Any, *args: Any, **kwargs: Any) -> Any:
                identity = kwargs["invocation_identity"]
                state.identity = {
                    "model_call_id": surface.model_call_id,
                    "provider_attempt_id": kwargs["provider_attempt_id"],
                    "candidate_ordinal": kwargs["candidate_ordinal"],
                    "segment_id": identity.segment_id,
                    "surface_fingerprint": identity.surface_fingerprint,
                    "execution": getattr(EXECUTION_IDENTITY, "value", None),
                }
                try:
                    return original(self, candidate, surface, *args, **kwargs)
                finally:
                    state.identity = None

            return execute

        setattr(SingleCandidateAgentTransport, name, wrap(original))
    target = urlsplit(endpoint)
    original_send = httpx.Client.send

    def send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        url = request.url
        if (url.host, url.port or (443 if url.scheme == "https" else 80), url.scheme) != (
            target.hostname,
            target.port or (443 if target.scheme == "https" else 80),
            target.scheme,
        ):
            channel.attempt("network", blocked=True, boundary="http_destination")
            raise PermissionError("provider destination outside frozen endpoint")
        expected = target.path.rstrip("/") + "/chat/completions"
        if request.method != "POST" or url.path != expected or url.query:
            channel.attempt("network", blocked=True, boundary="http_path")
            raise PermissionError("provider operation outside allowed surface")
        body = request.read()
        if len(body) > limits["max_input_bytes"]:
            channel.attempt("provider_send", blocked=True, input_bytes=len(body))
            raise ValueError("provider serialized input exceeds frozen cap")
        payload = json.loads(body)
        if (
            payload.get("model") != limits["model"]
            or payload.get("max_tokens", limits["max_output_tokens"]) > limits["max_output_tokens"]
        ):
            raise ValueError("provider model/output cap changed")
        channel.attempt(
            "provider_send",
            **(getattr(state, "identity", None) or {}),
            input_bytes=len(body),
            output_cap=limits["max_output_tokens"],
        )
        attempt = f"http-{channel.seq}"
        private = Path(channel.config["data_dir"])
        path = private / (attempt + "-request.json")
        path.write_bytes(body)
        observations = []
        for source in getattr(state, "sources", []):
            selected = json.loads(source["content"])
            strings = []

            def leaves(value: Any, path: str = "$", identity: Any = None) -> None:
                if isinstance(value, dict):
                    identity = {
                        k: value[k]
                        for k in (
                            "id",
                            "version",
                            "note_id",
                            "note_version_id",
                            "source_id",
                            "evidence_id",
                        )
                        if k in value
                    } or identity
                    for k, v in value.items():
                        if k in {"content", "text", "excerpt", "preference"} and isinstance(v, str):
                            strings.append((v, f"{path}.{k}", identity))
                        elif isinstance(v, (dict, list)):
                            leaves(v, f"{path}.{k}", identity)
                elif isinstance(value, list):
                    for n, v in enumerate(value):
                        leaves(v, f"{path}[{n}]", identity)

            leaves(selected)
            for i, m in enumerate(payload.get("messages", [])):
                content = m.get("content", "")
                if not isinstance(content, str):
                    continue
                ranges = []
                encoded = content.encode()
                for t, source_path, unit in strings:
                    needle = t.encode()
                    start = 0
                    while needle and (offset := encoded.find(needle, start)) >= 0:
                        ranges.append(
                            {
                                "start": offset,
                                "end": offset + len(needle),
                                "content_hash": hashlib.sha256(needle).hexdigest(),
                                "source_field_path": source_path,
                                "unit_identity": unit,
                                "coordinate_space": "decoded-field-utf8",
                            }
                        )
                        start = offset + len(needle)
                if ranges:
                    observations.append(
                        {
                            "kind": source["kind"],
                            "source_id": source["source_id"],
                            "revision": source["revision"],
                            "field_path": f"messages[{i}].content",
                            "ranges": ranges,
                        }
                    )
        channel.emit(
            "model_observation",
            {
                "category": "final_input",
                "attempt": attempt,
                "identity": getattr(state, "identity", None),
                "request_file": path.name,
                "payload_hash": hashlib.sha256(body).hexdigest(),
                "observations": observations,
            },
            ack=True,
        )
        kwargs["follow_redirects"] = False
        try:
            SEND_PERMISSION.permitted = True
            try:
                response = original_send(self, request, *args, **kwargs)
            finally:
                SEND_PERMISSION.permitted = False
            channel.emit(
                "model_observation",
                {
                    "category": "provider_response",
                    "attempt": attempt,
                    "status": response.status_code,
                    "request_id_hash": hashlib.sha256(
                        response.headers.get("x-request-id", "").encode()
                    ).hexdigest(),
                },
                ack=True,
            )
            if not kwargs.get("stream"):
                raw = response.json()
                channel.emit(
                    "model_observation",
                    {
                        "category": "usage",
                        "attempt": attempt,
                        "actual_model": raw.get("model"),
                        "usage": raw.get("usage"),
                    },
                    ack=True,
                )
            else:
                if not isinstance(response.stream, httpx.SyncByteStream):
                    raise TypeError("synchronous provider stream required")
                original_stream = response.stream

                class ObservedStream(httpx.SyncByteStream):
                    def __iter__(self) -> Any:
                        pending = b""
                        for chunk in original_stream:
                            pending += chunk
                            if len(pending) > 1024 * 1024:
                                raise ValueError("oversized provider SSE frame")
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                if line.startswith(b"data:"):
                                    try:
                                        value = json.loads(line[5:].strip())
                                    except ValueError:
                                        continue
                                    if value.get("usage"):
                                        channel.emit(
                                            "model_observation",
                                            {
                                                "category": "usage",
                                                "attempt": attempt,
                                                "actual_model": value.get("model"),
                                                "usage": value["usage"],
                                                "stream": True,
                                            },
                                            ack=True,
                                        )
                            yield chunk

                    def close(self) -> None:
                        original_stream.close()

                response.stream = ObservedStream()
            return response
        except Exception as exc:
            channel.emit(
                "model_observation",
                {
                    "category": "provider_error",
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                },
                ack=True,
            )
            raise

    setattr(httpx.Client, "send", send)
