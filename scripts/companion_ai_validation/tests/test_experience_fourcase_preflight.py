"""The four-case gate extends, rather than reopens, the settled retest."""
from copy import deepcopy
import json

import pytest

from scripts.companion_ai_validation import experience_fourcase_continuation as q
from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation.tests import test_companion_ai_preflight as old
from scripts.companion_ai_validation.tests import test_experience_retest_preflight as previous


@pytest.fixture(scope="module")
def verified_fourcase_fields():
    # Replay every immutable archive once. These tests mutate GitHub metadata,
    # never archive bytes; archive-corruption coverage retains real replay.
    return q.lineage_fields()


@pytest.fixture(autouse=True)
def replay_once(monkeypatch, verified_fourcase_fields):
    original = p.verified_lineage
    def verified(**kwargs):
        if kwargs == {"experience_fourcase": True}:
            return deepcopy(verified_fourcase_fields)
        return original(**kwargs)
    monkeypatch.setattr(p, "verified_lineage", verified)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(old, "WORKFLOW_ID", p.abandoned.WORKFLOW_ID)
    monkeypatch.setattr(p, "WORKFLOW_ID", p.abandoned.WORKFLOW_ID)
    monkeypatch.setattr(p, "ENVIRONMENT_ID", old.ENV_ID)
    monkeypatch.setattr(p, "BRANCH_POLICY_ID", 66)
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(p.time, "monotonic", lambda: 1000.0)


def fixture(phase="before"):
    env, event, data = previous.fixture(phase)
    message = p.marker(old.H, experience_fourcase=True)
    event["head_commit"]["message"] = message
    data[f"git/commits/{old.R}"].data["message"] = message
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    runs[-2:] = [old.run(33, q.PREDECESSOR_HELPER_SHA, run_id=q.PREDECESSOR_HELPER_RUN_ID),
                 old.run(34, q.PREDECESSOR_SOURCE_SHA, run_id=q.PREDECESSOR_RUN_ID),
                 old.run(35, old.H, run_id=1), old.run(36, old.R, run_id=20, current=True)]
    old.add_pages(data, old.RUNS_PATH, "workflow_runs", runs)
    for run in runs[-4:]:
        jobs = old.jobs_for(run, phase)
        if run["id"] == q.PREDECESSOR_RUN_ID:
            for job in jobs:
                job["conclusion"] = "success"
        old.add_pages(data, f"actions/runs/{run['id']}/jobs?filter=all", "jobs", jobs)
    data["actions/runs/20"] = p.Response(deepcopy(runs[-1]))
    data[f"git/commits/{q.PREDECESSOR_HELPER_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_HELPER_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA}})
    data[f"git/commits/{q.PREDECESSOR_SOURCE_SHA}"] = p.Response({
        "sha": q.PREDECESSOR_SOURCE_SHA, "tree": {"sha": q.PREDECESSOR_TREE_SHA},
        "parents": [{"sha": q.PREDECESSOR_HELPER_SHA}],
        "message": p.marker(q.PREDECESSOR_HELPER_SHA, experience_retest=True)})
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
                       read=lambda path: deepcopy(data[path]), experience_fourcase=True)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_exact_ten_batch_history_admitted(phase):
    assert execute(fixture(phase), phase)["ok"]


@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", q.ALL_HISTORICAL_IDENTITIES)
def test_old_identity_never_arms(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, experience_fourcase=True)


@pytest.mark.parametrize("run_id", [20, q.PREDECESSOR_RUN_ID])
def test_new_and_historical_reviews_required_independently(run_id):
    values = fixture("paid")
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")


@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes", "id", "workflow_run"])
def test_tenth_artifact_tamper_blocked(field):
    values = fixture()
    values[2][f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked):
        execute(values)


OLD_MODES = ({}, {"shadow_review": True}, {"shadow_quote_v2": True},
             {"shadow_projection": True}, {"shadow_single_target": True},
             {"experience": True}, {"experience_retest": True})


