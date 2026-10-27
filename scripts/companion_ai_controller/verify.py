"""Pure rehearsal verifier: candidate bytes are data, never executable authority.

No network/credential/production adapter exists in this phase. Synthetic platform
metadata exercises the contract, but cannot create a live admission.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import io
import json
import math
import stat
from typing import Any
import zipfile

from scripts.companion_ai_controller import history
from scripts.companion_ai_controller import policy as p

Blocked = p.Blocked
SYNTHETIC_NODES = ("fixture::first", "fixture::second")


def _aggregate() -> dict[str, Any]:
    return {"passed": True, "exit_code": 0, "collected": list(SYNTHETIC_NODES),
            "finished": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}
                         for node in SYNTHETIC_NODES}, "deselected": []}


def _fixture_archive() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("companion-ai-offline.json", (2026, 10, 27, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(info, p.canonical(_aggregate()))
    return stream.getvalue()


SYNTHETIC_ARTIFACT_SHA256 = hashlib.sha256(_fixture_archive()).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        p.demand(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def strict_json(raw: bytes) -> Any:
    p.demand(type(raw) is bytes and 0 < len(raw) <= 65536, "JSON_SIZE_INVALID")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Blocked("JSON_NONFINITE")))
    except (ValueError, UnicodeError, RecursionError):
        raise Blocked("JSON_INVALID") from None


def verify_artifact(raw: bytes, claimed_digest: str) -> None:
    p.demand(type(raw) is bytes and 0 < len(raw) <= 131072, "ARTIFACT_SIZE_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    # Fixture pin is independent of the candidate's supplied metadata. Real
    # registration must replace this with verified CI identities, not trust an
    # attacker-supplied digest next to an attacker-supplied ZIP.
    p.demand(digest == claimed_digest == SYNTHETIC_ARTIFACT_SHA256, "ARTIFACT_DIGEST_MISMATCH")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            p.demand(len(members) == 1 and members[0].filename == "companion-ai-offline.json",
                     "ARTIFACT_MEMBERS_INVALID")
            info = members[0]
            p.demand(not info.is_dir() and not info.flag_bits & 1
                     and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
                     and 0 < info.file_size <= 65536, "ARTIFACT_MEMBER_INVALID")
            aggregate = strict_json(archive.read(info))
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile):
        raise Blocked("ARTIFACT_INVALID") from None
    p.demand(p.canonical(aggregate) == p.canonical(_aggregate()), "AGGREGATE_INCOMPLETE")


@dataclass(frozen=True)
class Admission:
    mode: str
    live_allowed: bool
    phase: str
    binding_digest: str
    batch_key: str
    payload_hashes: tuple[str, ...]
    controller_sha: str
    source_sha: str
    prior_calls: int
    settled_micro_cny: int
    reserved_micro_cny: int
    deadline: int
    checked_at: float


def _binding(value: Any) -> Any:
    if type(value) is bytes:
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, dict):
        return {key: _binding(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_binding(item) for item in value]
    return value


def _verify(bundle: Mapping[str, Any], *, now: float, phase: str,
            registration: p.Registration | None, approved: Admission | None) -> Admission:
    p.demand(registration is not None, "PRODUCTION_UNCONFIGURED")
    assert registration is not None
    p.demand(registration == p.fixture_registration() and registration.rehearsal is True,
             "LIVE_ADMISSION_DISABLED")
    p.demand(phase in ("before", "paid"), "PHASE_INVALID")
    p.demand(type(now) in (int, float) and math.isfinite(now) and 0 < now <= p.DEADLINE - p.WINDOW,
             "DEADLINE_OR_WINDOW_EXPIRED")
    p.demand(isinstance(bundle, Mapping) and set(bundle) == {
        "mode", "platform", "candidate", "ci", "artifact", "artifact_digest", "manifest",
        "requests", "history", "environment", "review"}, "BUNDLE_SCHEMA_INVALID")
    p.demand(bundle["mode"] == "synthetic_platform", "PLATFORM_ADAPTER_UNAVAILABLE")
    expected_platform = {"repository_id": p.REPOSITORY_ID, "event": "workflow_dispatch",
        "ref": p.CONTROL_REF, "workflow_id": registration.workflow_id,
        "workflow_sha": registration.controller_sha, "checkout_sha": registration.controller_sha,
        "branch_sha": registration.controller_sha, "configured_controller_sha": registration.controller_sha,
        "run_id": 910001, "attempt": 1, "concurrency": p.CONCURRENCY, "cancel_in_progress": False}
    p.demand(type(bundle["platform"]) is dict and p.canonical(bundle["platform"]) ==
             p.canonical(expected_platform), "PLATFORM_BINDING_INVALID")
    p.demand(p.canonical(bundle["candidate"]) == p.canonical({"repository_id": p.REPOSITORY_ID,
        "sha": p.SOURCE_SHA, "tree": p.SOURCE_TREE}), "CANDIDATE_IDENTITY_INVALID")
    expected_ci = {"mode": "synthetic_ci", "id": 910002, "workflow_id": 910003,
        "artifact_id": 910004, "head_sha": p.SOURCE_SHA, "attempt": 1,
        "status": "completed", "conclusion": "success"}
    p.demand(type(bundle["ci"]) is dict and p.canonical(bundle["ci"]) == p.canonical(expected_ci),
             "CI_IDENTITY_INVALID")
    verify_artifact(bundle["artifact"], bundle["artifact_digest"])
    p.demand(type(bundle["manifest"]) is bytes and
             hashlib.sha256(bundle["manifest"]).hexdigest() == p.MANIFEST_SHA256
             and strict_json(bundle["manifest"]) == p.MANIFEST, "MANIFEST_CHANGED")
    requests = bundle["requests"]
    p.demand(type(requests) is list and len(requests) == 6, "EXACT_SIX_REQUIRED")
    for raw, digest in zip(requests, p.REQUEST_HASHES, strict=True):
        p.demand(type(raw) is bytes and 0 < len(raw) <= 65536
                 and hashlib.sha256(raw).hexdigest() == digest, "PAYLOAD_CHANGED")
        request = strict_json(raw)
        p.demand(set(request) == {"model", "messages", "thinking", "temperature", "max_tokens", "response_format"}
                 and request["model"] == "deepseek-flash" and request["max_tokens"] == 768
                 and request["thinking"] == {"type": "disabled"}
                 and request["response_format"] == {"type": "json_object"}
                 and request["temperature"] == 0, "PAYLOAD_PROTOCOL_INVALID")
    expected_environment = {"mode": "synthetic_environment", "id": registration.environment_id,
        "allowed_ref": p.CONTROL_REF, "reviewer_id": p.OWNER_ID, "can_admins_bypass": False,
        "full_branch_protection_machine_verified": False,
        "owner_setup_attestation": "synthetic_only_not_platform_proof"}
    p.demand(type(bundle["environment"]) is dict and p.canonical(bundle["environment"]) ==
             p.canonical(expected_environment), "ENVIRONMENT_BINDING_INVALID")
    balance = history.verify_history(bundle["history"], registration=registration, current_run=910001, phase=phase)
    p.demand(balance.settled_micro_cny + 6 * p.RESERVATION <= p.CAP, "BUDGET_EXCEEDED")
    normalized = deepcopy(dict(bundle))
    normalized["review"] = None
    for row in normalized["history"]["controller_runs"]:
        if row["id"] == 910001:
            row["paid_started"] = False
    binding = hashlib.sha256(p.canonical(_binding(normalized))).hexdigest()
    if phase == "before":
        p.demand(bundle["review"] is None, "PREAPPROVAL_FORBIDDEN")
    if phase == "paid":
        p.demand(isinstance(approved, Admission) and approved.phase == "before"
                 and approved.mode == "rehearsal" and approved.live_allowed is False
                 and approved.binding_digest == binding and now >= approved.checked_at,
                 "TOCTOU_OR_ADMISSION_MISMATCH")
        p.demand(p.canonical(bundle["review"]) == p.canonical({"mode": "synthetic_owner_review", "run_id": 910001,
            "environment_id": registration.environment_id, "reviewer_id": p.OWNER_ID,
            "state": "approved"}), "OWNER_APPROVAL_INVALID")
    return Admission("rehearsal", False, phase, binding, p.BATCH_KEY, p.REQUEST_HASHES,
                     registration.controller_sha, p.SOURCE_SHA, balance.calls,
                     balance.settled_micro_cny, balance.reserved_micro_cny, p.DEADLINE, float(now))


def verify_bundle(bundle: Mapping[str, Any], *, now: float, phase: str = "before",
                  registration: p.Registration | None = None,
                  approved: Admission | None = None) -> Admission:
    try:
        return _verify(bundle, now=now, phase=phase, registration=registration, approved=approved)
    except Blocked:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
        raise Blocked("MALFORMED_EVIDENCE") from None


def make_rehearsal_bundle() -> dict[str, Any]:
    """Explicit fake platform evidence; no GETs, secrets, live approvals or IDs."""
    reg = p.fixture_registration()
    return {"mode": "synthetic_platform", "platform": {
        "repository_id": p.REPOSITORY_ID, "event": "workflow_dispatch", "ref": p.CONTROL_REF,
        "workflow_id": reg.workflow_id, "workflow_sha": reg.controller_sha,
        "checkout_sha": reg.controller_sha, "branch_sha": reg.controller_sha,
        "configured_controller_sha": reg.controller_sha, "run_id": 910001, "attempt": 1,
        "concurrency": p.CONCURRENCY, "cancel_in_progress": False},
        "candidate": {"repository_id": p.REPOSITORY_ID, "sha": p.SOURCE_SHA, "tree": p.SOURCE_TREE},
        "ci": {"mode": "synthetic_ci", "id": 910002, "workflow_id": 910003, "artifact_id": 910004,
               "head_sha": p.SOURCE_SHA, "attempt": 1, "status": "completed", "conclusion": "success"},
        "artifact": _fixture_archive(), "artifact_digest": SYNTHETIC_ARTIFACT_SHA256,
        "manifest": p.canonical(p.MANIFEST), "requests": list(p.frozen_requests()),
        "history": {"legacy_checkpoints": [{"run_id": run, "archive_sha256": sha}
            for run, sha in zip(history.LEGACY_RUNS, history.LEGACY_HASHES, strict=True)],
            "legacy_scan": {"mode": "synthetic_platform", "complete": True,
                            "unknown_paid_runs": [], "active_runs": []},
            "controller_runs": [{"id": 910001, "number": 1, "workflow_id": reg.workflow_id,
                "attempt": 1, "kind": "controller", "status": "in_progress", "paid_started": False,
                "batch_key": p.BATCH_KEY, "controller_sha": reg.controller_sha}], "dry_runs": []},
        "environment": {"mode": "synthetic_environment", "id": reg.environment_id,
            "allowed_ref": p.CONTROL_REF, "reviewer_id": p.OWNER_ID, "can_admins_bypass": False,
            "full_branch_protection_machine_verified": False,
            "owner_setup_attestation": "synthetic_only_not_platform_proof"},
        "review": None}


def synthetic_approve(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Fake platform transition only; never calls an approval API."""
    result = deepcopy(dict(bundle))
    p.demand(result.get("mode") == "synthetic_platform" and result.get("review") is None,
             "FAKE_TRANSITION_INVALID")
    result["review"] = {"mode": "synthetic_owner_review", "run_id": 910001,
        "environment_id": p.fixture_registration().environment_id,
        "reviewer_id": p.OWNER_ID, "state": "approved"}
    current = [row for row in result["history"]["controller_runs"] if row["id"] == 910001]
    p.demand(len(current) == 1 and current[0]["paid_started"] is False, "FAKE_TRANSITION_INVALID")
    current[0]["paid_started"] = True
    return result
