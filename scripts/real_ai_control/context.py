"""Associate each queued Runtime turn, never a reusable worker's creation context."""

from __future__ import annotations

import threading
from typing import Any, Callable
from weakref import WeakKeyDictionary

from .archive import EvidenceArchive
from .transport import API_REQUEST, RUNTIME_IDENTITY


def install_runtime_association(archive: EvidenceArchive) -> Callable[[], None]:
    from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager

    submit, worker = RuntimeExecutionManager.submit, RuntimeExecutionManager._worker_entry
    bindings: WeakKeyDictionary[Any, dict[tuple[str, int], int | None]] = WeakKeyDictionary()
    lock = threading.Lock()

    def admitted(manager: Any, **kwargs: Any) -> Any:
        key = kwargs["turn_id"], kwargs.get("generation", 1)
        with lock:
            # A replay retains the first admission; it never adopts a poll's ID.
            entries = bindings.setdefault(manager, {})
            fresh = key not in entries
            entries.setdefault(key, API_REQUEST.get())
            try:
                # submit never waits for a worker; its queued worker may wait
                # briefly for this lock. Refused admission removes the mapping.
                return submit(manager, **kwargs)
            except BaseException:
                if fresh:
                    entries.pop(key, None)
                raise

    def entered(manager: Any, handle: Any) -> Any:
        key = handle.turn_id, handle.generation
        with lock:
            api_id = bindings.get(manager, {}).get(key)
        token = API_REQUEST.set(api_id)
        identity = dict(
            turn_id=handle.turn_id,
            generation=handle.generation,
            conversation_id=handle.conversation_id,
            request_id=handle.request_id,
        )
        other = RUNTIME_IDENTITY.set(identity)
        try:
            archive.event("runtime-worker-binding", api_request_id=api_id, identity=identity)
            return worker(manager, handle)
        finally:
            RUNTIME_IDENTITY.reset(other)
            API_REQUEST.reset(token)

    setattr(RuntimeExecutionManager, "submit", admitted)
    setattr(RuntimeExecutionManager, "_worker_entry", entered)

    def restore() -> None:
        setattr(RuntimeExecutionManager, "submit", submit)
        setattr(RuntimeExecutionManager, "_worker_entry", worker)

    return restore
