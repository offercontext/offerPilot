"""Fail-closed fixed-six production admission; no credentials or send capability.

Registration is external deployment input, never inferred from a candidate or a
successful fixture. This module performs only public, scoped GitHub GETs. Owner
setup attestation is explicit human trust, not proof of hidden branch settings.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
import hashlib
import argparse
import json
import math
from pathlib import Path
import re
import time
from typing import Any

from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_controller import read_only as ro
from scripts.companion_ai_validation import preflight as old_gate

PRODUCTION_REGISTRATION = None
WORKFLOW_PATH = ".github/workflows/companion-ai-controller.yml"
DRY_PATH = ".github/workflows/companion-ai-controller-dry-run.yml"
DRY_WORKFLOW = 397812462
OFFLINE_JOB = "Controller offline safety"
PREFLIGHT_JOB = "Controller request preflight"
PAID_JOB = "Controller approved real AI"
DRY_JOB = "Controller no-secret evidence rehearsal"
CONTROL_DRY_JOB = "Controller platform-only review"
SETUP_ATTESTATION = "owner-verified-lock-and-no-bypass"
CAMPAIGN_CHECKPOINT = "owner-confirmed-registered-entrypoints-only-and-environment-only-key"
SHA = re.compile(r"[0-9a-f]{40}\Z")
Blocked = p.Blocked


@dataclass(frozen=True)
class DryRunAnchor:
    run_id: int
    workflow_id: int
    head_sha: str
    workflow_blob_sha: str
    job_name: str = DRY_JOB
    event: str = "push"
    head_branch: str = "feat/20261027-fixed-ai-controller"


KNOWN_DRY_ANCHORS = (
    DryRunAnchor(40447671170, DRY_WORKFLOW, "3e03dad887c17845c4e4414ce537ffc25c4bb1f5",
                 "9a34228203f01b44bcfe6ba10121215309bd9dbd", "Controller fake-only contract rehearsal"),
    DryRunAnchor(40450655710, DRY_WORKFLOW, "95583e10e0199230abe9d6541d8f326269f799a4",
                 "c5c4d6da2a9198004afb1443bdf023a31969f930", DRY_JOB),
)


@dataclass(frozen=True)
class Registration:
    controller_sha: str
    workflow_id: int
    environment_id: int
    environment_name: str
    branch_policy_id: int
    dry_run_anchors: tuple[DryRunAnchor, ...]
    other_workflow_ids: tuple[int, ...]
    owner_setup_attestation: str
    owner_campaign_checkpoint: str
    repository_id: int = p.REPOSITORY_ID
    control_ref: str = p.CONTROL_REF
    owner_id: int = p.OWNER_ID
    actor_id: int = p.OWNER_ID
    reader_mode: str = "anonymous"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, controller_sha: str) -> Registration:
        try:
            p.demand(type(value) is dict and set(value) == {f.name for f in fields(cls)} - {"controller_sha"},
                     "REGISTRATION_SCHEMA_INVALID")
            data = dict(value)
            p.demand(type(data["dry_run_anchors"]) is list and type(data["other_workflow_ids"]) is list,
                     "REGISTRATION_SCHEMA_INVALID")
            data["dry_run_anchors"] = tuple(DryRunAnchor(**row) for row in data["dry_run_anchors"])
            data["other_workflow_ids"] = tuple(data["other_workflow_ids"])
            result = cls(controller_sha=controller_sha, **data)
            _registration(result)
            return result
        except Blocked:
            raise
        except (TypeError, ValueError, KeyError, AttributeError):
            raise Blocked("REGISTRATION_SCHEMA_INVALID") from None


@dataclass(frozen=True)
class RunContext:
    repository_id: int
    ref: str
    sha: str
    workflow_sha: str
    run_id: int
    attempt: int
    event: str
    actor_id: int
    triggering_actor_id: int


@dataclass(frozen=True)
class ProductionAdmission:
    mode: str
    phase: str
    binding_digest: str
    registration_digest: str
    run_id: int
    controller_sha: str
    source_sha: str
    payload_hashes: tuple[str, ...]
    batch_key: str
    prior_calls: int
    settled_micro_cny: int
    reserved_micro_cny: int
    checked_at: float
    expires_at: float
    deadline: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ProductionAdmission:
        try:
            p.demand(type(value) is dict and set(value) == {f.name for f in fields(cls)}, "HANDOFF_SCHEMA_INVALID")
            data = dict(value)
            p.demand(type(data["payload_hashes"]) is list, "HANDOFF_SCHEMA_INVALID")
            data["payload_hashes"] = tuple(data["payload_hashes"])
            result = cls(**data)
            p.demand(result.mode == "production_verified" and result.phase == "before"
                     and result.source_sha == p.SOURCE_SHA and result.payload_hashes == p.REQUEST_HASHES
                     and result.batch_key == p.BATCH_KEY and result.deadline == p.DEADLINE
                     and type(result.run_id) is int and result.run_id > 0
                     and type(result.checked_at) in (int, float) and math.isfinite(result.checked_at)
                     and 0 < result.checked_at <= p.DEADLINE-p.WINDOW
                     and result.expires_at == p.DEADLINE
                     and result.prior_calls == p.PRIOR_CALLS and result.settled_micro_cny == p.PRIOR_SPEND
                     and result.reserved_micro_cny == 0
                     and all(type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v)
                             for v in (result.binding_digest, result.registration_digest))
                     and type(result.controller_sha) is str and SHA.fullmatch(result.controller_sha) is not None,
                     "HANDOFF_CONTENT_INVALID")
            return result
        except Blocked:
            raise
        except (TypeError, ValueError, KeyError, AttributeError):
            raise Blocked("HANDOFF_SCHEMA_INVALID") from None


def _registration(reg: Registration | None) -> Registration:
    p.demand(type(reg) is Registration, "PRODUCTION_UNCONFIGURED")
    assert reg is not None
    p.demand(type(reg.controller_sha) is str and SHA.fullmatch(reg.controller_sha) is not None
             and type(reg.repository_id) is int and reg.repository_id == p.REPOSITORY_ID
             and reg.control_ref == p.CONTROL_REF and reg.owner_id == p.OWNER_ID
             and reg.actor_id == p.OWNER_ID and reg.owner_setup_attestation == SETUP_ATTESTATION
             and reg.owner_campaign_checkpoint == CAMPAIGN_CHECKPOINT
             and reg.reader_mode in ("anonymous", "github_actions_read")
             and type(reg.environment_name) is str
             and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", reg.environment_name) is not None
             and all(type(v) is int and v > 0 for v in (reg.workflow_id, reg.environment_id,
                     reg.branch_policy_id, reg.owner_id, reg.actor_id)), "REGISTRATION_INVALID")
    p.demand(type(reg.dry_run_anchors) is tuple and bool(reg.dry_run_anchors)
             and all(type(a) is DryRunAnchor and type(a.run_id) is int and a.run_id > 0
                     and type(a.workflow_id) is int and a.workflow_id == DRY_WORKFLOW
                     and type(a.head_sha) is str and SHA.fullmatch(a.head_sha)
                     and type(a.workflow_blob_sha) is str and SHA.fullmatch(a.workflow_blob_sha)
                     and ((a.event == "push" and a.head_branch == "feat/20261027-fixed-ai-controller"
                           and a.job_name in (DRY_JOB, "Controller fake-only contract rehearsal"))
                          or (a.event == "workflow_dispatch" and a.head_branch == "ai-validation-control"
                              and a.head_sha == reg.controller_sha and a.job_name == CONTROL_DRY_JOB))
                     for a in reg.dry_run_anchors)
             and len({a.run_id for a in reg.dry_run_anchors}) == len(reg.dry_run_anchors)
             and {40447671170, 40450655710}.issubset({a.run_id for a in reg.dry_run_anchors}),
             "DRY_ANCHORS_INVALID")
    p.demand(set(KNOWN_DRY_ANCHORS).issubset(set(reg.dry_run_anchors)), "KNOWN_DRY_ANCHOR_CHANGED")
    p.demand(type(reg.other_workflow_ids) is tuple and all(type(v) is int and v > 0 for v in reg.other_workflow_ids)
             and len(set(reg.other_workflow_ids)) == len(reg.other_workflow_ids)
             and not ({reg.workflow_id, DRY_WORKFLOW, ro.WORKFLOW} & set(reg.other_workflow_ids))
             and len({reg.workflow_id, DRY_WORKFLOW, ro.WORKFLOW}) == 3, "WORKFLOW_REGISTRY_INVALID")
    return reg


def _paths(reg: Registration, ctx: RunContext) -> frozenset[str]:
    values = {"/git/ref/heads/ai-validation-control", f"/git/commits/{reg.controller_sha}",
              f"/actions/workflows/{ro.WORKFLOW}", f"/actions/workflows/{reg.workflow_id}",
              f"/actions/workflows/{DRY_WORKFLOW}", f"/environments/{reg.environment_name}",
              f"/actions/runs/{ctx.run_id}/approvals"}
    for path in ("/actions/workflows", f"/actions/workflows/{reg.workflow_id}/runs",
                 f"/actions/workflows/{DRY_WORKFLOW}/runs",
                 f"/environments/{reg.environment_name}/deployment-branch-policies"):
        values.update(f"{path}?per_page=100&page={page}" for page in range(1, ro.MAX_PAGES+1))
    for anchor in reg.dry_run_anchors:
        values.add(f"/git/commits/{anchor.head_sha}")
        if anchor.event == "workflow_dispatch":
            values.add(f"/actions/runs/{anchor.run_id}/approvals")
    return frozenset(values)


class _Snapshot:
    def __init__(self, reader: ro.Reader, paths: frozenset[str]):
        self.reader = reader
        self.paths = paths
        self.records: dict[str, Any] = {}

    def __call__(self, path: str) -> ro.Response:
        p.demand(ro._allowed(path) or (path in self.paths and ro._registered_safe(path)), "GET_PATH_NOT_ALLOWED")
        result = self.reader(path)
        value = ro._json(result.body)
        if path in self.records:
            p.demand(self.records[path] == value, "READ_CHANGED_DURING_SNAPSHOT")
        self.records[path] = value
        return result

    def tree(self, tree_sha: str) -> dict[str, Any]:
        p.demand(type(tree_sha) is str and SHA.fullmatch(tree_sha) is not None, "TREE_SHA_INVALID")
        path = f"/git/trees/{tree_sha}?recursive=1"
        self.paths = self.paths | {path}
        if isinstance(self.reader, ro.PublicReader):
            self.reader.registered_paths = self.reader.registered_paths | {path}
        result = self.obj(path)
        p.demand(result.get("sha") == tree_sha and result.get("truncated") is False
                 and type(result.get("tree")) is list, "TREE_INCOMPLETE")
        return result

    def obj(self, path: str) -> dict[str, Any]:
        return ro._read(self, path, registered_paths=self.paths)[0]

    def pages(self, path: str, key: str) -> list[dict[str, Any]]:
        return ro._pages(self, path, key, registered_paths=self.paths)


def _time(now: float | None) -> float:
    value = time.time() if now is None else now
    p.demand(type(value) in (int, float) and math.isfinite(value)
             and 0 < value <= p.DEADLINE-p.WINDOW, "DEADLINE_OR_WINDOW_EXPIRED")
    return float(value)


def _run_identity(row: Mapping[str, Any], reg: Registration, workflow_id: int, sha: str, *, current: bool) -> None:
    repo = row.get("repository")
    p.demand(type(repo) is dict and repo.get("id") == p.REPOSITORY_ID
             and repo.get("full_name") == "offercontext/offerPilot"
             and type(row.get("workflow_id")) is int and row["workflow_id"] == workflow_id
             and row.get("head_sha") == sha and type(row.get("run_attempt")) is int and row["run_attempt"] == 1,
             "RUN_IDENTITY_INVALID")
    if current:
        p.demand(row.get("event") == "workflow_dispatch" and row.get("head_branch") == "ai-validation-control"
                 and row.get("status") == "in_progress" and row.get("conclusion") is None
                 and row.get("actor", {}).get("id") == reg.actor_id
                 and row.get("triggering_actor", {}).get("id") == reg.actor_id, "CURRENT_RUN_INVALID")


def _workflow(snapshot: _Snapshot, workflow_id: int, path: str, state: str) -> None:
    meta = snapshot.obj(f"/actions/workflows/{workflow_id}")
    p.demand(type(meta.get("id")) is int and meta["id"] == workflow_id
             and meta.get("path") == path and meta.get("state") == state, "WORKFLOW_STATE_INVALID")


def _history(snapshot: _Snapshot, reg: Registration, ctx: RunContext, phase: str, control_paths: set[str]) -> None:
    _workflow(snapshot, ro.WORKFLOW, old_gate.WORKFLOW, "disabled_manually")
    _workflow(snapshot, reg.workflow_id, WORKFLOW_PATH, "active")
    _workflow(snapshot, DRY_WORKFLOW, DRY_PATH, "active")
    catalogue = snapshot.pages("/actions/workflows", "workflows")
    p.demand({item["id"] for item in catalogue} == {
        ro.WORKFLOW, reg.workflow_id, DRY_WORKFLOW, *reg.other_workflow_ids}, "WORKFLOW_CATALOGUE_CHANGED")
    p.demand(all(type(item.get("path")) is str and item["path"] not in control_paths
                 for item in catalogue if item["id"] in reg.other_workflow_ids), "OTHER_WORKFLOW_REACHES_CONTROL")
    source_zip = Path(__file__).with_name("evidence") / "h-offline-12707161298.zip"
    p.demand(source_zip.is_file() and not source_zip.is_symlink(), "H_EVIDENCE_MISSING")
    with source_zip.open("rb") as stream:
        raw = stream.read(ro.ZIP_SIZE+1)
    ro.collect_source(snapshot, artifact_bytes=raw, scan_history=True)
    dry = snapshot.pages(f"/actions/workflows/{DRY_WORKFLOW}/runs", "workflow_runs")
    anchors = {a.run_id: a for a in reg.dry_run_anchors}
    p.demand(any(a.event == "workflow_dispatch" for a in reg.dry_run_anchors), "PLATFORM_REHEARSAL_MISSING")
    p.demand({row["id"] for row in dry} == set(anchors), "UNREGISTERED_DRY_RUN")
    p.demand(sorted(row.get("run_number", 0) for row in dry) == list(range(1, len(dry)+1)), "DRY_HISTORY_GAP")
    for row in dry:
        anchor = anchors[row["id"]]
        _run_identity(row, reg, DRY_WORKFLOW, anchor.head_sha, current=False)
        p.demand(row.get("status") == "completed" and row.get("conclusion") == "success"
                 and row.get("event") == anchor.event and row.get("head_branch") == anchor.head_branch,
                 "DRY_RUN_UNRESOLVED")
        jobs = snapshot.pages(f"/actions/runs/{anchor.run_id}/jobs", "jobs")
        p.demand(len(jobs) == 1 and jobs[0].get("name") == anchor.job_name
                 and jobs[0].get("run_id") == anchor.run_id and jobs[0].get("head_sha") == anchor.head_sha
                 and jobs[0].get("status") == "completed" and jobs[0].get("conclusion") == "success",
                 "DRY_RUN_IS_NOT_PAID_EXEMPTION")
        commit = snapshot.obj(f"/git/commits/{anchor.head_sha}")
        p.demand(commit.get("sha") == anchor.head_sha and SHA.fullmatch(commit.get("tree", {}).get("sha", "")) is not None,
                 "DRY_COMMIT_INVALID")
        tree = snapshot.tree(commit["tree"]["sha"])
        entries = [entry for entry in tree["tree"] if entry.get("path") == DRY_PATH]
        p.demand(len(entries) == 1 and entries[0].get("mode") == "100644"
                 and entries[0].get("type") == "blob" and entries[0].get("sha") == anchor.workflow_blob_sha,
                 "DRY_WORKFLOW_BLOB_INVALID")
        if anchor.event == "workflow_dispatch":
            _approval(snapshot, reg, replace(ctx, run_id=anchor.run_id), bind=True)
    runs = snapshot.pages(f"/actions/workflows/{reg.workflow_id}/runs", "workflow_runs")
    p.demand(sorted(row.get("run_number", 0) for row in runs) == list(range(1, len(runs)+1)), "PRODUCTION_HISTORY_GAP")
    selected = [row for row in runs if row["id"] == ctx.run_id]
    p.demand(len(selected) == 1 and selected[0].get("run_number") == len(runs), "CURRENT_RUN_NOT_LATEST")
    for row in runs:
        current = row["id"] == ctx.run_id
        _run_identity(row, reg, reg.workflow_id, reg.controller_sha, current=current)
        jobs = snapshot.pages(f"/actions/runs/{row['id']}/jobs", "jobs")
        names = {job.get("name"): job for job in jobs}
        p.demand(len(names) == len(jobs) and set(names).issubset({OFFLINE_JOB, PREFLIGHT_JOB, PAID_JOB})
                 and all(job.get("run_id") == row["id"] and job.get("head_sha") == reg.controller_sha for job in jobs),
                 "PRODUCTION_JOB_IDENTITY_INVALID")
        if not current:
            paid = names.get(PAID_JOB)
            p.demand(row.get("status") == "completed" and all(job.get("status") == "completed" for job in jobs)
                     and paid is not None and paid.get("conclusion") == "skipped",
                     "PRIOR_PAID_STARTED_OR_UNKNOWN")
            continue
        p.demand({OFFLINE_JOB, PREFLIGHT_JOB}.issubset(names)
                 and names[OFFLINE_JOB].get("status") == "completed"
                 and names[OFFLINE_JOB].get("conclusion") == "success", "CURRENT_OFFLINE_REQUIRED")
        if phase == "before":
            p.demand(names[PREFLIGHT_JOB].get("status") == "in_progress"
                     and names[PREFLIGHT_JOB].get("conclusion") is None
                     and (PAID_JOB not in names or (names[PAID_JOB].get("status") in ("queued", "waiting", "pending")
                                                  and names[PAID_JOB].get("conclusion") is None)),
                     "BEFORE_JOB_STATE_INVALID")
        else:
            p.demand(names[PREFLIGHT_JOB].get("status") == "completed"
                     and names[PREFLIGHT_JOB].get("conclusion") == "success"
                     and PAID_JOB in names and names[PAID_JOB].get("status") == "in_progress"
                     and names[PAID_JOB].get("conclusion") is None, "PAID_JOB_STATE_INVALID")
        # Only the known current lifecycle transition is removed from binding.
        path = f"/actions/runs/{ctx.run_id}/jobs?per_page=100&page=1"
        p.demand(len(jobs) <= 3, "CURRENT_JOBS_INVALID")
        snapshot.records[path] = {"offline_job_id": names[OFFLINE_JOB]["id"],
            "preflight_job_id": names[PREFLIGHT_JOB]["id"], "head_sha": reg.controller_sha}
    path = f"/actions/workflows/{reg.workflow_id}/runs?per_page=100&page=1"
    # Current run updated_at/run_started_at may change with owner approval; all
    # immutable identity fields and every other run remain bound.
    for key, value in list(snapshot.records.items()):
        if key.startswith(f"/actions/workflows/{reg.workflow_id}/runs?"):
            value = json.loads(json.dumps(value))
            for row in value["workflow_runs"]:
                if row["id"] == ctx.run_id:
                    for volatile in ("updated_at", "run_started_at", "referenced_workflows"):
                        row.pop(volatile, None)
            snapshot.records[key] = value


def _environment(snapshot: _Snapshot, reg: Registration) -> None:
    env = snapshot.obj(f"/environments/{reg.environment_name}")
    p.demand(env.get("id") == reg.environment_id and env.get("name") == reg.environment_name
             and env.get("can_admins_bypass") is False, "ENVIRONMENT_INVALID")
    rules = env.get("protection_rules")
    p.demand(type(rules) is list and all(type(rule) is dict for rule in rules)
             and all(rule.get("type") in ("required_reviewers", "branch_policy") for rule in rules),
             "ENVIRONMENT_RULES_INVALID")
    assert isinstance(rules, list)
    reviewers = [rule for rule in rules if rule.get("type") == "required_reviewers"]
    p.demand(len(reviewers) == 1 and len(rules) <= 2, "OWNER_REVIEWER_REQUIRED")
    rule = reviewers[0]
    configured = rule.get("reviewers")
    p.demand(rule.get("prevent_self_review") is False and type(configured) is list
             and len(configured) == 1 and configured[0].get("type") == "User"
             and type(configured[0].get("reviewer")) is dict, "OWNER_REVIEWER_REQUIRED")
    owner = rule["reviewers"][0]["reviewer"]
    p.demand(owner.get("type") == "User" and type(owner.get("id")) is int
             and owner["id"] == p.OWNER_ID and owner.get("login") == "XiaoZheBrother", "OWNER_REVIEWER_REQUIRED")
    p.demand(env.get("deployment_branch_policy") == {"protected_branches": False, "custom_branch_policies": True},
             "ENVIRONMENT_BRANCH_POLICY_INVALID")
    policies = snapshot.pages(f"/environments/{reg.environment_name}/deployment-branch-policies", "branch_policies")
    p.demand(len(policies) == 1 and policies[0].get("id") == reg.branch_policy_id
             and policies[0].get("name") == "ai-validation-control" and policies[0].get("type") == "branch",
             "ENVIRONMENT_BRANCH_POLICY_INVALID")


def _approval(snapshot: _Snapshot, reg: Registration, ctx: RunContext, *, bind: bool = False) -> None:
    path = f"/actions/runs/{ctx.run_id}/approvals"
    response = snapshot(path)
    reviews = ro._json(response.body)
    wrapped = ro.Response(p.canonical({"reviews": reviews}), response.status, response.headers)
    ro._read(lambda _: wrapped, "")
    p.demand(type(reviews) is list and len(reviews) == 1, "OWNER_APPROVAL_UNVERIFIED")
    review = reviews[0]
    owner, environments = review.get("user"), review.get("environments")
    p.demand(review.get("state") == "approved" and type(owner) is dict
             and owner.get("type") == "User" and owner.get("id") == p.OWNER_ID
             and owner.get("login") == "XiaoZheBrother" and type(environments) is list
             and len(environments) == 1 and environments[0].get("id") == reg.environment_id
             and environments[0].get("name") == reg.environment_name, "OWNER_APPROVAL_UNVERIFIED")
    if not bind:
        snapshot.records.pop(path)


def _verify(registration: Registration | None, context: RunContext, *, phase: str,
            before: ProductionAdmission | None, reader: ro.Reader | None, now: float | None, metadata_token: str | None) -> ProductionAdmission:
    reg = _registration(registration)
    started = _time(now)
    p.demand(type(context) is RunContext and context.repository_id == p.REPOSITORY_ID
             and context.ref == p.CONTROL_REF and context.sha == context.workflow_sha == reg.controller_sha
             and type(context.run_id) is int and context.run_id > 0 and type(context.attempt) is int
             and context.attempt == 1 and context.event == "workflow_dispatch"
             and context.actor_id == context.triggering_actor_id == reg.actor_id, "RUNTIME_CONTEXT_INVALID")
    paths = _paths(reg, context)
    p.demand(reader is None or metadata_token is None, "TEST_READER_CREDENTIALS_FORBIDDEN")
    snapshot = _Snapshot(build_reader(reg, context, metadata_token=metadata_token) if reader is None else reader, paths)
    ref = snapshot.obj("/git/ref/heads/ai-validation-control")
    p.demand(ref.get("ref") == p.CONTROL_REF and ref.get("object", {}).get("type") == "commit"
             and ref["object"].get("sha") == reg.controller_sha, "CONTROL_REF_UNVERIFIED")
    commit = snapshot.obj(f"/git/commits/{reg.controller_sha}")
    p.demand(commit.get("sha") == reg.controller_sha and SHA.fullmatch(commit.get("tree", {}).get("sha", "")) is not None,
             "CONTROL_COMMIT_UNVERIFIED")
    tree = snapshot.tree(commit["tree"]["sha"])
    entries = [entry for entry in tree["tree"] if entry.get("path", "").casefold().startswith(".github/workflows/")]
    p.demand(len(entries) == 2 and {entry.get("path") for entry in entries} == {WORKFLOW_PATH, DRY_PATH}
             and all(entry.get("type") == "blob" and entry.get("mode") == "100644"
                     and SHA.fullmatch(entry.get("sha", "")) for entry in entries), "CONTROL_TREE_EXTRA_ENTRYPOINT")
    _history(snapshot, reg, context, phase, {entry["path"] for entry in tree["tree"]})
    _environment(snapshot, reg)
    if phase == "paid":
        _approval(snapshot, reg, context)
    else:
        p.demand(phase == "before", "PHASE_INVALID")
    # Read locally pinned bytes each time; no user-supplied path, module or model.
    p.demand(tuple(hashlib.sha256(raw).hexdigest() for raw in p.frozen_requests()) == p.REQUEST_HASHES,
             "FROZEN_DATA_CHANGED")
    registration_digest = hashlib.sha256(p.canonical(asdict(reg))).hexdigest()
    binding = hashlib.sha256(p.canonical({"registration": registration_digest,
        "context": asdict(context), "observations": snapshot.records,
        "source_sha": p.SOURCE_SHA, "source_tree": p.SOURCE_TREE,
        "payload_hashes": p.REQUEST_HASHES, "batch_key": p.BATCH_KEY})).hexdigest()
    finished = _time(now)
    p.demand(0 <= finished - started < 120, "ADMISSION_READ_DEADLINE")
    if phase == "paid":
        p.demand(type(before) is ProductionAdmission and before.phase == "before"
                 and before.mode == "production_verified" and before.run_id == context.run_id
                 and before.binding_digest == binding and before.registration_digest == registration_digest
                 and type(before.checked_at) in (int, float) and 0 < before.checked_at <= started
                 and before.deadline == p.DEADLINE and before.expires_at == p.DEADLINE,
                 "APPROVAL_TOCTOU_MISMATCH")
    return ProductionAdmission("production_verified", phase, binding, registration_digest, context.run_id,
        reg.controller_sha, p.SOURCE_SHA, p.REQUEST_HASHES, p.BATCH_KEY, p.PRIOR_CALLS,
        p.PRIOR_SPEND, 0, finished, p.DEADLINE if phase == "before" else started+120, p.DEADLINE)


def build_reader(registration: Registration | None, context: RunContext, *,
                 metadata_token: str | None = None) -> ro.PublicReader:
    reg = _registration(registration)
    paths = _paths(reg, context)
    if reg.reader_mode == "anonymous":
        p.demand(metadata_token is None, "UNEXPECTED_METADATA_TOKEN")
        return ro.PublicReader(registered_paths=paths)
    p.demand(type(metadata_token) is str and bool(metadata_token), "EXPLICIT_METADATA_TOKEN_REQUIRED")
    assert metadata_token is not None
    return ro.GitHubActionsReadReader(token=metadata_token, registered_paths=paths)


def verify_before(registration: Registration | None, context: RunContext, *,
                  reader: ro.Reader | None = None, now: float | None = None,
                  metadata_token: str | None = None) -> ProductionAdmission:
    return _safe_verify(registration, context, phase="before", before=None, reader=reader, now=now, metadata_token=metadata_token)


def verify_paid(registration: Registration | None, context: RunContext, before: ProductionAdmission, *,
                reader: ro.Reader | None = None, now: float | None = None,
                  metadata_token: str | None = None) -> ProductionAdmission:
    return _safe_verify(registration, context, phase="paid", before=before, reader=reader, now=now, metadata_token=metadata_token)


def _safe_verify(*args: Any, **kwargs: Any) -> ProductionAdmission:
    try:
        return _verify(*args, **kwargs)
    except Blocked:
        raise
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError, RecursionError, OSError):
        raise Blocked("PRODUCTION_EVIDENCE_INVALID") from None


def control_probe(registration: Registration | None, context: RunContext, *,
                  reader: ro.Reader | None = None, now: float | None = None,
                  metadata_token: str | None = None) -> dict[str, Any]:
    """Independent 120-second platform read; never extends campaign authority."""
    try:
        reg = _registration(registration)
        started = time.monotonic()
        wall = time.time() if now is None else now
        p.demand(type(wall) in (int, float) and math.isfinite(wall) and wall > 0,
                 "CONTROL_PROBE_CLOCK_INVALID")
        p.demand(type(context) is RunContext and context.repository_id == p.REPOSITORY_ID
                 and context.ref == p.CONTROL_REF and context.sha == context.workflow_sha == reg.controller_sha
                 and type(context.run_id) is int and context.run_id > 0 and context.attempt == 1
                 and context.event == "workflow_dispatch"
                 and context.actor_id == context.triggering_actor_id == reg.actor_id, "RUNTIME_CONTEXT_INVALID")
        p.demand(metadata_token is None, "CONTROL_PROBE_CREDENTIALS_FORBIDDEN")
        snapshot = _Snapshot(ro.PublicReader(registered_paths=_paths(reg, context))
                             if reader is None else reader, _paths(reg, context))
        ref = snapshot.obj("/git/ref/heads/ai-validation-control")
        p.demand(ref.get("ref") == p.CONTROL_REF and ref.get("object", {}).get("type") == "commit"
                 and ref["object"].get("sha") == reg.controller_sha, "CONTROL_REF_UNVERIFIED")
        commit = snapshot.obj(f"/git/commits/{reg.controller_sha}")
        p.demand(commit.get("sha") == reg.controller_sha, "CONTROL_COMMIT_UNVERIFIED")
        tree = snapshot.tree(commit.get("tree", {}).get("sha", ""))
        entries = [entry for entry in tree["tree"] if entry.get("path", "").casefold().startswith(".github/workflows/")]
        p.demand(len(entries) == 2 and {entry.get("path") for entry in entries} == {WORKFLOW_PATH, DRY_PATH}
                 and all(entry.get("type") == "blob" and entry.get("mode") == "100644" for entry in entries),
                 "CONTROL_TREE_EXTRA_ENTRYPOINT")
        _workflow(snapshot, DRY_WORKFLOW, DRY_PATH, "active")
        run = snapshot.obj(f"/actions/runs/{context.run_id}")
        p.demand(run.get("id") == context.run_id, "CURRENT_RUN_INVALID")
        _run_identity(run, reg, DRY_WORKFLOW, reg.controller_sha, current=True)
        jobs = snapshot.pages(f"/actions/runs/{context.run_id}/jobs", "jobs")
        p.demand(len(jobs) == 1 and jobs[0].get("name") == CONTROL_DRY_JOB
                 and jobs[0].get("run_id") == context.run_id and jobs[0].get("head_sha") == reg.controller_sha
                 and jobs[0].get("status") == "in_progress" and jobs[0].get("conclusion") is None,
                 "PLATFORM_ONLY_JOB_REQUIRED")
        _environment(snapshot, reg)
        _approval(snapshot, reg, context, bind=True)
        checked = time.time() if now is None else now
        elapsed = time.monotonic() - started
        p.demand(type(checked) in (int, float) and math.isfinite(checked) and checked >= wall,
                 "CONTROL_PROBE_CLOCK_INVALID")
        p.demand(math.isfinite(elapsed) and 0 <= elapsed <= 120, "CONTROL_PROBE_READ_DEADLINE")
        return {"mode": "platform_only", "production_admission": False, "live_allowed": False,
            "paid_started": False, "provider_access": False, "run_id": context.run_id,
            "controller_sha": reg.controller_sha, "environment_verified": True,
            "owner_current_run_approval_verified": True, "hidden_protection_machine_verified": False,
            "campaign_attempt_consumed": False, "checked_at": checked,
            "evidence_sha256": hashlib.sha256(p.canonical(snapshot.records)).hexdigest()}
    except Blocked:
        raise
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OSError):
        raise Blocked("CONTROL_PROBE_INVALID") from None


def probe_existing_metadata(reader: ro.Reader | None = None) -> dict[str, Any]:
    """Anonymous observation only. Never probes or pretends to configure control."""
    env_name = "offerpilot-companion-ai-20261021"
    paths = frozenset({f"/environments/{env_name}", f"/actions/workflows/{DRY_WORKFLOW}",
                      *(f"/actions/runs/{anchor.run_id}/approvals" for anchor in KNOWN_DRY_ANCHORS)})
    source = ro.PublicReader(registered_paths=paths) if reader is None else reader
    snapshot = _Snapshot(source, paths)
    env = snapshot.obj(f"/environments/{env_name}")
    p.demand(env.get("id") == 24931188785 and env.get("name") == env_name, "PROBE_ENVIRONMENT_IDENTITY")
    workflow = snapshot.obj(f"/actions/workflows/{DRY_WORKFLOW}")
    p.demand(workflow.get("id") == DRY_WORKFLOW and workflow.get("path") == DRY_PATH, "PROBE_WORKFLOW_IDENTITY")
    anchors = []
    for anchor in KNOWN_DRY_ANCHORS:
        run = snapshot.obj(f"/actions/runs/{anchor.run_id}")
        p.demand(run.get("id") == anchor.run_id and run.get("workflow_id") == DRY_WORKFLOW
                 and run.get("head_sha") == anchor.head_sha and run.get("run_attempt") == 1
                 and run.get("status") == "completed" and run.get("conclusion") == "success",
                 "PROBE_RUN_IDENTITY")
        jobs = snapshot.pages(f"/actions/runs/{anchor.run_id}/jobs", "jobs")
        p.demand(len(jobs) == 1 and jobs[0].get("name") == anchor.job_name
                 and jobs[0].get("run_id") == anchor.run_id and jobs[0].get("head_sha") == anchor.head_sha
                 and jobs[0].get("status") == "completed" and jobs[0].get("conclusion") == "success",
                 "PROBE_JOB_IDENTITY")
        response = snapshot(f"/actions/runs/{anchor.run_id}/approvals")
        reviews = ro._json(response.body)
        wrapped = ro.Response(p.canonical({"reviews": reviews}), response.status, response.headers)
        ro._read(lambda _: wrapped, "")
        p.demand(type(reviews) is list, "PROBE_APPROVAL_SHAPE")
        anchors.append({"run_id": anchor.run_id, "head_sha": anchor.head_sha,
                        "job_name": anchor.job_name, "approval_record_count": len(reviews)})
    rules = env.get("protection_rules")
    p.demand(type(rules) is list, "PROBE_ENVIRONMENT_SHAPE")
    assert isinstance(rules, list)
    return {"mode": "public_platform_probe", "production_admission": False, "live_allowed": False,
        "configured_control_verified": False, "owner_current_run_approval_verified": False,
        "environment_id": env["id"], "environment_name": env_name,
        "protection_rule_types": [rule.get("type") for rule in rules],
        "can_admins_bypass": env.get("can_admins_bypass"),
        "deployment_branch_policy": env.get("deployment_branch_policy"), "dry_run_anchors": anchors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe existing public platform metadata; never admit production")
    parser.add_argument("--public-probe", action="store_true", required=True)
    parser.parse_args()
    try:
        print(json.dumps(probe_existing_metadata(), sort_keys=True))
        return 0
    except Blocked as exc:
        print(json.dumps({"mode": "public_platform_probe", "production_admission": False,
                          "live_allowed": False, "configured_control_verified": False, "error": str(exc)}))
        return 2
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OSError):
        print(json.dumps({"mode": "public_platform_probe", "production_admission": False,
                          "live_allowed": False, "configured_control_verified": False,
                          "error": "PUBLIC_PROBE_INVALID"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
