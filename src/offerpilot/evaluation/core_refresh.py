"""Explicit baseline evidence generations; original approvals and product IDs are immutable."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any

from offerpilot.evaluation import core_governance as gov
from offerpilot.evaluation.core_contracts import RunManifestV2, TrialResultV2
from offerpilot.evaluation.core_planning import read_plan
from offerpilot.evaluation.core_refresh_contracts import (
    BoundFile,
    GenerationRecord,
    RefreshApproval,
    RefreshIndependentRecord,
    RefreshRevocation,
    bound_file,
    identifier,
    maintainer_record,
    read,
    reference,
    require,
    valid_at,
)
from offerpilot.evaluation.core_refresh_history import audit_history
from offerpilot.evaluation.core_refresh_store import REFRESH, REVOKE, generation_state
from offerpilot.evaluation.core_refresh_sources import source_origins
from offerpilot.evaluation.core_runner import read_run
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import ROOT, evaluator_hash


def strict_state(store: Path) -> tuple[str | None, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Unlike legacy recover_current, this path never repairs any cache or partial write."""
    store = safe_path(store.absolute())
    require(store.is_dir(), "original baseline store unavailable")
    for name in ("records", "events", "evaluator-refreshes", "evidence-generations"):
        folder = store / name
        if folder.exists():
            safe_path(folder)
            for item in folder.iterdir():
                require(safe_path(item).is_file(), "partial baseline namespace; audit required")
    current, events = gov.current_from_journal(store)
    require(
        read(store / "current.json") == dict(baseline_id=current),
        "product current cache differs; audit required",
    )
    promoted = {identifier(e["baseline_id"]) for e in events if e["action"] == "promote"}
    expected = {
        store / "records" / (name + suffix)
        for name in promoted
        for suffix in (".json", ".reservation")
    }
    actual = set((store / "records").iterdir()) if (store / "records").exists() else set()
    require(actual == expected, "partial/orphan product records; audit required")
    require(
        all(p.suffix == ".json" for p in (store / "events").iterdir()),
        "partial/unknown journal files; audit required",
    )
    return current, events, generation_state(store, events)


def evidence_fingerprint(manifest: RunManifestV2, rows: list[TrialResultV2]) -> str:
    """Full identities, evidence and raw outcomes; candidate failures remain part of the proof."""
    return digest(
        dict(
            manifest=digest(manifest.model_dump(mode="json")),
            rows=[r.model_dump(mode="json") for r in rows],
        )
    )


def fresh_run(
    directory: Path, original: dict[str, Any]
) -> tuple[RunManifestV2, list[TrialResultV2]]:
    manifest, rows = read_run(directory)  # Current strict reader, never a hash waiver.
    plan = read_plan(directory / "run-plan.json", check_targets=False)
    require(
        digest(plan.model_dump(mode="json")) == digest(manifest.model_dump(mode="json")),
        "refresh executed manifest differs from its frozen plan",
    )
    require(
        manifest.mode == "deterministic"
        and manifest.repeats == 3
        and manifest.provider_profile is None
        and manifest.network_policy == "trial-loopback"
        and set(manifest.variants) == {"baseline", "candidate"}
        and len(manifest.planned_trials) == 114
        and len(rows) == 114
        and manifest.evaluator_hash == evaluator_hash(),
        "refresh requires the full frozen 114 plan/results",
    )
    require(
        Path(manifest.variants["baseline"]["checkout"]).resolve()
        != Path(manifest.variants["candidate"]["checkout"]).resolve(),
        "refresh checkouts must differ",
    )
    require(
        gov.product_hash(manifest, "baseline") == original["product_snapshot_hash"],
        "refresh baseline arm is not the approved product",
    )
    expected = {
        (p.execution_item_id, p.repeat_index)
        for p in manifest.planned_trials
        if p.variant_id == "baseline"
    }
    require(
        len(expected) == 57
        and len({item for item, _ in expected}) == 19
        and {n for _, n in expected} == {1, 2, 3},
        "refresh denominator differs",
    )
    for variant in ("baseline", "candidate"):
        require(
            {(r.execution_item_id, r.repeat_index) for r in rows if r.variant_id == variant}
            == expected,
            "refresh arm missing or foreign results",
        )
        require(
            manifest.variants[variant].get("fault") == "none",
            "diagnostic fault cannot refresh baseline",
        )
    return manifest, rows


