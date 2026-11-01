"""Input projection: the exact settled sixth failure is history, never new approval."""
from copy import deepcopy
import json

import pytest

from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import shadow_projection_continuation as q
from scripts.companion_ai_validation.tests import test_shadow_quote_preflight as shadow
from scripts.companion_ai_validation.tests import test_companion_ai_preflight as old


@pytest.fixture(autouse=True)
def fixed_clock_and_registration(monkeypatch):
    monkeypatch.setattr(p, "WORKFLOW_ID", old.WORKFLOW_ID)
    monkeypatch.setattr(p, "ENVIRONMENT_ID", old.ENV_ID)
    monkeypatch.setattr(p, "BRANCH_POLICY_ID", 66)
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(p.time, "monotonic", lambda: 1000.0)


def fixture(phase="before"):
    env, event, data = shadow.fixture(phase)
    message = p.marker(old.H, shadow_projection=True)
    event["head_commit"]["message"] = message
    data[f"git/commits/{old.R}"].data["message"] = message
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    sixth = old.run(12, q.PREDECESSOR_SOURCE_SHA, run_id=q.PREDECESSOR_RUN_ID)
    sixth["conclusion"] = "failure"
    runs[:] = runs[:10] + [
        old.run(11, q.PREDECESSOR_HELPER_SHA, run_id=q.PREDECESSOR_HELPER_RUN_ID),
        sixth, old.run(13, old.H, run_id=1), old.run(14, old.R, current=True)]
    env["GITHUB_RUN_ID"] = "14"
    data["actions/runs/14"] = p.Response(deepcopy(runs[-1]))
    data["actions/runs/14/approvals"] = deepcopy(data["actions/runs/12/approvals"])
    old.add_pages(data, old.RUNS_PATH, "workflow_runs", runs)
    for value in runs[10:]:
        jobs = old.jobs_for(value, phase)
        if value["id"] == q.PREDECESSOR_RUN_ID:
            for job in jobs:
                job["conclusion"] = "failure" if job["name"] == p.PAID_JOB else "success"
        old.add_pages(data, f"actions/runs/{value['id']}/jobs?filter=all", "jobs", jobs)
    data[f"git/commits/{q.PREDECESSOR_HELPER_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_HELPER_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA}})
    data[f"git/commits/{q.PREDECESSOR_SOURCE_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_SOURCE_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA},
        "parents": [{"sha": q.PREDECESSOR_HELPER_SHA}],
        "message": p.marker(q.PREDECESSOR_HELPER_SHA, shadow_quote_v2=True)})
    data[f"actions/runs/{q.PREDECESSOR_RUN_ID}/approvals"] = deepcopy(data["actions/runs/14/approvals"])
    data[f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"] = p.Response({
        "id": q.PREDECESSOR_ARTIFACT_ID, "name": f"companion-ai-{q.PREDECESSOR_SOURCE_SHA}",
        "size_in_bytes": q.ARCHIVE_SIZE, "digest": f"sha256:{q.ARCHIVE_SHA256}",
        "expired": False, "url": p.API + f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}",
        "workflow_run": {"id": q.PREDECESSOR_RUN_ID, "repository_id": old.REPO_ID,
                         "head_repository_id": old.REPO_ID, "head_branch": p.BRANCH,
                         "head_sha": q.PREDECESSOR_SOURCE_SHA}})
    return env, event, data


def execute(values, phase="before"):
    env, event, data = values
    return p.preflight(phase=phase, env=env, event=event,
                       read=lambda path: deepcopy(data[path]), shadow_projection=True)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_complete_six_batch_history_and_new_current_pass(phase):
    assert execute(fixture(phase), phase)["ok"]


@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", q.ALL_HISTORICAL_IDENTITIES)
def test_all_old_identities_rejected(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, shadow_projection=True)


