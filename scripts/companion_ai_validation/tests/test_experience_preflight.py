"""New runtime experience admission inherits all eight immutable paid batches."""
from copy import deepcopy
import json
import pytest
from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import experience_continuation as q
from scripts.companion_ai_validation.tests import test_single_target_preflight as single
from scripts.companion_ai_validation.tests import test_companion_ai_preflight as old

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(p, "WORKFLOW_ID", old.WORKFLOW_ID)
    monkeypatch.setattr(p, "ENVIRONMENT_ID", old.ENV_ID)
    monkeypatch.setattr(p, "BRANCH_POLICY_ID", 66)
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(p.time, "monotonic", lambda: 1000.0)

def fixture(phase="before"):
    env, event, data = single.fixture(phase)
    message = p.marker(old.H, experience=True)
    event["head_commit"]["message"] = message
    data[f"git/commits/{old.R}"].data["message"] = message
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    eighth = old.run(16, q.PREDECESSOR_SOURCE_SHA, run_id=q.PREDECESSOR_RUN_ID)
    eighth["conclusion"] = "failure"
    runs[:] = runs[:14] + [old.run(15, q.PREDECESSOR_HELPER_SHA, run_id=q.PREDECESSOR_HELPER_RUN_ID),
                           eighth, old.run(17, old.H, run_id=1), old.run(18, old.R, current=True)]
    env["GITHUB_RUN_ID"] = "18"
    data["actions/runs/18"] = p.Response(deepcopy(runs[-1]))
    data["actions/runs/18/approvals"] = deepcopy(data["actions/runs/16/approvals"])
    old.add_pages(data, old.RUNS_PATH, "workflow_runs", runs)
    for value in runs[14:]:
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
        "message": p.marker(q.PREDECESSOR_HELPER_SHA, shadow_single_target=True)})
    data[f"actions/runs/{q.PREDECESSOR_RUN_ID}/approvals"] = deepcopy(data["actions/runs/18/approvals"])
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
                       read=lambda path: deepcopy(data[path]), experience=True)

@pytest.mark.parametrize("phase", ["before", "paid"])
def test_exact_eight_batch_history_admitted(phase):
    assert execute(fixture(phase), phase)["ok"]

@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", q.ALL_HISTORICAL_IDENTITIES)
def test_old_identity_never_arms(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, experience=True)

@pytest.mark.parametrize("run_id", [18, q.PREDECESSOR_RUN_ID])
def test_new_and_historical_reviews_required_independently(run_id):
    values = fixture("paid")
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")

@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes", "id", "workflow_run"])
def test_eighth_artifact_tamper_blocked(field):
    values = fixture()
    values[2][f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked):
        execute(values)

@pytest.mark.parametrize("mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True},
                                   {"shadow_projection": True}, {"shadow_single_target": True}])
def test_old_marker_not_experience(mode):
    values = fixture()
    values[1]["head_commit"]["message"] = p.marker(old.H, **mode)
    with pytest.raises(p.Blocked):
        execute(values)

def test_experience_admission_is_new_lineage(tmp_path):
    ctx = p.validate_context(fixture("paid")[0], experience=True)
    target = tmp_path / "admission.json"
    p.write_admission(str(target), ctx)
    record = json.loads(target.read_text())
    q.validate_lineage(record)
    assert record["prior_request_count"] == 61
    assert record["prior_settled_micro_cny"] == 458256
    assert record["correction_ordinal"] == 8
    with pytest.raises(p.lineage.ContinuationError):
        single.q.validate_lineage(record)

@pytest.mark.parametrize("remaining", [1200, 1199, 0, -1])
def test_new_deadline_cannot_extend(monkeypatch, remaining):
    assert q.PRICE_VALID_UNTIL == 1793664000
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - remaining)
    if remaining == 1200:
        assert execute(fixture())["ok"]
    else:
        with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
            execute(fixture())

def test_conflicting_modes_fail_closed():
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.validate_context(fixture()[0], experience=True, shadow_single_target=True)
    assert p.main(["--phase", "paid", "--experience", "--shadow-single-target"]) == 1

@pytest.mark.parametrize("phase", ["before", "paid"])
def test_cli_exact_new_profile_uses_new_artifact(tmp_path, monkeypatch, phase):
    env, event, data = fixture(phase)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    source, admission = tmp_path / "event.json", tmp_path / "admission.json"
    source.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    read_paths = []
    def read(path):
        read_paths.append(path)
        return deepcopy(data[path])
    monkeypatch.setattr(p, "GitHubReader", lambda token, **kwargs: read)
    args = ["--phase", phase, "--experience"]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 0
    assert f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}" in read_paths
    assert admission.exists() is (phase == "paid")
