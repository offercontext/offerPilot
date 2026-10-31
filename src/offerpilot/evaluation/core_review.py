"""Blinded local human review packs and fail-closed judge calibration eligibility."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
from typing import Any
from uuid import uuid4

from offerpilot.evaluation.core_governance import Adjudication, verified_reference
from offerpilot.evaluation.core_review_evidence import review_evidence
from offerpilot.evaluation.core_process_signals import process_summary
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


def model_context(
    private: Path, evidence: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Export captured messages, never infer model knowledge from fixture state."""
    calls: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    fields = {"role", "content", "name", "tool_calls", "tool_call_id", "function_call"}
    roles = {"system", "developer", "user", "assistant", "tool", "function"}
    for observation in evidence.get("input_observations", []):
        if observation.get("category") != "final_input":
            continue
        name = observation.get("request_file")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("captured model input path is invalid")
        path = private / name
        recorded = evidence.get("files", {}).get(name, {})
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(private.resolve())
            or not path.is_file()
        ):
            raise ValueError("captured model input is unavailable")
        raw = path.read_bytes()
        actual_hash = sha256(raw).hexdigest()
        if actual_hash != recorded.get("sha256") or actual_hash != observation.get("payload_hash"):
            raise ValueError("captured model input is not bound to sealed evidence")
        payload = json.loads(raw)
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if (
            not isinstance(messages, list)
            or not messages
            or any(
                not isinstance(message, dict)
                or not isinstance(message.get("role"), str)
                or message["role"] not in roles
                for message in messages
            )
        ):
            raise ValueError("captured model messages are unsupported")
        number = len(calls) + 1
        calls.append(
            {
                "number": number,
                "messages": [{k: v for k, v in m.items() if k in fields} for m in messages],
            }
        )
        bindings.append(
            {
                "number": number,
                "request_file": name,
                "payload_hash": actual_hash,
                "epoch": observation.get("epoch"),
                "seq": observation.get("seq"),
                "identity": observation.get("identity"),
            }
        )
    return {"status": "captured" if calls else "not_captured", "calls": calls}, bindings


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
            context, context_bindings = model_context(Path(row.private_directory), evidence)
            provenance, execution, process_audit = review_evidence(evidence, context_bindings)
            token = str(uuid4())
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
                    "model_context": context,
                    "source_provenance": provenance,
                    "execution_observation": execution,
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
                    "model_input_bindings": context_bindings,
                    **process_audit,
                }
            )
    if not samples:
        raise ValueError("no actual quality review samples")
    secrets.SystemRandom().shuffle(samples)
    output.mkdir(parents=True)
    atomic_json(
        output / "review-pack.json",
        {
            "schema_version": 3,
            "samples": samples,
            "context_scope": "Captured model-visible messages, call-bound source/version observations "
            "and sealed process observations; missing JD or candidate background is not inferred. "
            "Observed process data does not automatically certify functional success or safety.",
            "release_eligible": False,
        },
    )
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        audit_output,
        {
            "schema_version": 3,
            "pack_hash": file_hash(output / "review-pack.json"),
            "review_exporter_hash": file_hash(Path(__file__)),
            "process_exporter_hash": file_hash(Path(review_evidence.__code__.co_filename)),
            "process_signal_exporter_hash": file_hash(Path(process_summary.__code__.co_filename)),
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
