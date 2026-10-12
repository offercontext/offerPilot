"""Blinded local human review packs and fail-closed judge calibration eligibility."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
from typing import Any
from uuid import uuid4

from offerpilot.evaluation.core_governance import Adjudication, verified_reference
from offerpilot.evaluation.core_runner import read_run
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

RUBRIC = {
    "rubric": "quality-supported-v1",
    "version": 1,
    "dimensions": {
        "relevance": "0 unrelated; 1 partial; 2 addresses request; 3 follows all explicit constraints",
        "grounding": "0 invents/contradicts evidence; 1 weak support; 2 traceable support; 3 precise support with uncertainty",
        "actionability": "0 unusable; 1 vague; 2 useful; 3 concrete and appropriate to requested scope",
        "interaction_burden": "0 excessive/obstructive; 1 avoidable burden; 2 reasonable; 3 concise without omitting necessary clarification",
    },
    "score_range": [0, 3],
    "decision": "Human records pass/fail/abstain with a reason and supporting sentences; dimensions are not an automatic weighted verdict. Safety failures remain blocking.",
}


def blinded_pack(directories: list[Path], output: Path, audit_output: Path) -> dict[str, Any]:
    if (
        output.exists()
        or audit_output.exists()
        or audit_output.resolve().is_relative_to(output.resolve())
        or not directories
        or len({p.resolve() for p in directories}) != len(directories)
    ):
        raise ValueError("review output must be new, audit separate, and runs unique")
    samples, audit, runs = [], [], []
    seen = set()
    for directory in directories:
        manifest, rows = read_run(directory)
        runs.append(
            {
                "directory": str(directory.resolve()),
                "manifest_hash": digest(manifest.model_dump(mode="json")),
            }
        )
        for row in rows:
            if (
                not any(r.rule_id == "quality.supported" for r in row.rules)
                or row.execution_status == "not_run"
            ):
                continue
            if row.identity() in seen:
                raise ValueError("duplicate review trial")
            seen.add(row.identity())
            evidence = load(Path(row.private_directory), row.evidence_hash)
            if evidence.get("errors"):
                raise ValueError("review evidence incomplete")
            token = str(uuid4())
            sources = [
                s
                for v in evidence["input_observations"]
                if v.get("category") == "sources"
                for s in v.get("sources", [])
            ]
            samples.append(
                {
                    "sample_id": token,
                    "task_family": row.execution_item_id.split(".")[1],
                    "user_requests": [
                        v["body"]["message"]
                        for v in evidence["http"]
                        if isinstance(v.get("body"), dict)
                        and isinstance(v["body"].get("message"), str)
                    ],
                    "answer": evidence["facts"].get("answer", ""),
                    "source_material": [
                        {"kind": s["kind"], "content": json.loads(s["content"])} for s in sources
                    ],
                    "rubric": RUBRIC,
                }
            )
            audit.append(
                {
                    "sample_id": token,
                    "trial_key": list(row.identity()),
                    "evidence_hash": row.evidence_hash,
                    "private_directory": row.private_directory,
                    "raw_verdict": row.verdict,
                    "known_fail_rules": [r.rule_id for r in row.rules if r.status == "fail"],
                }
            )
    if not samples:
        raise ValueError("no actual quality review samples")
    secrets.SystemRandom().shuffle(samples)
    output.mkdir(parents=True)
    atomic_json(
        output / "review-pack.json",
        {"schema_version": 1, "samples": samples, "release_eligible": False},
    )
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        audit_output,
        {
            "schema_version": 1,
            "pack_hash": file_hash(output / "review-pack.json"),
            "runs": runs,
            "samples": audit,
            "release_eligible": False,
        },
    )
    return {
        "samples": len(samples),
        "pack": str(output / "review-pack.json"),
        "audit": str(audit_output),
        "human_labels": 0,
        "release_eligible": False,
    }


def judge_eligibility(
    records: list[Adjudication], evidence: dict[tuple[str, str, str, str, int], str]
) -> dict[str, Any]:
    """No network. Authentic records and explicit provider grants are still prerequisites."""
    now = datetime.now(timezone.utc)
    groups: dict[tuple[str, str, str, str, int], list[Adjudication]] = {}
    invalid = 0
    identities = set()
    for record in records:
        if (
            record.run_id != record.trial_key[0]
            or record.adjudication_id in identities
            or not structured_human_record(record)
            or record.created_at > now
            or record.expires_at <= now
            or evidence.get(record.trial_key) != record.evidence_hash
            or not verified_reference(record.record_ref, record.record_hash)
        ):
            invalid += 1
            continue
        identities.add(record.adjudication_id)
        groups.setdefault(record.trial_key, []).append(record)
    labelled = {
        key: rows[0].decision
        for key, rows in groups.items()
        if {r.decision for r in rows} in ({"pass"}, {"fail"})
    }
    eligible = invalid == 0 and len(labelled) >= 20 and set(labelled.values()) == {"pass", "fail"}
    return {
        "eligible_for_calibration": eligible,
        "unique_human_samples": len(labelled),
        "positive": sum(v == "pass" for v in labelled.values()),
        "negative": sum(v == "fail" for v in labelled.values()),
        "invalid_records": invalid,
        "minimum": 20,
        "network_requests": 0,
        "judge_release_enabled": False,
        "identity_authenticated": False,
        "reason": "requires_authorized_calibration_and_recorded_false_positives_false_negatives"
        if eligible
        else "insufficient_valid_positive_and_negative_human_labels",
        "release_eligible": False,
    }


def structured_human_record(record: Adjudication) -> bool:
    """Bind per-sample annotations; a generic text file is not a human label."""
    try:
        path = Path(record.record_ref)
        if path.stat().st_size > 131072:
            return False
        actual = json.loads(path.read_bytes())
        fields = {
            "adjudication_id",
            "run_id",
            "trial_key",
            "evidence_hash",
            "rubric",
            "reviewer",
            "decision",
            "reason",
            "dimensions",
            "created_at",
            "expires_at",
        }
        expected = {k: v for k, v in record.model_dump(mode="json").items() if k in fields}
        expected.update(schema_version=1, kind="human-rubric-record")
        return bool(actual == expected)
    except (ValueError, TypeError, KeyError, OSError):
        return False