def independent_reviews(
    bundle: gov.ReviewBundle, manifest: RunManifestV2, fingerprint: str, at: datetime
) -> bool:
    require(
        not bundle.adjudications and not bundle.exceptions,
        "refresh cannot migrate human labels or exceptions",
    )
    accepted = False
    for review in bundle.independent_reviews:
        record = RefreshIndependentRecord.model_validate(
            read(bound_file(dict(path=review.record_ref, sha256=review.record_hash)))
        )
        require(
            record.reviewer == review.reviewer
            and record.disposition == review.disposition
            and record.created_at == review.created_at
            and record.expires_at == review.expires_at
            and record.baseline_snapshot_hash
            == review.candidate_snapshot_hash
            == gov.product_hash(manifest, "baseline")
            and record.candidate_snapshot_hash == gov.product_hash(manifest)
            and record.evaluator_hash == review.evaluator_hash == manifest.evaluator_hash
            and record.run_manifest_hash == digest(manifest.model_dump(mode="json"))
            and record.evidence_fingerprint == fingerprint,
            "independent refresh review is not bound to this new run and baseline arm",
        )
        if valid_at(record.created_at, record.expires_at, at) and record.disposition == "accepted":
            accepted = True
    return accepted


def qualification(
    directory: Path,
    original: dict[str, Any],
    policy_path: Path,
    review_path: Path,
    experiment_path: Path,
    at: datetime,
) -> dict[str, Any]:
    manifest, rows = fresh_run(directory, original)
    policy = gov.GatePolicyV2.model_validate(read(policy_path))
    canonical = gov.GatePolicyV2.model_validate(
        read(ROOT / "evals/policies/core-initial-baseline-v5.json")
    )
    require(policy == canonical, "refresh requires the unchanged formal initial-baseline-v5 policy")
    bundle = gov.ReviewBundle.model_validate(read(review_path))
    fingerprint = evidence_fingerprint(manifest, rows)
    reviewed = independent_reviews(bundle, manifest, fingerprint, at)
    experiment = gov.ExperimentSpec.model_validate(read(experiment_path))
    comparison = gov.compare_core(
        manifest, rows, manifest, rows, experiment, baseline_variant="baseline"
    )
    require(comparison["exit_code"] != 2, "refresh comparison variables are not declared")
    origins = source_origins(manifest, rows, directory)
    gate = gov.gate_core(
        [manifest],
        [rows],
        policy,
        bundle,
        "initial_baseline",
        "enforce",
        candidate_variant="baseline",
        review_at=at,
    )
    gate["sources"][0]["directory"] = str(directory.resolve())
    baseline_rows = [r for r in rows if r.variant_id == "baseline"]
    complete = all(r.execution_status == "completed" for r in rows)
    hard_pass = all(
        r.verdict == "pass" and all(v.status == "pass" for v in r.rules) for r in baseline_rows
    )
    return dict(
        manifest_hash=digest(manifest.model_dump(mode="json")),
        baseline_product_hash=gov.product_hash(manifest, "baseline"),
        candidate_product_hash=gov.product_hash(manifest),
        evaluator_hash=manifest.evaluator_hash,
        fingerprint=fingerprint,
        origins=origins,
        gate=gate,
        comparison=comparison,
        activatable=bool(complete and hard_pass and reviewed and gate["gate_satisfied"]),
        source_variant="baseline",
        all114_completed=complete,
        baseline57_hard_pass=hard_pass,
    )


