"""Final HTTP body observer and endpoint fence, confined to a trial process."""

from __future__ import annotations

from datetime import datetime, timezone
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any
from types import SimpleNamespace
from urllib.parse import urlsplit

class ContextLocal:
    """Copy bindings into async tasks without sharing a worker's mutable slot."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_values", ContextVar(name, default=None))

    def __getattr__(self, name: str) -> Any:
        values = self._values.get() or {}
        if name not in values:
            raise AttributeError(name)
        return values[name]

    def __setattr__(self, name: str, value: Any) -> None:
        self._values.set({**(self._values.get() or {}), name: value})


class SendPermission:
    """A task-local, revocable permission for one admitted physical send."""

    def __init__(self) -> None:
        self.scope: ContextVar[dict[str, Any] | None] = ContextVar("eval-send-permission", default=None)

    @property
    def permitted(self) -> bool:
        return bool((self.scope.get() or {}).get("permitted"))

    @permitted.setter
    def permitted(self, value: bool) -> None:
        if value:
            self.scope.set(dict(permitted=True, addresses=set(), attempt=None))
        elif (scope := self.scope.get()) is not None:
            # Child tasks inherit this object. Revoke it for them as well.
            scope.update(permitted=False, addresses=set(), attempt=None)

    @property
    def addresses(self) -> set[tuple[str, int]]:
        return (self.scope.get() or {}).get("addresses", set()) if self.permitted else set()

    @addresses.setter
    def addresses(self, value: set[tuple[str, int]]) -> None:
        if (scope := self.scope.get()) is not None:
            scope["addresses"] = value

    @property
    def attempt(self) -> Any:
        return (self.scope.get() or {}).get("attempt") if self.permitted else None

    @attempt.setter
    def attempt(self, value: Any) -> None:
        if (scope := self.scope.get()) is not None:
            scope["attempt"] = value


SEND_PERMISSION = SendPermission()
EXECUTION_IDENTITY = ContextLocal("eval-execution-identity")


def observe(channel: Any, endpoint: str, limits: dict[str, Any],
            *, async_client: Any = None) -> Any:
    import httpx
    from offerpilot.context_projector.contracts import canonical_json
    from offerpilot.context_projector.gateway import SingleCandidateAgentTransport
    from offerpilot.context_projector.projector import ModelSurfaceProjector

    state = ContextLocal("eval-provider-invocation")
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
                activity = threading.Event()
                activity.set()
                state.activity = activity
                if getattr(state, "barrier", None) is None:
                    state.barrier = SimpleNamespace(reached=False)
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
                    activity.clear()
                    state.identity = None
                    state.sources = []

            return execute

        setattr(SingleCandidateAgentTransport, name, wrap(original))
    target = urlsplit(endpoint)
    original_send = httpx.Client.send

    def prepare(request: Any, *, require_scope: bool = False) -> Any:
        identity = getattr(state, "identity", None)
        activity = getattr(state, "activity", None)
        if (require_scope and identity is None) or (identity is not None and
                                                   (activity is None or not activity.is_set())):
            raise PermissionError("provider invocation scope is missing or closed")
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
                            options: dict[str, Any] = {"separators": (",", ":")} if compact else {}
                            serialized = json.dumps(
                                root_unit, ensure_ascii=ascii_only, sort_keys=compact,
                                **options,
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
        ] == "E07.runtime_reattach" and not getattr(getattr(state, "barrier", None), "reached", False):
            if getattr(state, "barrier", None) is None:
                state.barrier = SimpleNamespace(reached=False)
            state.barrier.reached = True
            channel.emit("fault_reached", {"boundary": "http_before_send"}, ack=True)
        return SimpleNamespace(attempt=attempt, private=private, payload=payload,
                               identity=identity, activity=activity)

    def send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        prepared = prepare(request)
        attempt, private, payload = prepared.attempt, prepared.private, prepared.payload
        kwargs["follow_redirects"] = False
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        invocation_identity = prepared.identity
        trace_events: list[dict[str, Any]] = []
        previous_trace = request.extensions.get("trace")

        def trace(name: str, info: Any) -> None:
            if len(trace_events) < 64:
                trace_events.append(dict(event=name, elapsed_seconds=round(time.monotonic() - started, 6)))
            if previous_trace is not None:
                previous_trace(name, info)

        request.extensions["trace"] = trace
        responded = False
        try:
            SEND_PERMISSION.permitted = True
            SEND_PERMISSION.addresses = set()
            SEND_PERMISSION.attempt = attempt
            try:
                response = original_send(self, request, *args, **kwargs)
            finally:
                SEND_PERMISSION.permitted = False
                SEND_PERMISSION.addresses = set()
                SEND_PERMISSION.attempt = None
                if previous_trace is None:
                    request.extensions.pop("trace", None)
                else:
                    request.extensions["trace"] = previous_trace
            responded = True
            channel.emit(
                "model_observation",
                {
                    "category": "provider_response",
                    "attempt": attempt,
                    "identity": invocation_identity,
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
                    "transport_events": trace_events,
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
                from scripts.evaluation_subject.core_stream import observe_stream

                observe_stream(channel, response, attempt, invocation_identity, started, started_at)
            return response
        except Exception as exc:
            chain: list[dict[str, Any]] = []
            seen: set[int] = set()
            current: BaseException | None = exc
            while current is not None and id(current) not in seen and len(chain) < 8:
                seen.add(id(current))
                chain.append(dict(type=type(current).__name__, module=type(current).__module__,
                                  errno=getattr(current, "errno", None),
                                  winerror=getattr(current, "winerror", None)))
                current = current.__cause__ or current.__context__
            channel.emit(
                "model_observation",
                {
                    "category": "provider_error",
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "exception_chain": chain,
                    "http_response_received": responded,
                    "failure_stage": "response_capture" if responded else
                    trace_events[-1]["event"] if trace_events else "http_send",
                    "transport_events": trace_events,
                },
                ack=True,
            )
            raise

    setattr(httpx.Client, "send", send)
    if async_client is not None:
        from scripts.evaluation_subject.core_async_transport import install

        return install(channel, async_client, prepare)
    return None
