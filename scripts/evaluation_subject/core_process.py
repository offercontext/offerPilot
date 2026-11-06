"""Child-only observation of actual proposals and Pending creation, before HITL."""

from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Any
from uuid import uuid4

from scripts.evaluation_subject.core_transport import EXECUTION_IDENTITY

VERSION = "process-signals-v1"


@contextmanager
def observe(channel: Any) -> Any:
    from offerpilot.ai.agent_loop import AgentLoopRunner, _LoopServices

    state = threading.local()
    originals = []

    def emit(event: str, **values: Any) -> None:
        scope = getattr(state, "scope", None)
        if scope is not None:
            scope["index"] += 1
            channel.emit(
                "model_observation",
                dict(category="process", version=VERSION, **scope, event=event, **values),
                ack=True,
            )

    def install(cls: Any, name: str, wrapper: Any) -> None:
        original = getattr(cls, name)
        originals.append((cls, name, original))
        setattr(cls, name, wrapper(original))

    def run_wrapper(original: Any) -> Any:
        def run(self: Any, invocation: Any, **kwargs: Any) -> Any:
            previous = getattr(state, "scope", None)
            state.scope = dict(
                scope_id=str(uuid4()),
                index=0,
                execution=getattr(EXECUTION_IDENTITY, "value", None),
                # Approved continuations carry Approval authority, which has no Segment ID.
                # Preserve the unknown coordinate without interrupting confirmation execution.
                segment_id=getattr(invocation.tool_context.authority, "segment_id", None),
            )
            state.identity = None
            emit("started")
            try:
                result = original(self, invocation, **kwargs)
                emit("finished", outcome="completed", pending=result.pending is not None)
                return result
            except BaseException:
                emit("finished", outcome="failed", pending=None)
                raise
            finally:
                state.scope = previous
                state.identity = None

        return run

    def project_wrapper(original: Any) -> Any:
        def project(self: Any, *args: Any, **kwargs: Any) -> Any:
            surface = original(self, *args, **kwargs)
            identity = dict(
                model_call_id=surface.model_call_id,
                segment_id=kwargs["build_identity"].segment_id,
                surface_fingerprint=surface.runtime_surface_fingerprint,
            )
            state.identity = identity
            emit("model_input", identity=identity)
            return surface

        return project

    def model_wrapper(original: Any) -> Any:
        def model(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            view = getattr(getattr(self, "surface_gate", None), "authority_metadata_view", None)
            entries = getattr(view, "entries", {})
            calls = []
            for call in result.tool_calls:
                entry = entries.get(call.name)
                calls.append(
                    dict(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        operation_kind=None if entry is None else str(entry.operation_kind),
                        confirmation_required=None
                        if entry is None
                        else entry.confirmation_policy == "required",
                    )
                )
            emit("model_result", identity=getattr(state, "identity", None), tool_calls=calls)
            return result

        return model

    def pending_wrapper(original: Any) -> Any:
        def pending(self: Any, turn: Any, action: Any) -> Any:
            emit(
                "pending_created",
                identity=getattr(state, "identity", None),
                tool_call_id=action.tool_call_id,
                tool_name=action.tool_name,
                persistence="not_yet_observed",
            )
            # Creation is observable even if persistence later fails. Never call it persisted.
            return original(self, turn, action)

        return pending

    try:
        install(AgentLoopRunner, "run", run_wrapper)
        install(_LoopServices, "project_model_surface", project_wrapper)
        install(_LoopServices, "complete_model", model_wrapper)
        install(_LoopServices, "persist_pending_before_release", pending_wrapper)
        yield
    finally:
        for cls, name, original in reversed(originals):
            setattr(cls, name, original)
