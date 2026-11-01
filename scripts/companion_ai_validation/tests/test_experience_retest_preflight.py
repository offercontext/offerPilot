"""New runtime experience admission inherits all nine immutable paid batches."""
from copy import deepcopy
import json
import pytest
from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import experience_retest_continuation as q
from scripts.companion_ai_validation.tests import test_experience_preflight as single
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
    message = p.marker(old.H, experience_retest=True)
    event["head_commit"]["message"] = message
    data[f"git/commits/{old.R}"].data["message"] = message
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    ninth = old.run(18, q.PREDECESSOR_SOURCE_SHA, run_id=q.PREDECESSOR_RUN_ID)
    runs[:] = runs[:16] + [old.run(17, q.PREDECESSOR_HELPER_SHA, run_id=q.PREDECESSOR_HELPER_RUN_ID),
                           ninth, old.run(19, old.H, run_id=1), old.run(20, old.R, current=True)]
    env["GITHUB_RUN_ID"] = "20"
    data["actions/runs/20"] = p.Response(deepcopy(runs[-1]))
    data["actions/runs/20/approvals"] = deepcopy(data["actions/runs/18/approvals"])
    old.add_pages(data, old.RUNS_PATH, "workflow_runs", runs)
    for value in runs[16:]:
        jobs = old.jobs_for(value, phase)
        if value["id"] == q.PREDECESSOR_RUN_ID:
            for job in jobs:
                job["conclusion"] = "success"
        old.add_pages(data, f"actions/runs/{value['id']}/jobs?filter=all", "jobs", jobs)
    data[f"git/commits/{q.PREDECESSOR_HELPER_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_HELPER_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA}})
    data[f"git/commits/{q.PREDECESSOR_SOURCE_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_SOURCE_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA},
        "parents": [{"sha": q.PREDECESSOR_HELPER_SHA}],
        "message": p.marker(q.PREDECESSOR_HELPER_SHA, experience=True)})
    data[f"actions/runs/{q.PREDECESSOR_RUN_ID}/approvals"] = deepcopy(data["actions/runs/20/approvals"])
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
                       read=lambda path: deepcopy(data[path]), experience_retest=True)

@pytest.mark.parametrize("phase", ["before", "paid"])
def test_exact_nine_batch_history_admitted(phase):
    assert execute(fixture(phase), phase)["ok"]

@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", q.ALL_HISTORICAL_IDENTITIES)
def test_old_identity_never_arms(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, experience_retest=True)

@pytest.mark.parametrize("run_id", [20, q.PREDECESSOR_RUN_ID])
def test_new_and_historical_reviews_required_independently(run_id):
    values = fixture("paid")
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")

@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes", "id", "workflow_run"])
def test_ninth_artifact_tamper_blocked(field):
    values = fixture()
    values[2][f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked):
        execute(values)

@pytest.mark.parametrize("mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True},
                                   {"shadow_projection": True}, {"shadow_single_target": True},
                                   {"experience": True}])
def test_old_marker_not_experience(mode):
    values = fixture()
    values[1]["head_commit"]["message"] = p.marker(old.H, **mode)
    with pytest.raises(p.Blocked):
        execute(values)

def test_experience_admission_is_new_lineage(tmp_path):
    ctx = p.validate_context(fixture("paid")[0], experience_retest=True)
    target = tmp_path / "admission.json"
    p.write_admission(str(target), ctx)
    record = json.loads(target.read_text())
    q.validate_lineage(record)
    assert record["prior_request_count"] == 73
    assert record["prior_settled_micro_cny"] == 558124
    assert record["correction_ordinal"] == 9
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
        p.validate_context(fixture()[0], experience_retest=True, experience=True)
    assert p.main(["--phase", "paid", "--experience-retest", "--experience"]) == 1

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
    args = ["--phase", phase, "--experience-retest"]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 0
    assert f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}" in read_paths
    assert admission.exists() is (phase == "paid")


def test_history_extends_without_rewriting_old_failure():
    old_history = p.historical_batches(experience=True)
    history = p.historical_batches(experience_retest=True)
    assert history[:-1] == old_history
    assert len(history) == 9
    assert history[-2].conclusion == "failure"
    assert history[-1].conclusion == "success"  # Workflow mechanics, not semantics.
    assert history[-1].request_marker == p.marker(q.PREDECESSOR_HELPER_SHA, experience=True)


def test_exact_retest_marker_not_accepted_as_old_mode():
    value = p.marker(old.H, experience_retest=True)
    assert "one-shot-20261101-product-experience-retest-v1-9" in value
    assert str(q.PREDECESSOR_RUN_ID) in value and q.LEDGER_SHA256 in value
    assert not p.exact_marker(value, old.H, experience=True)
    assert p.exact_marker(value + "\n", old.H, experience_retest=True)
    assert not p.exact_marker(value + "\n\n", old.H, experience_retest=True)


@pytest.mark.parametrize("other", ["shadow_review", "shadow_quote_v2", "shadow_projection",
                                    "shadow_single_target", "experience"])
def test_retest_rejects_all_other_modes(other):
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.marker(old.H, experience_retest=True, **{other: True})


def test_retest_reader_only_adds_pinned_artifact(monkeypatch):
    class StopRead(Exception):
        pass
    class Opener:
        def open(self, request, timeout):
            raise StopRead
    reader = p.GitHubReader("synthetic-token", experience_retest=True)
    reader.opener = Opener()
    with pytest.raises(p.Blocked, match="GITHUB_READ_UNAVAILABLE"):
        reader(f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}")
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        reader(f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID + 1}")
    old_reader = p.GitHubReader("synthetic-token", experience=True)
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        old_reader(f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}")
