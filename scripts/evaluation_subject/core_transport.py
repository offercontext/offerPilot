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
    from offerpilot.context_projector.contracts import canonical_json
    from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
    from offerpilot.context_projector.projector import ModelSurfaceProjector

    state = threading.local()
    original_project = ModelSurfaceProjector.project
    projected: dict[str, tuple[Any, dict[str, Any]]] = {}
    projection_lock = threading.Lock()

    def project(self: Any, request: Any) -> Any:
        surface = original_project(self, request)
        optional = request.optional_sources
        sources = [
            {
                "kind": s.kind,
                "source_id": s.revision_identity,
                "revision": s.content_revision_fingerprint,
                "content": s.canonical_content.decode("utf-8"),
            }
            for s in (optional.sources if optional else ())
        ]
        selection = {
            "identity": {
                "model_call_id": surface.model_call_id,
                "segment_id": request.provider_surface_build_identity.segment_id,
                "surface_fingerprint": surface.runtime_surface_fingerprint,
            },
            "sources": sources,
            "contributors": [{"name": c.name, "status": c.status}
                             for c in (optional.contributors if optional else ())],
        }
        with projection_lock:
            projected[surface.model_call_id] = (surface, selection)
        channel.emit(
            "model_observation",
            {
                "category": "sources",
                **selection,
            },
            ack=True,
        )
        return surface

    setattr(ModelSurfaceProjector, "project", project)
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
                with projection_lock:
                    selected = projected.get(surface.model_call_id)
                state.sources = (
                    selected[1]["sources"]
                    if selected is not None and selected[0] is surface
                    and all(state.identity.get(key) == value
                            for key, value in selected[1]["identity"].items())
                    else []
                )
                try:
                    return original(self, candidate, surface, *args, **kwargs)
                finally:
                    state.identity = None
                    state.sources = []

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
        allowed_fields = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "stream",
            "stream_options",
            "max_tokens",
            "temperature",
            "thinking",
            "n",
        }
        if (
            payload.get("model") != limits["model"]
            or set(payload) - allowed_fields
            or type(payload.get("max_tokens")) is not int
            or not 0 < payload["max_tokens"] <= limits["max_output_tokens"]
            or type(payload.get("n", 1)) is not int
            or payload.get("n", 1) != 1
            or type(payload.get("temperature")) not in {int, float}
            or payload.get("temperature") != 0
            or payload.get("thinking") != {"type": "disabled"}
            or type(payload.get("stream", False)) is not bool
            or payload.get("stream") is True
            and payload.get("stream_options") != {"include_usage": True}
            or payload.get("stream", False) is False
            and "stream_options" in payload
        ):
            raise ValueError("provider model/output cap changed")
        attempt_seq = channel.provider_attempt(
            **(getattr(state, "identity", None) or {}),
            input_bytes=len(body),
            output_cap=limits["max_output_tokens"],
        )
        attempt = f"http-{attempt_seq}"
        private = Path(channel.config["data_dir"])
        path = private / (attempt + "-request.json")
        path.write_bytes(body)
        observations = []
        for source in getattr(state, "sources", []):
            selected = json.loads(source["content"])
            strings = []

            def leaves(value: Any, path: str, root_unit: Any, identity: Any = None) -> None:
                if isinstance(value, dict):
                    identity = {
                        k: value[k]
                        for k in (
                            "id",
                            "version",
                            "version_id",
                            "snapshot_id",
                            "note_id",
                            "note_version_id",
                            "source_id",
                            "evidence_id",
                        )
                        if k in value
                    } or identity
                    for k, v in value.items():
                        if k in {"content", "text", "excerpt", "preference"} and isinstance(v, str):
                            strings.append((v, f"{path}.{k}", identity, root_unit))
                        elif isinstance(v, (dict, list)):
                            leaves(v, f"{path}.{k}", root_unit, identity)
                elif isinstance(value, list):
                    for n, v in enumerate(value):
                        leaves(v, f"{path}[{n}]", root_unit, identity)

            for n, unit in enumerate(selected if isinstance(selected, list) else [selected]):
                leaves(unit, f"$[{n}]", unit)
            for i, m in enumerate(payload.get("messages", [])):
                content = m.get("content", "")
                if not isinstance(content, str):
                    continue
                ranges = []
                encoded = content.encode()
                for t, source_path, unit, root_unit in strings:
                    unit_bounds = []
                    # Require the complete selected versioned unit in the wire,
                    # rather than assigning its identity to matching history text.
                    for ascii_only in (False, True):
                        for compact in (False, True):
                            serialized = json.dumps(
                                root_unit, ensure_ascii=ascii_only, sort_keys=compact,
                                **({"separators": (",", ":")} if compact else {}),
                            ).encode()
                            cursor = 0
                            while (found := encoded.find(serialized, cursor)) >= 0:
                                unit_bounds.append((found, found + len(serialized)))
                                cursor = found + len(serialized)
                    representations = {
                        t.encode(): "plain",
                        json.dumps(t, ensure_ascii=False)[1:-1].encode(): "json-string-content",
                        json.dumps(t, ensure_ascii=True)[1:-1].encode(): "json-ascii-string-content",
                    }
                    for needle, wire_encoding in representations.items():
                        start = 0
                        while needle and (offset := encoded.find(needle, start)) >= 0:
                            ranges.append(
                                {
                                    "start": offset,
                                    "end": offset + len(needle),
                                    "content_hash": hashlib.sha256(needle).hexdigest(),
                                    "source_content_hash": hashlib.sha256(t.encode()).hexdigest(),
                                    "wire_encoding": wire_encoding,
                                    "source_field_path": source_path,
                                    "unit_identity": unit,
                                    "unit_hash": hashlib.sha256(canonical_json(root_unit)).hexdigest()
                                    if any(lo <= offset and offset + len(needle) <= hi
                                           for lo, hi in unit_bounds) else None,
                                    "unit_binding": "complete-json-unit"
                                    if any(lo <= offset and offset + len(needle) <= hi
                                           for lo, hi in unit_bounds) else "content-only",
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
        if channel.config["trial_key"][
            "execution_item_id"
        ] == "E07.runtime_reattach" and not getattr(state, "stream_barrier_reached", False):
            state.stream_barrier_reached = True
            channel.emit("fault_reached", {"boundary": "http_before_send"}, ack=True)
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
                        response.headers["x-request-id"].encode()
                    ).hexdigest()
                    if response.headers.get("x-request-id")
                    else None,
                    "transport_stream": bool(kwargs.get("stream")),
                    "wire_stream": bool(payload.get("stream")),
                    "content_encoding": response.headers.get("content-encoding"),
                    "content_type": response.headers.get("content-type"),
                },
                ack=True,
            )
            if not payload.get("stream"):
                # SDKs can use HTTP streaming for ordinary JSON. Read the decoded
                # response, rather than interpreting transport chunks as SSE.
                raw_bytes = response.read()
                if len(raw_bytes) > 1024 * 1024:
                    raise ValueError("oversized provider JSON response")
                raw = response.json()
                if response.status_code == 200:
                    (private / (attempt + "-response.json")).write_bytes(raw_bytes)
                channel.emit(
                    "model_observation",
                    {
                        "category": "usage",
                        "attempt": attempt,
                        "actual_model": raw.get("model"),
                        "usage": raw.get("usage"),
                        "completion_id_hash": hashlib.sha256(raw["id"].encode()).hexdigest()
                        if isinstance(raw.get("id"), str)
                        else None,
                    },
                    ack=True,
                )
            else:
                # Observe the public decoded-byte iterator so gzip/deflate SSE
                # is preserved for the SDK and the evidence observer alike.
                original_iterator = response.iter_bytes

                def observed_iterator(*args: Any, **kwargs: Any) -> Any:
                    pending = b""
                    for chunk in original_iterator(*args, **kwargs):
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
                                            "completion_id_hash": hashlib.sha256(
                                                value["id"].encode()
                                            ).hexdigest()
                                            if isinstance(value.get("id"), str)
                                            else None,
                                        },
                                        ack=True,
                                    )
                        yield chunk

                setattr(response, "iter_bytes", observed_iterator)
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
