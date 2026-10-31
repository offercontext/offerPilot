"""Read-only refresh journal/cache audit; the product pointer is independent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from offerpilot.evaluation.core_refresh_contracts import (
    GenerationRecord,
    RefreshRevocation,
    bound_file,
    identifier,
    maintainer_record,
    read,
    require,
)
from offerpilot.evaluation.registry import digest

REFRESH = "evaluator_evidence_refresh"
REVOKE = "revoke_refresh"


def generation_state(store: Path, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate committed generations and exact caches; never repair partial state."""
    heads: dict[str, dict[str, Any]] = {}
    records: dict[str, GenerationRecord] = {}
    revoked: set[str] = set()
    current = None
    last_commit: datetime | None = None
    for event in events:
        action = event["action"]
        baseline = identifier(event["baseline_id"])
        if action == "promote":
            current = baseline
            continue
        if action == "revoke":
            if current == baseline:
                current = None
            continue
        if action not in {REFRESH, REVOKE}:
            raise ValueError("unknown baseline generation event; audit required")
        require(current == baseline, "generation targets a noncurrent product; audit required")
        prior = heads.get(baseline, {}).get("generation_id")
        require(
            event.get("previous_generation") == prior,
            "generation CAS chain changed; audit required",
        )
        generation = identifier(event["generation_id"])
        path = store / "evaluator-refreshes" / (generation + ".json")
        record = GenerationRecord.model_validate(read(path))
        require(
            record.generation_id == generation
            and record.baseline_id == baseline
            and digest(record.model_dump(mode="json")) == event["generation_record_hash"]
            and path.with_suffix(".reservation").read_text(encoding="utf-8")
            == event["generation_record_hash"]
            and record.original_record_digest == event["record_hash"],
            "generation record differs; audit required",
        )
        request = read(bound_file(record.request))
        require(
            request.get("request_hash") == record.request_hash
            and digest(request.get("request")) == record.request_hash,
            "generation request changed; audit required",
        )
        payload = request["request"]
        requested_at = datetime.fromisoformat(payload["created_at"])
        require(
            payload.get("schema_version") == 3
            and payload.get("kind") == "evaluator-evidence-refresh-request"
            and payload.get("baseline_id") == baseline
            and payload.get("generation_id") == generation
            and payload.get("source_variant") == "baseline"
            and payload.get("activatable") is True
            and Path(payload["store"]).is_absolute()
            and Path(payload["store"]).resolve() == store.resolve()
            and payload["original_record_digest"] == record.original_record_digest
            and payload["expected_current"] == record.expected_current
            and payload["expected_previous_generation"] == record.expected_previous_generation
            and requested_at.tzinfo is not None
            and requested_at <= record.created_at,
            "generation request identity differs; audit required",
        )
        require(
            read(bound_file(record.approval_file)) == record.approval.model_dump(mode="json"),
            "generation approval file changed; audit required",
        )
        maintainer_record(record.approval, record.created_at)
        require(
            record.approval.baseline_id == baseline
            and record.approval.generation_id == generation
            and record.approval.request_hash == record.request_hash
            and record.approval.expected_current == record.expected_current
            and record.approval.expected_previous_generation == record.expected_previous_generation,
            "generation approval binding changed; audit required",
        )
        committed_at = datetime.fromisoformat(event["committed_at"])
        require(
            committed_at.tzinfo is not None
            and committed_at <= datetime.now(committed_at.tzinfo)
            and (last_commit is None or last_commit <= committed_at),
            "generation commit order invalid; audit required",
        )
        last_commit = committed_at
        if action == REFRESH:
            require(
                committed_at.tzinfo is not None
                and record.created_at <= committed_at
                and committed_at <= datetime.now(committed_at.tzinfo),
                "invalid generation commit time; audit required",
            )
            maintainer_record(record.approval, committed_at)
            require(
                generation not in records
                and generation not in revoked
                and record.expected_current == current
                and record.expected_previous_generation == prior,
                "generation reused or CAS binding changed; audit required",
            )
            records[generation] = record
            heads[baseline] = dict(
                baseline_id=baseline, generation_id=generation, event_sequence=event["sequence"]
            )
        else:
            require(
                generation in records and generation not in revoked,
                "generation revocation transition invalid; audit required",
            )
            revocation = RefreshRevocation.model_validate(event["revocation"])
            maintainer_record(revocation, committed_at)
            require(
                revocation.baseline_id == baseline
                and revocation.generation_id == generation
                and revocation.request_hash == record.request_hash
                and revocation.expected_current == current
                and revocation.expected_previous_generation == prior,
                "generation revocation binding changed; audit required",
            )
            revoked.add(generation)
            heads[baseline] = dict(
                baseline_id=baseline,
                generation_id=None if generation == prior else prior,
                event_sequence=event["sequence"],
            )
    folder = store / "evaluator-refreshes"
    expected = {
        folder / (name + suffix) for name in records for suffix in (".json", ".reservation")
    }
    actual = {p for p in folder.iterdir()} if folder.exists() else set()
    require(actual == expected, "partial/orphan generation files; audit required")
    cache = store / "evidence-generations"
    expected_cache = {cache / (name + ".json") for name in heads}
    require(
        ({p for p in cache.iterdir()} if cache.exists() else set()) == expected_cache,
        "generation cache missing/partial; audit required",
    )
    for baseline, value in heads.items():
        require(
            read(cache / (baseline + ".json")) == value, "generation cache differs; audit required"
        )
    return heads
