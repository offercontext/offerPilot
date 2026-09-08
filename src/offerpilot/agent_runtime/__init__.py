from importlib import import_module
from typing import TYPE_CHECKING

from offerpilot.agent_runtime.keyring import (
    JOURNAL_KEY_FILENAME,
    JournalKeyDomain,
    load_or_create_journal_key,
)
from offerpilot.agent_runtime.events import (
    ContextManifestInput,
    EventDraft,
    JournalEventValidationError,
    NormalizedContextIdentity,
    PreparedSnapshot,
    canonical_json,
    model_id_fingerprint,
    normalize_context_identity,
    normalize_source_reference,
    pending_identity_fingerprint,
    prepare_context_snapshot,
    prepare_event,
)

if TYPE_CHECKING:
    from offerpilot.agent_runtime.journal import (
        EventInput,
        NullRunRecorder,
        NullRunRecorderFactory,
        RunRecorder,
        RunRecorderFactory,
        ResumedDisposition,
        SafeRunRecorder,
        StartRunBuilder,
        StartSegmentBuilder,
        SuspendedDisposition,
        TerminalDisposition,
    )
    from offerpilot.agent_runtime.trace import (
        AgentRunTrace,
        AgentRunTraceNotFound,
        reconstruct_agent_run,
    )

__all__ = [
    "JOURNAL_KEY_FILENAME",
    "JournalKeyDomain",
    "load_or_create_journal_key",
    "ContextManifestInput",
    "EventDraft",
    "JournalEventValidationError",
    "NormalizedContextIdentity",
    "PreparedSnapshot",
    "canonical_json",
    "model_id_fingerprint",
    "normalize_context_identity",
    "normalize_source_reference",
    "pending_identity_fingerprint",
    "prepare_context_snapshot",
    "prepare_event",
    "EventInput",
    "NullRunRecorder",
    "NullRunRecorderFactory",
    "RunRecorder",
    "RunRecorderFactory",
    "ResumedDisposition",
    "SafeRunRecorder",
    "StartRunBuilder",
    "StartSegmentBuilder",
    "SuspendedDisposition",
    "TerminalDisposition",
    "AgentRunTrace",
    "AgentRunTraceNotFound",
    "reconstruct_agent_run",
]


def __getattr__(name: str) -> object:
    if name in {
        "EventInput",
        "NullRunRecorder",
        "NullRunRecorderFactory",
        "RunRecorder",
        "RunRecorderFactory",
        "ResumedDisposition",
        "SafeRunRecorder",
        "StartRunBuilder",
        "StartSegmentBuilder",
        "SuspendedDisposition",
        "TerminalDisposition",
    }:
        journal = import_module("offerpilot.agent_runtime.journal")
        return getattr(journal, name)
    if name in {"AgentRunTrace", "AgentRunTraceNotFound", "reconstruct_agent_run"}:
        trace = import_module("offerpilot.agent_runtime.trace")
        return getattr(trace, name)
    raise AttributeError(name)
