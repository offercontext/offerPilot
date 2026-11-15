"""Allowlisted synthetic P0 diagnostics; never export raw trial files or tokens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from offerpilot.evaluation.reporting import atomic_json

FRAME_TYPES = {"prepared", "ready", "call_attempt", "error", "fault_reached",
               "shutdown_complete", "model_observation", "executor_observation",
               "negative_control_reached"}
ERROR_TYPES = {"TimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
               "ReadError", "ConnectError", "RemoteProtocolError", "DriverError", "ProductFailure",
               "UnsupportedSubject", "ValueError", "RuntimeError", "OSError", "PermissionError",
               "BrokenPipeError", "EOFError", "JSONDecodeError", "AssertionError",
               "CancelledError", "TimeoutExpired"}
OPERATIONS = {"process.poll", "shutdown.send", "shutdown.wait", "tree.wait", "tree.kill",
              "tree.close", "reader.join", "stderr.close", "stdin.close", "stdout.close",
              "subject.stop", "http_driver.close"}
CONTROL_CODES = {"control", "shutdown_timeout", "abnormal_shutdown", "shutdown_failed",
                 "exit_unverified", "kill_failed", "control_not_closed", "tree_close_failed",
                 "stderr_close_failed", "target_snapshot_changed"}
ERROR_KEYS = {"driver", "observer", "control", "cleanup", "snapshot", "timeout_stage"}
LIMIT_KEYS = {"wall_seconds", "model_calls", "tool_attempts", "script_replies", "startup_seconds", "shutdown_seconds"}


def _choice(value, allowed):
    return value if isinstance(value, str) and value in allowed else "redacted"


def _classification(value):
    if not isinstance(value, str):
        return "redacted"
    parts = re.split("[:,]", value)
    return value if parts and all(p in ERROR_TYPES | OPERATIONS | CONTROL_CODES for p in parts) else "redacted"


def _integer(value):
    return value if type(value) is int else None


def _hash(value):
    return value if isinstance(value, str) and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value) else "redacted"


def _route(value):
    if value in {"/api/chat", "/api/chat/confirm"}:
        return value
    if isinstance(value, str) and value.startswith("/api/pilot/runtime/v1/turns"):
        return "/api/pilot/runtime/v1/turns/{redacted}"
    return "redacted"


def export_diagnostics(private: Path, plan, spec, expected, result, evidence):
    destination = os.environ.get("OFFERPILOT_P0_DIAGNOSTICS")
    if not destination:
        return
    control = []
    parse_errors = []
    for path in sorted(private.glob("epoch-*.control.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            parse_errors.append("control_unreadable")
            continue
        for line in lines:
            try:
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ValueError
            except ValueError:
                parse_errors.append("control_invalid_json")
                continue
            control.append({"type": _choice(frame.get("type"), FRAME_TYPES), "seq": _integer(frame.get("seq")),
                            "epoch": _integer(frame.get("epoch")),
                            "error_type": _choice(frame.get("error_type"), ERROR_TYPES)})
    payload = {
        "schema": "p0-safe-diagnostic-v1", "run_id": str(plan.run_id),
        "context": _choice(os.environ.get("OFFERPILOT_P0_DIAGNOSTIC_CONTEXT"), {"isolated", "shard"}),
        "execution_item": _choice(plan.execution_item_id, {"E01.runtime_read", "E04.runtime_edit", "E06.legacy_saved"}),
        "mode": _choice(plan.mode, {"deterministic"}),
        "limits": {k: _integer(v) for k, v in spec.evaluator_limits.model_dump().items() if k in LIMIT_KEYS},
        "source": {key: _hash(expected[key]) for key in ("commit", "diff_hash", "lock_hash", "files_hash")},
        "verdict": _choice(result.verdict, {"pass", "fail", "incomplete"}),
        "execution_status": _choice(result.execution_status, {"completed", "not_run", "setup_error", "eval_error",
                                                               "product_failed", "unsupported", "interrupted"}),
        "errors": {_choice(k, ERROR_KEYS): _classification(v) for k, v in evidence.fragment_errors.items()},
        "driver": [{"method": _choice(row.get("method"), {"GET", "POST"}), "route": _route(row.get("path")),
                    "status": _integer(row.get("status"))} for row in evidence.http],
        "control": control, "diagnostic_errors": parse_errors,
        "completion": {key: evidence.facts.get(key) is True
                       for key in ("driver_completed", "control_closed")},
        "cleanup": [{"operation": _choice(row.get("operation"), OPERATIONS),
                     "error_type": _choice(row.get("error_type"), ERROR_TYPES), "epoch": _integer(row.get("epoch"))}
                    for row in evidence.facts.get("cleanup_errors", [])],
        # Raw stderr, DB, HTTP bodies, confirmation tokens and model content stay private.
        "stderr": [{"epoch": int(match.group(1)), "bytes": _integer(value.get("bytes")), "sha256": _hash(value.get("sha256"))}
                   for name, value in evidence.files.items()
                   if (match := re.fullmatch(r"epoch-(\d+)\.stderr", name))],
    }
    atomic_json(Path(destination) / (str(plan.run_id) + ".json"), payload)