def prepare_refresh(
    store: Path,
    baseline_id: str,
    generation_id: str,
    directory: Path,
    policy: Path,
    review: Path,
    experiment: Path,
    history: Path,
    output: Path,
) -> dict[str, Any]:
    identifier(baseline_id)
    identifier(generation_id)
    store = safe_path(store.absolute())
    current, events, heads = strict_state(store)
    require(current == baseline_id, "refresh requires the current approved product")
    require(
        not any(e.get("generation_id") == generation_id for e in events),
        "generation id already used",
    )
    original = gov.load_baseline(store, baseline_id)
    require(original["evaluator_hash"] != evaluator_hash(), "no evaluator refresh is needed")
    old_manifest = read(Path(original["run_directory"]) / "run-manifest.json")
    require(digest(old_manifest) == original["manifest_hash"], "original manifest changed")
    old_checkout = safe_path(Path(old_manifest["variants"]["candidate"]["checkout"]))
    output = safe_path(output.absolute())
    require(
        not output.exists()
        and not any(
            output.is_relative_to(p.resolve())
            for p in (store, old_checkout, Path(original["run_directory"]))
        ),
        "refresh output must be new and outside approved store/checkout/raw",
    )
    output.mkdir(parents=True)
    historical = audit_history(store, original, history, output)
    at = datetime.now(timezone.utc)
    measured = qualification(directory, original, policy, review, experiment, at)
    atomic_json(output / "historical-proof.json", historical)
    atomic_json(output / "measurement.json", measured)
    atomic_json(output / "baseline-qualification-gate.json", measured["gate"])
    request = dict(
        schema_version=3,
        kind="evaluator-evidence-refresh-request",
        baseline_id=baseline_id,
        generation_id=generation_id,
        source_variant="baseline",
        store=str(store),
        expected_current=current,
        expected_previous_generation=heads.get(baseline_id, {}).get("generation_id"),
        created_at=at.isoformat(),
        original_record=reference(store / "records" / (baseline_id + ".json")),
        original_record_digest=digest(original),
        original_product_hash=original["product_snapshot_hash"],
        old_evaluator_hash=original["evaluator_hash"],
        evaluator_hash=measured["evaluator_hash"],
        candidate_product_hash=measured["candidate_product_hash"],
        run_directory=str(directory.resolve()),
        manifest_hash=measured["manifest_hash"],
        source_origins_hash=digest(measured["origins"]),
        evidence_fingerprint=measured["fingerprint"],
        raw_files={
            name: reference(directory / name)
            for name in (
                "run-plan.json",
                "run-manifest.json",
                "trial-results.json",
                "summary.json",
                "source-files.json",
            )
        },
        policy=reference(policy),
        review=reference(review),
        experiment=reference(experiment),
        gate=reference(output / "baseline-qualification-gate.json"),
        history_descriptor=reference(history),
        historical_proof=reference(output / "historical-proof.json"),
        measurement=reference(output / "measurement.json"),
        activatable=measured["activatable"],
        release_eligible=False,
    )
    require(strict_state(store) == (current, events, heads), "store changed during preparation")
    envelope = dict(request=request, request_hash=digest(request))
    atomic_json(output / "refresh-request.json", envelope)
    return envelope


