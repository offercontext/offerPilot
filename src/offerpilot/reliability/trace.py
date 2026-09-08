"""Sanitized trace envelopes for Mock Interview provider operations.

One JSON line per operation is appended to ``<data_dir>/logs/mock_interview_trace.jsonl``.
The envelope links an API request, its provider call, and the browser-facing
response through ``operation_id`` while never recording raw JD/resume/answer
text, prompts, model output, API keys, or full idempotency keys.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRACE_FIELDS: tuple[str, ...] = (
    "run_id",
    "scenario_id",
    "operation_id",
    "attempt_id",
    "generation_revision",
    "idempotency_key_hash",
    "provider",
    "model",
    "capability_snapshot_hash",
    "input_fingerprint",
    "schema_fingerprint",
    "started_at",
    "first_byte_ms",
    "completed_ms",
    "provider_outcome",
    "validator_stage",
    "failure_category",
    "repair_count",
    "final_disposition",
    "response_error_code",
)

PROVIDER_OUTCOMES = frozenset(
    {"success", "success_after_repair", "unverifiable", "provider_error"}
)
VALIDATOR_STAGES = frozenset({"question", "feedback"})

_RUN_ID = uuid.uuid4().hex
_WRITE_LOCK = threading.Lock()
_MAX_STRING_LENGTH = 200


def new_run_id() -> str:
    return _RUN_ID


def hash_idempotency_key(idempotency_key: str) -> str:
    """Store only a prefixed digest; the raw key must never appear in traces."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"idem-{digest}"


def _require_scalar(name: str, value: Any, *, allowed: frozenset[str] | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or value is None:
        if value is None:
            return
        raise ValueError(f"trace field {name!r} must stay scalar, got {type(value).__name__}")
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise ValueError(f"trace field {name!r} exceeds {_MAX_STRING_LENGTH} characters")
        if allowed is not None and value not in allowed:
            raise ValueError(f"trace field {name!r} has unknown value {value!r}")


@dataclass(frozen=True)
class MockInterviewTraceEnvelope:
    run_id: str
    scenario_id: str
    operation_id: str
    attempt_id: int | None
    generation_revision: int | None
    idempotency_key_hash: str
    provider: str
    model: str
    capability_snapshot_hash: str
    input_fingerprint: str
    schema_fingerprint: str
    started_at: str
    first_byte_ms: int | None
    completed_ms: int | None
    provider_outcome: str
    validator_stage: str
    failure_category: str
    repair_count: int
    final_disposition: str
    response_error_code: str

    def __post_init__(self) -> None:
        for field in TRACE_FIELDS:
            value = getattr(self, field)
            if isinstance(value, (dict, list, tuple, set)):
                raise ValueError(f"trace field {field!r} must not carry structured content")
        _require_scalar("provider_outcome", self.provider_outcome, allowed=PROVIDER_OUTCOMES)
        _require_scalar("validator_stage", self.validator_stage, allowed=VALIDATOR_STAGES)
        for field in ("run_id", "scenario_id", "operation_id", "idempotency_key_hash",
                      "provider", "model", "capability_snapshot_hash", "input_fingerprint",
                      "schema_fingerprint", "started_at", "failure_category",
                      "final_disposition", "response_error_code"):
            _require_scalar(field, getattr(self, field))
        if not isinstance(self.repair_count, int) or isinstance(self.repair_count, bool):
            raise ValueError("trace field 'repair_count' must be an int")
        for field in ("attempt_id", "generation_revision", "first_byte_ms", "completed_ms"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"trace field {field!r} must be an int or null")

    def to_json(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in TRACE_FIELDS}


def record_mock_interview_trace(data_dir: Path, envelope: MockInterviewTraceEnvelope) -> None:
    log_dir = data_dir / "logs"
    line = json.dumps(envelope.to_json(), ensure_ascii=True, separators=(",", ":"))
    with _WRITE_LOCK:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "mock_interview_trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_mock_interview_traces(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "logs" / "mock_interview_trace.jsonl"
    if not path.exists():
        return []
    traces: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if list(entry) != list(TRACE_FIELDS):
            raise ValueError(f"trace envelope field drift: {sorted(set(entry) ^ set(TRACE_FIELDS))}")
        traces.append(entry)
    return traces
