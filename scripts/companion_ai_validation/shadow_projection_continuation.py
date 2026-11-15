"""Fixed input-projection batch inheriting six immutable historical batches.

This lineage never resumes either failed review batch or erases its cost. The
sixth batch remains a consumed failure at s03 with only two completed stages.
All public lineage verification re-reads and verifies the six actual archives.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.shadow_input_projection import sixth_checkpoint as checkpoint
from scripts.companion_ai_validation import continuation as archive
from scripts.companion_ai_validation import shadow_quote_continuation as old

ADMISSION_SCHEMA = old.ADMISSION_SCHEMA
LINEAGE_KEYS = old.LINEAGE_KEYS
CAMPAIGN_ID = old.CAMPAIGN_ID
CORRECTION_ORDINAL = 6
BUDGET = 20_000_000
RESERVATION = 3_000_000
PRIOR_SETTLED = 419_388
PRIOR_REQUESTS = 52
PRICE_VALID_UNTIL = 1793577600  # 2026-11-02 00:00 UTC; never extended by replay.
OPERATIONS = ("s01", "s02", "s03", "s04", "s05", "s06")
OPERATIONS_SHA256 = old.OPERATIONS_SHA256
PREDECESSOR_SOURCE_SHA = checkpoint.SOURCE_SHA
PREDECESSOR_HELPER_SHA = checkpoint.HELPER_SHA
PREDECESSOR_RUN_ID = checkpoint.RUN_ID
PREDECESSOR_HELPER_RUN_ID = checkpoint.HELPER_RUN_ID
PREDECESSOR_TREE_SHA = checkpoint.TREE_SHA
PREDECESSOR_ARTIFACT_ID = checkpoint.ARTIFACT_ID
ARCHIVE_SHA256 = checkpoint.ARCHIVE_SHA256
ARCHIVE_SIZE = checkpoint.ARCHIVE_SIZE
MEMBERS = checkpoint.MEMBERS
LEDGER_SHA256 = MEMBERS["budget.jsonl"][1]
EVIDENCE_PATH = (Path(__file__).resolve().parents[2] / "experiments/shadow_input_projection"
                 / "fixtures/paid-original-artifact.zip")
ContinuationError = archive.ContinuationError
ALL_HISTORICAL_IDENTITIES = old.ALL_HISTORICAL_IDENTITIES | frozenset((
    PREDECESSOR_SOURCE_SHA, PREDECESSOR_HELPER_SHA,
))


@dataclass(frozen=True)
class Predecessor:
    settled_micro_cny: int = PRIOR_SETTLED
    reserved_micro_cny: int = 0
    request_count: int = PRIOR_REQUESTS
    source_sha: str = PREDECESSOR_SOURCE_SHA
    helper_sha: str = PREDECESSOR_HELPER_SHA
    run_id: int = PREDECESSOR_RUN_ID
    artifact_id: int = PREDECESSOR_ARTIFACT_ID
    archive_sha256: str = ARCHIVE_SHA256
    ledger_sha256: str = LEDGER_SHA256


def verified_predecessor() -> Predecessor:
    # This verifies all five prior archives and preserves their failure records.
    previous = old.verified_predecessor()
    try:
        checkpoint.equal((previous.request_count, previous.settled_micro_cny,
                          previous.reserved_micro_cny), (49, 414284, 0))
        checkpoint.equal(old.lineage_fields(), checkpoint.HISTORICAL_LINEAGE)
        terminal = checkpoint.verify_archive(archive._read_evidence(EVIDENCE_PATH, ARCHIVE_SIZE))
        checkpoint.equal((terminal.status, terminal.resumable, terminal.authorizes_execution,
                          terminal.actual_calls, terminal.lifetime_calls,
                          terminal.settled_micro_cny, terminal.reserved_micro_cny,
                          terminal.stages_complete, terminal.error_code),
                         ("consumed_failed", False, False, 3, 52, 419388, 0, 2,
                          "shadow_quote_quote_not_unique"))
    except checkpoint.CheckpointError:
        raise ContinuationError("PREDECESSOR_EVIDENCE_INVALID") from None
    return Predecessor(settled_micro_cny=terminal.settled_micro_cny,
                       reserved_micro_cny=terminal.reserved_micro_cny,
                       request_count=terminal.lifetime_calls)


def lineage_fields() -> dict[str, Any]:
    previous = verified_predecessor()
    return {
        "campaign_id": CAMPAIGN_ID, "correction_ordinal": CORRECTION_ORDINAL,
        "predecessor_run_id": previous.run_id, "predecessor_artifact_id": previous.artifact_id,
        "predecessor_source_sha": previous.source_sha, "predecessor_helper_sha": previous.helper_sha,
        "predecessor_archive_sha256": previous.archive_sha256,
        "predecessor_ledger_sha256": previous.ledger_sha256,
        "prior_settled_micro_cny": previous.settled_micro_cny,
        "prior_reserved_micro_cny": previous.reserved_micro_cny,
        "prior_request_count": previous.request_count, "budget_micro_cny": BUDGET,
        "operations_sha256": OPERATIONS_SHA256, "price_valid_until": PRICE_VALID_UNTIL,
    }


def validate_lineage(record: Mapping[str, Any]) -> None:
    archive._demand(isinstance(record, Mapping) and archive._exact(record, lineage_fields()),
                    "CONTINUATION_LINEAGE_MISMATCH")