def check_request(
    path: Path, at: datetime
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    envelope = read(path)
    require(
        set(envelope) == {"request", "request_hash"}
        and digest(envelope["request"]) == envelope["request_hash"],
        "frozen refresh request checksum differs",
    )
    request = envelope["request"]
    require(
        request.get("schema_version") == 3
        and request.get("kind") == "evaluator-evidence-refresh-request"
        and request.get("source_variant") == "baseline"
        and request.get("release_eligible") is False,
        "not a frozen evaluator refresh request",
    )
    require(Path(request["store"]).is_absolute(), "refresh store must be absolute")
    store = safe_path(Path(request["store"]))
    identifier(request["generation_id"])
    requested_at = datetime.fromisoformat(request["created_at"])
    require(
        requested_at.tzinfo is not None
        and requested_at <= at
        and request["expected_current"] == request["baseline_id"],
        "request time or product CAS invalid",
    )
    original = gov.load_baseline(store, identifier(request["baseline_id"]))
    require(
        bound_file(request["original_record"])
        == store / "records" / (request["baseline_id"] + ".json")
        and digest(original) == request["original_record_digest"]
        and original["product_snapshot_hash"] == request["original_product_hash"]
        and original["evaluator_hash"] == request["old_evaluator_hash"]
        and request["evaluator_hash"] == evaluator_hash(),
        "refresh original/evaluator binding changed",
    )
    historical = audit_history(store, original, bound_file(request["history_descriptor"]))
    old_proof = read(bound_file(request["historical_proof"]))
    require(
        historical["proof"] == old_proof["proof"]
        and historical["child"] == old_proof["child"]
        and historical["descriptor"] == old_proof["descriptor"],
        "historical proof identity changed",
    )
    for name, value in request["raw_files"].items():
        require(
            bound_file(value) == Path(request["run_directory"]) / name, "refresh raw file moved"
        )
    require(
        set(request["raw_files"])
        == {
            "run-plan.json",
            "run-manifest.json",
            "trial-results.json",
            "summary.json",
            "source-files.json",
        },
        "refresh raw file binding missing",
    )
    measured = qualification(
        Path(request["run_directory"]),
        original,
        bound_file(request["policy"]),
        bound_file(request["review"]),
        bound_file(request["experiment"]),
        at,
    )
    saved = read(bound_file(request["measurement"]))
    saved_gate = read(bound_file(request["gate"]))
    checked_gate = measured["gate"] | {k: saved_gate[k] for k in ("gate_id", "created_at")}
    require(
        saved["gate"] == saved_gate
        and checked_gate == saved_gate
        and measured["manifest_hash"] == request["manifest_hash"]
        and measured["fingerprint"] == request["evidence_fingerprint"]
        and digest(measured["origins"]) == request["source_origins_hash"]
        and measured["candidate_product_hash"] == request["candidate_product_hash"]
        and measured["activatable"] is True
        and request["activatable"] is True,
        "new raw qualification or independent review no longer supports activation",
    )
    return request, original, measured


def activate_refresh(request_path: Path, approval_path: Path) -> dict[str, Any]:
    # Validate explicit approval before touching a lock or any store file.
    approval_reference = reference(approval_path)
    approval = RefreshApproval.model_validate(read(approval_path))
    require(reference(approval_path) == approval_reference, "approval changed during read")
    at = datetime.now(timezone.utc)
    maintainer_record(approval, at)
    request_reference = reference(request_path)
    envelope = read(request_path)
    require(reference(request_path) == request_reference, "request changed during read")
    require(
        envelope.get("request_hash") == approval.request_hash,
        "approval is for a different frozen request",
    )
    store = safe_path(Path(envelope["request"]["store"]))
    strict_state(store)
    with gov.store_lock(store):
        current, events, heads = strict_state(store)
        request, original, measured = check_request(request_path, datetime.now(timezone.utc))
        previous = heads.get(approval.baseline_id, {}).get("generation_id")
        require(
            current
            == approval.expected_current
            == approval.baseline_id
            == request["expected_current"]
            == request["baseline_id"]
            and previous
            == approval.expected_previous_generation
            == request["expected_previous_generation"]
            and approval.generation_id == request["generation_id"],
            "refresh compare-and-swap conflict",
        )
        require(
            not any(e.get("generation_id") == approval.generation_id for e in events),
            "generation id already used",
        )
        manifest = read_run(Path(request["run_directory"]))[0]

        def frozen_inputs() -> None:
            require(
                reference(request_path) == request_reference
                and reference(approval_path) == approval_reference,
                "refresh input changed during validation",
            )
            for key in (
                "original_record",
                "policy",
                "review",
                "experiment",
                "gate",
                "history_descriptor",
                "historical_proof",
                "measurement",
            ):
                bound_file(request[key])
            for value in request["raw_files"].values():
                bound_file(value)

        frozen_inputs()
        before_record = datetime.now(timezone.utc)
        maintainer_record(approval, before_record)
        review_bundle = gov.ReviewBundle.model_validate(read(bound_file(request["review"])))
        require(
            independent_reviews(review_bundle, manifest, measured["fingerprint"], before_record),
            "review expired before record write",
        )
        record = GenerationRecord(
            baseline_id=approval.baseline_id,
            generation_id=approval.generation_id,
            original_record_digest=digest(original),
            expected_current=approval.baseline_id,
            expected_previous_generation=previous,
            created_at=datetime.now(timezone.utc),
            request=BoundFile.model_validate(request_reference),
            request_hash=approval.request_hash,
            approval_file=BoundFile.model_validate(approval_reference),
            approval=approval,
        )
        value = record.model_dump(mode="json")
        folder = store / "evaluator-refreshes"
        folder.mkdir(exist_ok=True)
        path = folder / (approval.generation_id + ".json")
        with path.with_suffix(".reservation").open("x", encoding="utf-8") as stream:
            stream.write(digest(value))
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json(path, value)
        frozen_inputs()
        committed_at = datetime.now(timezone.utc)
        require(
            record.created_at <= committed_at
            and all(
                datetime.fromisoformat(e["committed_at"]) <= committed_at
                for e in events
                if "committed_at" in e
            ),
            "refresh commit clock moved backwards; audit required",
        )
        maintainer_record(approval, committed_at)
        require(
            independent_reviews(
                gov.ReviewBundle.model_validate(read(bound_file(request["review"]))),
                manifest,
                measured["fingerprint"],
                committed_at,
            ),
            "review expired before journal commit",
        )
        sequence = len(events) + 1
        gov.append_store_event(
            store,
            dict(
                action=REFRESH,
                previous=current,
                baseline_id=current,
                record_hash=digest(original),
                generation_id=approval.generation_id,
                generation_record_hash=digest(value),
                previous_generation=previous,
                committed_at=committed_at.isoformat(),
            ),
        )
        atomic_json(
            store / "evidence-generations" / (approval.baseline_id + ".json"),
            dict(
                baseline_id=current, generation_id=approval.generation_id, event_sequence=sequence
            ),
        )
        strict_state(store)
        return value


def resolve_baseline(store: Path, baseline_id: str) -> dict[str, Any]:
    """The only approved-evidence view used by release, CLI gate and promotion."""
    current, events, heads = strict_state(store)
    require(current == baseline_id, "approved product is not current")
    original = gov.load_baseline(store, baseline_id)
    if original["evaluator_hash"] == evaluator_hash():
        return original
    generation = heads.get(baseline_id, {}).get("generation_id")
    require(
        bool(generation),
        "approved baseline needs a specifically approved evaluator evidence generation",
    )
    record_path = store / "evaluator-refreshes" / (str(generation) + ".json")
    record = GenerationRecord.model_validate(read(record_path))
    event = next(e for e in events if e["action"] == REFRESH and e["generation_id"] == generation)
    request, _, measured = check_request(
        bound_file(record.request), datetime.fromisoformat(event["committed_at"])
    )
    require(strict_state(store) == (current, events, heads), "baseline changed while resolving")
    return original | dict(
        evaluator_hash=request["evaluator_hash"],
        source_variant="baseline",
        sources=measured["gate"]["sources"],
        run_directory=request["run_directory"],
        manifest_hash=request["manifest_hash"],
        gate_path=str(bound_file(request["gate"])),
        gate_hash=digest(read(bound_file(request["gate"]))),
        original_record_digest=digest(original),
        refresh_generation=dict(
            generation_id=generation,
            record_hash=digest(record.model_dump(mode="json")),
            request_hash=record.request_hash,
            committed_at=event["committed_at"],
        ),
    )


def revoke_refresh(store: Path, revocation_path: Path) -> dict[str, Any]:
    revocation = RefreshRevocation.model_validate(read(revocation_path))
    maintainer_record(revocation, datetime.now(timezone.utc))
    strict_state(store)
    with gov.store_lock(store):
        current, events, heads = strict_state(store)
        prior = heads.get(revocation.baseline_id, {}).get("generation_id")
        require(
            current == revocation.baseline_id == revocation.expected_current
            and prior == revocation.expected_previous_generation,
            "revocation compare-and-swap conflict",
        )
        record = GenerationRecord.model_validate(
            read(store / "evaluator-refreshes" / (revocation.generation_id + ".json"))
        )
        original = gov.load_baseline(store, revocation.baseline_id)
        require(
            record.baseline_id == revocation.baseline_id
            and record.generation_id == revocation.generation_id
            and record.original_record_digest == digest(original)
            and any(
                e["action"] == REFRESH
                and e["baseline_id"] == revocation.baseline_id
                and e["generation_id"] == revocation.generation_id
                and e["generation_record_hash"] == digest(record.model_dump(mode="json"))
                for e in events
            )
            and record.request_hash == revocation.request_hash
            and not any(
                e["action"] == REVOKE and e["generation_id"] == revocation.generation_id
                for e in events
            ),
            "revocation target differs or already revoked",
        )
        committed_at = datetime.now(timezone.utc)
        require(
            all(
                datetime.fromisoformat(e["committed_at"]) <= committed_at
                for e in events
                if "committed_at" in e
            ),
            "revocation commit clock moved backwards",
        )
        maintainer_record(revocation, committed_at)
        gov.append_store_event(
            store,
            dict(
                action=REVOKE,
                previous=current,
                baseline_id=current,
                record_hash=digest(original),
                generation_id=revocation.generation_id,
                generation_record_hash=digest(record.model_dump(mode="json")),
                previous_generation=prior,
                committed_at=committed_at.isoformat(),
                revocation=revocation.model_dump(mode="json"),
            ),
        )
        cache = dict(
            baseline_id=current,
            generation_id=None if prior == revocation.generation_id else prior,
            event_sequence=len(events) + 1,
        )
        atomic_json(store / "evidence-generations" / (revocation.baseline_id + ".json"), cache)
        strict_state(store)
        return cache