@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes", "id", "workflow_run"])
def test_sixth_artifact_identity_tamper_blocked(field):
    values = fixture()
    values[2][f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("job_name,conclusion", [
    (job, conclusion) for job in (p.OFFLINE_JOB, p.PREFLIGHT_JOB, p.PAID_JOB)
    for conclusion in ("success", "failure", "skipped", "cancelled", None)
    if conclusion != ("failure" if job == p.PAID_JOB else "success")
])
def test_sixth_only_exact_job_outcome_allowed(job_name, conclusion):
    values = fixture()
    jobs = values[2][p.page_path(f"actions/runs/{q.PREDECESSOR_RUN_ID}/jobs?filter=all", 1)].data["jobs"]
    next(j for j in jobs if j["name"] == job_name)["conclusion"] = conclusion
    with pytest.raises(p.Blocked, match="PREDECESSOR_RUN_MISMATCH"):
        execute(values)


@pytest.mark.parametrize("which", ["first", "sixth"])
def test_success_and_failure_run_statuses_not_interchangeable(which):
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    target = next(r for r in runs if r["id"] == (p.lineage.ORIGINAL_RUN_ID if which == "first" else q.PREDECESSOR_RUN_ID))
    target["conclusion"] = "failure" if which == "first" else "success"
    with pytest.raises(p.Blocked, match="PREDECESSOR_RUN_MISMATCH"):
        execute(values)


@pytest.mark.parametrize("run_id", [14, q.PREDECESSOR_RUN_ID])
def test_current_and_historical_owner_reviews_independent(run_id):
    values = fixture("paid")
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")


@pytest.mark.parametrize("old_mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True}])
@pytest.mark.parametrize("field", ["event", "commit"])
def test_cross_protocol_marker_rejected(old_mode, field):
    values = fixture()
    message = p.marker(old.H, **old_mode)
    if field == "event":
        values[1]["head_commit"]["message"] = message
    else:
        values[2][f"git/commits/{old.R}"].data["message"] = message
    with pytest.raises(p.Blocked):
        execute(values)


def test_protocols_mutually_exclusive():
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.validate_context(fixture()[0], shadow_review=True, shadow_projection=True)
    assert p.main(["--phase", "paid", "--shadow-review", "--shadow-projection"]) == 1


@pytest.mark.parametrize("remaining", [1200, 1199, 0, -1])
def test_new_deadline_full_twenty_minutes(monkeypatch, remaining):
    assert q.PRICE_VALID_UNTIL == 1793577600
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - remaining)
    if remaining == 1200:
        assert execute(fixture())["ok"]
    else:
        with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
            execute(fixture())


def test_new_admission_carries_exact_sixth_settlement(tmp_path):
    ctx = p.validate_context(fixture("paid")[0], shadow_projection=True)
    path = tmp_path / "admission.json"
    p.write_admission(str(path), ctx)
    record = json.loads(path.read_text())
    q.validate_lineage(record)
    assert record["prior_request_count"] == 52
    assert record["prior_settled_micro_cny"] == 419388
    assert record["correction_ordinal"] == 6
    with pytest.raises(p.lineage.ContinuationError):
        shadow.q.validate_lineage(record)


def test_fsync_expiry_refuses_success(monkeypatch, tmp_path):
    ctx = p.validate_context(fixture()[0], shadow_projection=True)
    real = p.os.fsync
    def late(fd):
        real(fd)
        monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL)
    monkeypatch.setattr(p.os, "fsync", late)
    with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
        p.write_admission(str(tmp_path / "admission.json"), ctx)


def test_cli_paid_reads_sixth_and_current_approval(tmp_path, monkeypatch):
    env, event, data = fixture("paid")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path, output = tmp_path / "event.json", tmp_path / "admission.json"
    path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    calls = []
    def reader(token, *, shadow_projection=False):
        assert shadow_projection is True
        def read(path):
            calls.append(path)
            return deepcopy(data[path])
        return read
    monkeypatch.setattr(p, "GitHubReader", reader)
    assert p.main(["--phase", "paid", "--shadow-projection", "--admission-file", str(output)]) == 0
    q.validate_lineage(json.loads(output.read_text()))
    assert f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}" in calls
    assert "actions/runs/14/approvals" in calls


@pytest.mark.parametrize("mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True}, {"shadow_projection": True}])
def test_sixth_artifact_reader_allowlist_is_protocol_scoped(monkeypatch, mode):
    class Reply:
        status = 200
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, limit):
            return b'{}'
    class OfflineOpener:
        def open(self, request, timeout):
            assert request.full_url == p.API + f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"
            return Reply()
    monkeypatch.setattr(p, "build_opener", lambda *args: OfflineOpener())
    reader = p.GitHubReader("synthetic-never-network", **mode)
    path = f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"
    if mode.get("shadow_projection"):
        assert reader(path).data == {}
    else:
        with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
            reader(path)


def test_sixth_exact_ledger_verification_cannot_be_omitted(monkeypatch):
    def corrupt():
        raise p.lineage.ContinuationError("SYNTHETIC_CORRUPT_FIFTH")
    monkeypatch.setattr(q, "lineage_fields", corrupt)
    with pytest.raises(p.Blocked, match="SYNTHETIC_CORRUPT_FIFTH"):
        execute(fixture())


def test_arbitrary_additional_paid_failure_is_not_the_pinned_sixth():
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    # Reidentify the exact sixth run while retaining its failed paid job shape.
    value = next(r for r in runs if r["id"] == q.PREDECESSOR_RUN_ID)
    prior = value["id"]
    value["id"] = 999999999
    jobs = values[2][p.page_path(f"actions/runs/{prior}/jobs?filter=all", 1)].data["jobs"]
    for job in jobs:
        job["run_id"] = value["id"]
    old.add_pages(values[2], f"actions/runs/{value['id']}/jobs?filter=all", "jobs", jobs)
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        execute(values)


@pytest.mark.parametrize("flags", [
    {"shadow_quote_v2": True, "shadow_projection": True},
    {"shadow_review": True, "shadow_projection": True},
    {"shadow_review": True, "shadow_quote_v2": True},
    {"shadow_projection": 1}, {"shadow_projection": "true"},
    {"shadow_quote_v2": 0}, {"shadow_review": None},
])
def test_profile_flags_exact_bool_and_exclusive(flags):
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.validate_context(fixture()[0], **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.Context(old.H, old.R, 14, old.REPO_ID, **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.marker(old.H, **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.GitHubReader("synthetic-no-network", **flags)


def test_sixth_failure_forbidden_to_prior_quote_protocol():
    env, event, data = fixture()
    event["head_commit"]["message"] = p.marker(old.H, shadow_quote_v2=True)
    data[f"git/commits/{old.R}"].data["message"] = event["head_commit"]["message"]
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        p.preflight(phase="before", env=env, event=event, read=data.__getitem__, shadow_quote_v2=True)
