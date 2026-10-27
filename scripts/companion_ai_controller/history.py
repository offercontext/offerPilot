"""Fail-closed rehearsal history contract and real four-archive accounting."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_validation import continuation as old
from scripts.companion_ai_validation import shadow_continuation as fourth

LEGACY_RUNS = (old.ORIGINAL_RUN_ID, old.SECOND_RUN_ID, old.PREDECESSOR_RUN_ID,
               fourth.PREDECESSOR_RUN_ID)
LEGACY_HASHES = (old.ORIGINAL_ARCHIVE_SHA256, old.SECOND_ARCHIVE_SHA256,
                 old.ARCHIVE_SHA256, fourth.ARCHIVE_SHA256)


@dataclass(frozen=True)
class Balance:
    calls: int = p.PRIOR_CALLS
    settled_micro_cny: int = p.PRIOR_SPEND
    reserved_micro_cny: int = 0


def verified_archives() -> Balance:
    """Trusted installed controller modules only, not modules supplied by candidates."""
    try:
        predecessor = fourth.verified_predecessor()
    except (old.ContinuationError, OSError, ValueError):
        raise p.Blocked("LEGACY_ARCHIVE_INVALID") from None
    p.demand((predecessor.request_count, predecessor.settled_micro_cny,
              predecessor.reserved_micro_cny) == (p.PRIOR_CALLS, p.PRIOR_SPEND, 0),
             "LEGACY_BALANCE_MISMATCH")
    return Balance()


def verify_history(value: Any, *, registration: p.Registration, current_run: int, phase: str = "before") -> Balance:
    p.demand(phase in ("before", "paid"), "PHASE_INVALID")
    p.demand(isinstance(value, Mapping), "HISTORY_INVALID")
    p.demand(set(value) == {"legacy_checkpoints", "legacy_scan", "controller_runs", "dry_runs"},
             "HISTORY_INVALID")
    p.demand(p.canonical(value["legacy_checkpoints"]) == p.canonical([
        {"run_id": run, "archive_sha256": digest}
        for run, digest in zip(LEGACY_RUNS, LEGACY_HASHES, strict=True)]), "LEGACY_CHAIN_INVALID")
    # This is an explicitly synthetic platform scan, never a substitute for the
    # future production API pagination/identity verification adapter.
    scan = value["legacy_scan"]
    p.demand(isinstance(scan, dict) and set(scan) == {"mode", "complete", "unknown_paid_runs", "active_runs"}
             and p.canonical(scan) == p.canonical({"mode": "synthetic_platform", "complete": True,
                          "unknown_paid_runs": [], "active_runs": []}), "LEGACY_SCAN_UNRESOLVED")
    seen: set[int] = set()
    current_seen = False
    for key, expected_workflow in (("controller_runs", registration.workflow_id),
                                    ("dry_runs", registration.dry_run_workflow_id)):
        rows = value[key]
        p.demand(isinstance(rows, list), "HISTORY_INVALID")
        for number, row in enumerate(rows, 1):
            p.demand(isinstance(row, dict) and set(row) == {
                "id", "number", "workflow_id", "attempt", "kind", "status", "paid_started",
                "batch_key", "controller_sha"}, "RUN_SCHEMA_INVALID")
            p.demand(type(row["id"]) is int and row["id"] > 0 and row["id"] not in seen
                     and type(row["number"]) is int and row["number"] == number
                     and type(row["workflow_id"]) is int and row["workflow_id"] == expected_workflow
                     and type(row["attempt"]) is int and row["attempt"] == 1
                     and row["controller_sha"] == registration.controller_sha,
                     "RUN_IDENTITY_OR_HISTORY_GAP")
            seen.add(row["id"])
            p.demand(type(row["paid_started"]) is bool and row["status"] in
                     ("completed", "in_progress", "queued", "waiting", "cancelled"), "RUN_STATE_INVALID")
            if key == "dry_runs":
                p.demand(row["kind"] == "dry_run" and not row["paid_started"]
                         and row["batch_key"] is None and row["status"] == "completed",
                         "DRY_RUN_IS_NOT_PAID_EXEMPTION")
            elif row["id"] == current_run:
                p.demand(number == len(rows) and row["kind"] == "controller"
                         and row["status"] == "in_progress" and row["paid_started"] is (phase == "paid")
                         and row["batch_key"] == p.BATCH_KEY, "CURRENT_RUN_INVALID")
                current_seen = True
            else:
                # Any previous real paid start consumes the attempt, even a
                # different nonce/key. Missing artifacts never release money.
                p.demand(not row["paid_started"], "PAID_STARTED_GLOBAL_HALT")
                p.demand(row["status"] in ("completed", "cancelled"), "PRIOR_RUN_UNRESOLVED")
                p.demand((row["kind"] == "master_stub" and row["batch_key"] is None)
                         or (row["kind"] == "controller" and row["batch_key"] == p.BATCH_KEY),
                         "UNKNOWN_HISTORY_SCHEMA")
    p.demand(current_seen, "CURRENT_RUN_MISSING")
    return verified_archives()
