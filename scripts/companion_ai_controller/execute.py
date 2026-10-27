"""No-provider rehearsal only; a future live executor is deliberately absent.

All helper imports resolve in this reviewed controller checkout, never in a
candidate directory. Frozen request bytes are data. Simulation clock replacement
is local to the trusted broker module and cannot enable a network transport.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import shadow_continuation as lineage
from scripts.companion_ai_validation import shadow_driver as shadow

EVIDENCE_KIND = "offline_rehearsal_not_provider_evidence"
FILES = ("summary.json", "requests.jsonl", "responses.jsonl", "budget.jsonl")
FAILURES = frozenset({None, "missing_usage", "transport_unknown", "tool_response"})


class RehearsalError(RuntimeError):
    """Constant safe errors only."""


def execute_live(*_args: Any, **_kwargs: Any) -> None:
    """No fixture, flag, environment variable or admission can activate live."""
    raise RehearsalError("LIVE_EXECUTION_NOT_IMPLEMENTED")


class _Clock:
    @staticmethod
    def time() -> float:
        return float(lineage.PRICE_VALID_UNTIL - 3600)

    @staticmethod
    def monotonic() -> float:
        return 100.0


class _FakeUpstream:
    def __init__(self, failure: str | None):
        self.calls = 0
        self.failure = failure
        self.fixed_cases = shadow.cases()

    def __call__(self, raw: bytes) -> tuple[int, bytes]:
        if self.calls >= 6:
            raise RehearsalError("REHEARSAL_REQUEST_LIMIT")
        case = self.fixed_cases[self.calls]
        if raw != shadow.frozen_request(case["id"]):
            raise RehearsalError("REHEARSAL_REQUEST_MISMATCH")
        self.calls += 1
        if self.failure == "transport_unknown":
            raise OSError("SYNTHETIC_TRANSPORT_FAILURE")
        review = {"target": case["target"], "verdict": "unknown", "evidence": [],
                  "rationale": "固定假响应 仅演练结构 不评价模型效果"}
        message: dict[str, Any] = {"role": "assistant", "content": gate.encode(review).decode()}
        if self.failure == "tool_response":
            message["tool_calls"] = [{"function": {"name": "never_execute", "arguments": "{}"}}]
        response: dict[str, Any] = {"model": gate.MODEL, "choices": [
            {"finish_reason": "stop", "message": message}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100}}
        if self.failure == "missing_usage":
            response.pop("usage")
        return 200, gate.encode(response)


def _write(path: Path, raw: bytes) -> None:
    # Rehearsal output is a new private directory; never update an old run.
    with path.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def execute_rehearsal(evidence_dir: Path, *, failure: str | None = None) -> dict[str, Any]:
    """Exercise the actual fixed-six durable broker with only local fake replies."""
    if failure not in FAILURES:
        raise RehearsalError("REHEARSAL_FAILURE_MODE")
    if evidence_dir.exists() or evidence_dir.is_symlink():
        raise RehearsalError("REHEARSAL_OUTPUT_EXISTS")
    fake = _FakeUpstream(failure)
    # The socket constructor is also denied: a regression cannot call a provider
    # accidentally. There is no injectable user callback or transport argument.
    with patch.object(gate, "time", _Clock), patch("socket.socket", side_effect=
            RehearsalError("REHEARSAL_NETWORK_FORBIDDEN")):
        result = shadow.execute(fake, "8848ac33623167cb2c2a58b1e0122abe825810dc",
                                evidence_dir, window=gate.RunWindow(shadow_review=True))
    # The reused real ledger has simulated transitions. Label EVERY detached row
    # as fake, not merely the folder/summary, after the durable broker has closed.
    for name in FILES[1:]:
        path = evidence_dir / name
        if path.is_file():
            rows = [json.loads(line) for line in path.read_bytes().splitlines()]
            _write(path, b"".join(gate.encode({"evidence_kind": EVIDENCE_KIND, **row}) + b"\n"
                                   for row in rows))
    result.update(evidence_kind=EVIDENCE_KIND, provider_calls=0, simulated_calls=fake.calls,
                  real_prior_calls=48, real_prior_settled_micro_cny=411934,
                  production_protection_verified=False, live_allowed=False,
                  simulation_wall_time=_Clock.time())
    _write(evidence_dir / "summary.json", gate.encode(result) + b"\n")
    return result


def evidence_manifest(evidence_dir: Path) -> dict[str, str]:
    """No glob: exclude unrelated files, credentials, caches and source trees."""
    result = {}
    for name in FILES:
        path = evidence_dir / name
        if path.is_symlink() or not path.is_file():
            raise RehearsalError("REHEARSAL_EVIDENCE_MISSING")
        raw = path.read_bytes()
        if len(raw) > 2_000_000:
            raise RehearsalError("REHEARSAL_EVIDENCE_LIMIT")
        values = [json.loads(raw)] if name.endswith(".json") else [
            json.loads(line) for line in raw.splitlines()]
        if any(not isinstance(row, dict) or row.get("evidence_kind") != EVIDENCE_KIND
               for row in values):
            raise RehearsalError("REHEARSAL_EVIDENCE_UNLABELED")
        result[name] = hashlib.sha256(raw).hexdigest()
    return result
