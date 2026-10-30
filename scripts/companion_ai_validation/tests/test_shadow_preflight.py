"""The six-review gate cannot reuse expired dialog approvals or omit batch four."""
from copy import deepcopy
import json

import pytest

from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import shadow_continuation as s
from scripts.companion_ai_validation.tests import test_companion_ai_preflight as old


@pytest.fixture(autouse=True)
def fixed_clock_and_registration(monkeypatch):
    monkeypatch.setattr(p, "WORKFLOW_ID", old.WORKFLOW_ID)
    monkeypatch.setattr(p, "ENVIRONMENT_ID", old.ENV_ID)
    monkeypatch.setattr(p, "BRANCH_POLICY_ID", 66)
    monkeypatch.setattr(p.time, "time", lambda: s.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(p.time, "monotonic", lambda: 1000.0)


def fixture(phase="before"):
    env, event, data = old.fixture(phase, count=10)
    event["head_commit"]["message"] = p.marker(old.H, shadow_review=True)
    data[f"git/commits/{old.R}"].data["message"] = p.marker(old.H, shadow_review=True)
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    # Six old pinned runs, then fourth H/R, then new H/R. IDs stay distinct.
    runs[:] = runs[:6] + [
        old.run(7, s.PREDECESSOR_HELPER_SHA, run_id=s.PREDECESSOR_HELPER_RUN_ID),
        old.run(8, s.PREDECESSOR_SOURCE_SHA, run_id=s.PREDECESSOR_RUN_ID),
        old.run(9, old.H, run_id=1), old.run(10, old.R, current=True)]
    for value in runs:
        jobs = old.jobs_for(value, phase)
        if value["id"] == s.PREDECESSOR_RUN_ID:
            for job in jobs:
                job["conclusion"] = "success"
        old.add_pages(data, f"actions/runs/{value['id']}/jobs?filter=all", "jobs", jobs)
    data[f"git/commits/{s.PREDECESSOR_HELPER_SHA}"] = p.Response({
        "sha": s.PREDECESSOR_HELPER_SHA, "tree": {"sha": s.PREDECESSOR_TREE_SHA}})
    data[f"git/commits/{s.PREDECESSOR_SOURCE_SHA}"] = p.Response({
        "sha": s.PREDECESSOR_SOURCE_SHA, "tree": {"sha": s.PREDECESSOR_TREE_SHA},
        "parents": [{"sha": s.PREDECESSOR_HELPER_SHA}],
        "message": p.marker(s.PREDECESSOR_HELPER_SHA)})
    data[f"actions/runs/{s.PREDECESSOR_RUN_ID}/approvals"] = deepcopy(data["actions/runs/10/approvals"])
    data[f"actions/artifacts/{s.PREDECESSOR_ARTIFACT_ID}"] = p.Response({
        "id": s.PREDECESSOR_ARTIFACT_ID, "name": f"companion-ai-{s.PREDECESSOR_SOURCE_SHA}",
        "size_in_bytes": s.ARCHIVE_SIZE, "digest": f"sha256:{s.ARCHIVE_SHA256}",
        "expired": False, "url": p.API + f"actions/artifacts/{s.PREDECESSOR_ARTIFACT_ID}",
        "workflow_run": {"id": s.PREDECESSOR_RUN_ID, "repository_id": old.REPO_ID,
                         "head_repository_id": old.REPO_ID, "head_branch": p.BRANCH,
                         "head_sha": s.PREDECESSOR_SOURCE_SHA}})
    return env, event, data


def execute(values, phase="before"):
    env, event, data = values
    return p.preflight(phase=phase, env=env, event=event,
                       read=lambda path: deepcopy(data[path]), shadow_review=True)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_four_batch_lineage_new_marker_and_fresh_owner_pass(phase):
    assert execute(fixture(phase), phase)["ok"]


@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", s.ALL_HISTORICAL_IDENTITIES)
def test_every_historical_identity_rejected(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, shadow_review=True)


@pytest.mark.parametrize("field", ["event", "commit"])
def test_old_marker_is_never_shadow_authorization(field):
    values = fixture()
    if field == "event":
        values[1]["head_commit"]["message"] = p.marker(old.H)
    else:
        values[2][f"git/commits/{old.R}"].data["message"] = p.marker(old.H)
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes"])
def test_fourth_artifact_metadata_must_match(field):
    values = fixture()
    values[2][f"actions/artifacts/{s.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked, match="PREDECESSOR_ARTIFACT_MISMATCH"):
        execute(values)


def test_fourth_paid_run_cannot_be_treated_as_unpaid():
    values = fixture()
    jobs = values[2][p.page_path(f"actions/runs/{s.PREDECESSOR_RUN_ID}/jobs?filter=all", 1)].data["jobs"]
    next(job for job in jobs if job["name"] == p.PAID_JOB)["conclusion"] = "skipped"
    with pytest.raises(p.Blocked, match="PREDECESSOR_RUN_MISMATCH"):
        execute(values)


@pytest.mark.parametrize("which", ["current", "historical"])
def test_owner_approval_missing_never_inferred_from_prior_success(which):
    values = fixture("paid")
    run_id = 10 if which == "current" else s.PREDECESSOR_RUN_ID
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked):
        execute(values, "paid")


@pytest.mark.parametrize("remaining", [0, 1199, -1])
def test_shadow_window_requires_full_twenty_minutes(monkeypatch, remaining):
    monkeypatch.setattr(p.time, "time", lambda: s.PRICE_VALID_UNTIL - remaining)
    with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
        execute(fixture())


def test_shadow_admission_is_new_lineage_not_old_deadline(tmp_path):
    env, _, _ = fixture("paid")
    ctx = p.validate_context(env, shadow_review=True)
    path = tmp_path / "admission.json"
    p.write_admission(str(path), ctx)
    admission = json.loads(path.read_text())
    s.validate_lineage(admission)
    assert admission["prior_request_count"] == 48
    assert admission["prior_settled_micro_cny"] == 411934
    assert admission["correction_ordinal"] == 4
    with pytest.raises(p.lineage.ContinuationError):
        p.lineage.validate_lineage(admission)


def test_slow_admission_fsync_cannot_extend_quote(monkeypatch, tmp_path):
    env, _, _ = fixture("paid")
    ctx = p.validate_context(env, shadow_review=True)
    real = p.os.fsync
    def late(fd):
        real(fd)
        monkeypatch.setattr(p.time, "time", lambda: s.PRICE_VALID_UNTIL)
    monkeypatch.setattr(p.os, "fsync", late)
    with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
        p.write_admission(str(tmp_path / "admission.json"), ctx)


def test_shadow_cli_paid_writes_only_fresh_four_batch_admission(tmp_path, monkeypatch):
    env, event, data = fixture("paid")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    calls = []

    def reader(token, *, shadow_review=False):
        assert shadow_review is True
        def read(path):
            calls.append(path)
            return deepcopy(data[path])
        return read

    monkeypatch.setattr(p, "GitHubReader", reader)
    assert p.main(["--shadow-review", "--phase", "paid", "--admission-file", str(admission)]) == 0
    s.validate_lineage(json.loads(admission.read_text()))
    assert f"actions/artifacts/{s.PREDECESSOR_ARTIFACT_ID}" in calls
    assert "actions/runs/10/approvals" in calls


def test_shadow_cli_cannot_write_admission_without_current_owner(tmp_path, monkeypatch):
    env, event, data = fixture("paid")
    data["actions/runs/10/approvals"].data.clear()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(p, "GitHubReader", lambda token, **kwargs: data.__getitem__)
    assert p.main(["--shadow-review", "--phase", "paid", "--admission-file", str(admission)]) == 1
    assert not admission.exists()


def test_workflow_uses_same_shadow_profile_in_both_gates_and_live_entrypoint():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / p.WORKFLOW).read_text())
    commands = [step.get("run", "") for job in workflow["jobs"].values()
                for step in job["steps"]]
    for fragment in ("preflight --phase before", "preflight --phase paid", ".run --live"):
        selected = [command for command in commands if fragment in command]
        assert len(selected) == 1 and "--shadow-quote-v2" in selected[0]
    paid = workflow["jobs"]["paid"]
    assert paid["environment"] == p.ENVIRONMENT
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    secret_steps = [step for job in workflow["jobs"].values() for step in job["steps"]
                    if "OFFERPILOT_COMPANION_DEEPSEEK_20261021" in step.get("env", {})]
    assert len(secret_steps) == 1 and ".run --live --shadow-quote-v2" in secret_steps[0]["run"]


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_renewed_shadow_exact_twenty_minute_boundary_passes(monkeypatch, phase):
    # Renewal is a new deadline, not a relaxation of the full-session requirement.
    assert s.PRICE_VALID_UNTIL == 1793404800  # 2026-10-31T00:00:00Z
    monkeypatch.setattr(p.time, "time", lambda: s.PRICE_VALID_UNTIL - 1200)
    assert execute(fixture(phase), phase)["ok"]
