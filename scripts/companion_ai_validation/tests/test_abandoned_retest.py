"""Narrow zero-start exception, including missing-data and cancellation negatives."""
from copy import deepcopy
import pytest
from scripts.companion_ai_validation import abandoned_retest as a, preflight as p
from scripts.companion_ai_validation.tests import test_experience_retest_preflight as pf


def snapshot():
    value = deepcopy(a.verified_evidence()["after"])
    return value["run"], p.validate_jobs(value["run"], value["jobs"]["jobs"])


def test_exact_before_and_after_evidence_is_verified():
    data = a.verified_evidence()
    assert data["before"]["run"]["status"] == "waiting"
    assert data["after"]["run"]["conclusion"] == "cancelled"
    assert a.RUN_ID == 41290462304 and a.JOB_ID == 124804486152


@pytest.mark.parametrize("field,value", [
    ("id", 42), ("run_number", 31), ("run_attempt", 2), ("run_attempt", True),
    ("head_sha", "a"*40), ("workflow_id", 1), ("head_branch", "master"),
    ("status", "waiting"), ("conclusion", "success"), ("event", "workflow_dispatch"),
    ("repository", {"id": 1, "full_name": p.REPOSITORY}),
])
def test_changed_run_cannot_use_exception(field, value):
    run, jobs = snapshot()
    run[field] = value
    with pytest.raises(p.Blocked):
        a.validate_identity(run, jobs)


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("run_id", 1), ("run_attempt", 2), ("head_sha", "a"*40),
    ("status", "waiting"), ("conclusion", "skipped"),
    ("steps", [{"name": "checkout", "status": "completed"}]),
    ("steps", None), ("runner_id", None), ("runner_id", False), ("runner_id", 100),
    ("runner_name", None), ("runner_name", "runner"), ("runner_group_id", None),
    ("runner_group_name", "runner"), ("completed_at", None),
    ("started_at", "2026-11-04T02:48:32Z"),
])
def test_paid_started_unknown_or_wrong_identity_is_rejected(field, value):
    run, jobs = snapshot()
    jobs[p.PAID_JOB][field] = value
    with pytest.raises(p.Blocked):
        a.validate_identity(run, jobs)


@pytest.mark.parametrize("field", ["steps", "runner_id", "runner_name", "runner_group_id",
                                    "runner_group_name", "run_attempt", "completed_at"])
def test_missing_zero_start_metadata_is_rejected(field):
    run, jobs = snapshot()
    del jobs[p.PAID_JOB][field]
    with pytest.raises(p.Blocked):
        a.validate_identity(run, jobs)


@pytest.mark.parametrize("mutation", ["approval", "missing_artifact", "paid_artifact", "expired",
                                     "wrong_artifact", "digest", "owner", "artifact_bool_id"])
def test_approval_or_artifact_uncertainty_is_rejected(mutation):
    evidence = deepcopy(a.verified_evidence()["after"])
    approvals, artifacts = evidence["approvals"], evidence["artifacts"]["artifacts"]
    if mutation == "approval":
        approvals.append({"state": "approved"})
    elif mutation == "missing_artifact":
        artifacts.clear()
    elif mutation == "paid_artifact":
        artifacts.append({"id": 1, "name": f"companion-ai-{a.SOURCE_SHA}"})
    elif mutation == "expired":
        artifacts[0]["expired"] = True
    elif mutation == "wrong_artifact":
        artifacts[0]["name"] = "unexpected"
    elif mutation == "digest":
        artifacts[0]["digest"] = "sha256:" + "0"*64
    elif mutation == "owner":
        artifacts[0]["workflow_run"]["head_sha"] = "b"*40
    elif mutation == "artifact_bool_id":
        artifacts[0]["id"] = True
    with pytest.raises(p.Blocked):
        a.validate_auxiliary(approvals, artifacts)


def test_evidence_tamper_is_not_a_new_zero_start_authorization(tmp_path, monkeypatch):
    path = tmp_path / "evidence.json"
    path.write_bytes(a.EVIDENCE.read_bytes() + b" ")
    monkeypatch.setattr(a, "EVIDENCE", path)
    with pytest.raises(p.Blocked, match="ABANDONED_EVIDENCE_UNVERIFIED"):
        a.verified_evidence()


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("mutation", ["other_cancelled", "missing_abandoned", "new_approval",
                                     "missing_artifact_page", "changed_direct_run"])
def test_full_admission_requires_exact_zero_start_record(monkeypatch, phase, mutation):
    pf.clock.__wrapped__(monkeypatch)
    env, event, data = pf.fixture(phase)
    runs = data[p.page_path(pf.old.RUNS_PATH, 1)].data["workflow_runs"]
    if mutation == "other_cancelled":
        other = next(run for run in runs if run["id"] == 1019)
        other["conclusion"] = "cancelled"
        jobs = data[p.page_path("actions/runs/1019/jobs?filter=all", 1)].data["jobs"]
        next(job for job in jobs if job["name"] == p.PAID_JOB)["conclusion"] = "cancelled"
    elif mutation == "missing_abandoned":
        run = next(run for run in runs if run["id"] == a.RUN_ID)
        run["id"] += 1
        run["conclusion"] = "success"
        pf.old.add_pages(data, f"actions/runs/{run['id']}/jobs?filter=all", "jobs",
                         pf.old.jobs_for(run))
    elif mutation == "new_approval":
        data[f"actions/runs/{a.RUN_ID}/approvals"].data.append({"state": "approved"})
    elif mutation == "missing_artifact_page":
        data[p.page_path(f"actions/runs/{a.RUN_ID}/artifacts", 1)].data["total_count"] = 2
    elif mutation == "changed_direct_run":
        data[f"actions/runs/{a.RUN_ID}"].data["run_attempt"] = 2
    with pytest.raises(p.Blocked):
        pf.execute((env, event, data), phase)


@pytest.mark.parametrize("sha", [a.SOURCE_SHA, a.HELPER_SHA])
@pytest.mark.parametrize("key", [p.HELPER_VAR, p.REQUEST_VAR])
def test_cancelled_identity_is_not_a_fresh_request(monkeypatch, sha, key):
    pf.clock.__wrapped__(monkeypatch)
    env, _, _ = pf.fixture()
    env[key] = sha
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, experience_retest=True)


@pytest.mark.parametrize("new_mode", [False, True])
def test_artifact_endpoint_is_allowed_only_for_exact_new_mode(new_mode):
    class StopRead(Exception):
        pass
    class Opener:
        def open(self, request, timeout):
            raise StopRead
    reader = p.GitHubReader("synthetic-only", experience_retest=new_mode)
    reader.opener = Opener()
    exact = p.page_path(f"actions/runs/{a.RUN_ID}/artifacts", 1)
    with pytest.raises(p.Blocked, match="GITHUB_READ_UNAVAILABLE" if new_mode else "GITHUB_READ_PATH_DENIED"):
        reader(exact)
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        reader(p.page_path(f"actions/runs/{a.RUN_ID + 1}/artifacts", 1))
