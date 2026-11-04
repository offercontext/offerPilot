"""One exact unstarted, owner-cancelled request; never a generic cancel exception.

The before/after public metadata is immutable evidence, not an allowance. Every
admission re-reads the complete run/jobs/approvals/artifact lists. Missing fields,
additional artifacts, attempts, steps or a runner invalidate this exception.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.companion_ai_validation.preflight import Reader

RUN_ID = 41290462304
JOB_ID = 124804486152
SOURCE_SHA = "ca73a48211c0106daf5ac8d017c7cab7f0e03c35"
HELPER_SHA = "d6ed094caaf5d18a74abee5810f9fee0734bb508"
WORKFLOW_ID = 391917473
RUN_NUMBER = 32
OFFLINE_ARTIFACT_ID = 13077008604
EVIDENCE_SHA256 = "e0d2623bef6274642ac9f562b7c7a5db222a6cc25b20d429adaf63ee1a18c1bc"
EVIDENCE = (Path(__file__).resolve().parents[2] / "experiments/companion_experience"
            / "fixtures/abandoned-retest-v1.json")


def validate_identity(run: Mapping[str, Any], jobs: Mapping[str, Mapping[str, Any]],
                      *, before: bool = False) -> None:
    from scripts.companion_ai_validation import preflight as p
    expected = {"id": RUN_ID, "run_number": RUN_NUMBER, "run_attempt": 1,
                "head_sha": SOURCE_SHA, "workflow_id": WORKFLOW_ID,
                "head_branch": p.BRANCH, "path": p.WORKFLOW, "event": "push",
                "status": "waiting" if before else "completed",
                "conclusion": None if before else "cancelled"}
    p.demand(all(key in run and type(run[key]) is type(value) and run[key] == value
                 for key, value in expected.items()), "ABANDONED_RUN_UNVERIFIED")
    p.demand(set(jobs) == p.JOBS, "ABANDONED_RUN_UNVERIFIED")
    p.demand(p.trusted_repo(run.get("repository"), 241031113)
             and p.trusted_repo(run.get("head_repository"), 241031113),
             "ABANDONED_RUN_UNVERIFIED")
    for name, job_id in ((p.OFFLINE_JOB, 124800450462), (p.PREFLIGHT_JOB, 124804296400),
                         (p.PAID_JOB, JOB_ID)):
        job = jobs[name]
        identity = {"id": job_id, "run_id": RUN_ID, "run_attempt": 1,
                    "head_sha": SOURCE_SHA, "head_branch": p.BRANCH, "name": name}
        p.demand(all(key in job and type(job[key]) is type(value) and job[key] == value
                     for key, value in identity.items()), "ABANDONED_JOB_UNVERIFIED")
        if name != p.PAID_JOB:
            p.demand(p.completed(job, "success"), "ABANDONED_JOB_UNVERIFIED")
            continue
        zero: dict[str, Any] = {"status": "waiting" if before else "completed",
                "conclusion": None if before else "cancelled", "steps": [],
                "runner_id": None if before else 0,
                "runner_name": None if before else "",
                "runner_group_id": None if before else 0,
                "runner_group_name": None if before else "",
                "started_at": "2026-11-01T09:09:00Z",
                "completed_at": None if before else "2026-11-04T02:48:32Z"}
        p.demand(all(key in job and type(job[key]) is type(value) and job[key] == value
                     for key, value in zero.items()), "ABANDONED_JOB_STARTED_OR_UNKNOWN")


def validate_auxiliary(approvals: Any, artifacts: Any) -> None:
    from scripts.companion_ai_validation import preflight as p
    p.demand(approvals == [] and type(approvals) is list,
             "ABANDONED_APPROVAL_UNVERIFIED")
    p.demand(type(artifacts) is list and len(artifacts) == 1,
             "ABANDONED_ARTIFACT_UNVERIFIED")
    item = p.obj(artifacts[0])
    exact = {"id": OFFLINE_ARTIFACT_ID,
             "name": f"companion-ai-offline-{SOURCE_SHA}", "size_in_bytes": 59946,
             "digest": "sha256:ed213cc96128ff0c4d9a47acd5d914c8503ed1015ec3d8cd475a721bc69853a5",
             "expired": False}
    p.demand(all(key in item and type(item[key]) is type(value) and item[key] == value
                 for key, value in exact.items()), "ABANDONED_ARTIFACT_UNVERIFIED")
    owner = p.obj(item.get("workflow_run"))
    owner_exact = {"id": RUN_ID, "repository_id": 241031113,
                   "head_repository_id": 241031113, "head_branch": p.BRANCH,
                   "head_sha": SOURCE_SHA}
    p.demand(all(key in owner and type(owner[key]) is type(value) and owner[key] == value
                 for key, value in owner_exact.items()), "ABANDONED_ARTIFACT_UNVERIFIED")


def verified_evidence() -> dict[str, Any]:
    from scripts.companion_ai_validation import preflight as p
    try:
        raw = EVIDENCE.read_bytes()
        p.demand(hashlib.sha256(raw).hexdigest() == EVIDENCE_SHA256,
                 "ABANDONED_EVIDENCE_UNVERIFIED")
        data = p.obj(json.loads(raw))
        for key in ("before", "after"):
            snapshot = p.obj(data[key])
            run = p.obj(snapshot["run"])
            jobs = p.validate_jobs(run, snapshot["jobs"]["jobs"])
            validate_identity(run, jobs, before=key == "before")
            validate_auxiliary(snapshot["approvals"], snapshot["artifacts"]["artifacts"])
        return data
    except (OSError, KeyError, TypeError, ValueError):
        raise p.Blocked("ABANDONED_EVIDENCE_UNVERIFIED") from None


def validate_metadata(runs: list[dict[str, Any]], jobs: Mapping[int, list[dict[str, Any]]],
                      read: Reader) -> None:
    from scripts.companion_ai_validation import preflight as p
    verified_evidence()
    matches = [run for run in runs if run.get("id") == RUN_ID]
    p.demand(len(matches) == 1 and RUN_ID in jobs, "ABANDONED_RUN_UNVERIFIED")
    validate_identity(matches[0], p.validate_jobs(matches[0], jobs[RUN_ID]))
    # A second direct run read binds complete history to its latest terminal state.
    latest = p.obj(p.single(read, f"actions/runs/{RUN_ID}"))
    validate_identity(latest, p.validate_jobs(latest, jobs[RUN_ID]))
    approvals = p.single(read, f"actions/runs/{RUN_ID}/approvals")
    artifacts = p.read_pages(read, f"actions/runs/{RUN_ID}/artifacts", "artifacts")
    validate_auxiliary(approvals, artifacts)