@pytest.mark.parametrize("mode", OLD_MODES)
def test_old_marker_not_fourcase(mode):
    values = fixture()
    values[1]["head_commit"]["message"] = p.marker(old.H, **mode)
    with pytest.raises(p.Blocked):
        execute(values)
    assert not p.exact_marker(p.marker(old.H, experience_fourcase=True), old.H, **mode)


@pytest.mark.parametrize("other", [next(iter(mode)) for mode in OLD_MODES if mode])
def test_fourcase_rejects_all_other_modes(other):
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.marker(old.H, experience_fourcase=True, **{other: True})
    assert p.main(["--phase", "paid", "--experience-fourcase", "--" + other.replace("_", "-")]) == 1


def test_new_admission_cannot_be_replayed_as_old_profile(tmp_path):
    ctx = p.validate_context(fixture("paid")[0], experience_fourcase=True)
    target = tmp_path / "admission.json"
    p.write_admission(str(target), ctx)
    record = json.loads(target.read_text())
    q.validate_lineage(record)
    assert record["correction_ordinal"] == 11
    with pytest.raises(p.lineage.ContinuationError):
        previous.q.validate_lineage(record)


@pytest.mark.parametrize("remaining", [1200, 1199, 0, -1])
def test_new_deadline_cannot_extend(monkeypatch, remaining):
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - remaining)
    if remaining == 1200:
        assert execute(fixture())["ok"]
    else:
        with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
            execute(fixture())


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_cli_exact_profile_plumbing(tmp_path, monkeypatch, phase):
    env, event, data = fixture(phase)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    source, admission = tmp_path / "event.json", tmp_path / "admission.json"
    source.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(source))
    paths = []
    def reader(token, **kwargs):
        assert kwargs == {"experience_fourcase": True}
        def read(path):
            paths.append(path)
            return deepcopy(data[path])
        return read
    monkeypatch.setattr(p, "GitHubReader", reader)
    args = ["--phase", phase, "--experience-fourcase"]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 0
    assert f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}" in paths
    assert admission.exists() is (phase == "paid")


def test_history_extends_without_rewriting_previous_batches():
    history = p.historical_batches(experience_fourcase=True)
    assert history[:-1] == p.historical_batches(experience_retest=True)
    assert len(history) == 10
    assert history[-3].conclusion == "failure"
    assert history[-1].request_marker == p.marker(q.PREDECESSOR_HELPER_SHA, experience_retest=True)
    value = p.marker(old.H, experience_fourcase=True)
    assert "one-shot-20261104-product-experience-fourcase-11" in value
    assert p.exact_marker(value + "\n", old.H, experience_fourcase=True)
    assert not p.exact_marker(value + "\n\n", old.H, experience_fourcase=True)


def test_reader_only_adds_pinned_artifact():
    class Opener:
        def open(self, request, timeout):
            raise AssertionError("offline sentinel")
    reader = p.GitHubReader("synthetic-token", experience_fourcase=True)
    reader.opener = Opener()
    for batch in p.historical_batches(experience_fourcase=True):
        with pytest.raises(p.Blocked, match="GITHUB_READ_UNAVAILABLE"):
            reader(f"actions/artifacts/{batch.artifact_id}")
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        reader(f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID + 1}")
    old_reader = p.GitHubReader("synthetic-token", experience_retest=True)
    old_reader.opener = Opener()
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        old_reader(f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}")


@pytest.mark.parametrize("field,value", [("head_sha", "f" * 40), ("conclusion", "success"),
                                         ("run_attempt", 2), ("run_number", 31)])
def test_abandoned_exception_remains_exact(field, value):
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    run = next(run for run in runs if run["id"] == p.abandoned.RUN_ID)
    run[field] = value
    with pytest.raises(p.Blocked):
        execute(values)


def test_unpinned_paid_history_rejected():
    values = fixture()
    data = values[2]
    jobs = data[p.page_path("actions/runs/1019/jobs?filter=all", 1)].data["jobs"]
    next(job for job in jobs if job["name"] == p.PAID_JOB)["conclusion"] = "success"
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        execute(values)
