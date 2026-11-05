"""Single-target protocol: seven immutable batches and a fresh owner-reviewed request."""
from copy import deepcopy
import json

import pytest

from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import shadow_single_target_continuation as q
from scripts.companion_ai_validation.tests import test_projection_preflight as shadow
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
    message = p.marker(old.H, shadow_single_target=True)
    event["head_commit"]["message"] = message
    data[f"git/commits/{old.R}"].data["message"] = message
    runs = data[p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    seventh = old.run(14, q.PREDECESSOR_SOURCE_SHA, run_id=q.PREDECESSOR_RUN_ID)
    seventh["conclusion"] = "success"
    runs[:] = runs[:12] + [
        old.run(13, q.PREDECESSOR_HELPER_SHA, run_id=q.PREDECESSOR_HELPER_RUN_ID),
        seventh, old.run(15, old.H, run_id=1), old.run(16, old.R, current=True)]
    env["GITHUB_RUN_ID"] = "16"
    data["actions/runs/16"] = p.Response(deepcopy(runs[-1]))
    data["actions/runs/16/approvals"] = deepcopy(data["actions/runs/14/approvals"])
    old.add_pages(data, old.RUNS_PATH, "workflow_runs", runs)
    for value in runs[12:]:
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
        "message": p.marker(q.PREDECESSOR_HELPER_SHA, shadow_projection=True)})
    data[f"actions/runs/{q.PREDECESSOR_RUN_ID}/approvals"] = deepcopy(data["actions/runs/16/approvals"])
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
                       read=lambda path: deepcopy(data[path]), shadow_single_target=True)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_complete_seven_batch_history_and_new_current_pass(phase):
    assert execute(fixture(phase), phase)["ok"]


@pytest.mark.parametrize("role", [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize("identity", q.ALL_HISTORICAL_IDENTITIES)
def test_all_old_identities_rejected(role, identity):
    env, _, _ = fixture()
    env[role] = identity
    with pytest.raises(p.Blocked, match="EXACT_SHA_APPROVAL_MISSING"):
        p.validate_context(env, shadow_single_target=True)


@pytest.mark.parametrize("field", ["digest", "expired", "size_in_bytes", "id", "workflow_run"])
def test_seventh_artifact_identity_tamper_blocked(field):
    values = fixture()
    values[2][f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}"].data[field] = None
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("job_name,conclusion", [
    (job, conclusion) for job in (p.OFFLINE_JOB, p.PREFLIGHT_JOB, p.PAID_JOB)
    for conclusion in ("success", "failure", "skipped", "cancelled", None)
    if conclusion != "success"
])
def test_seventh_only_exact_job_outcome_allowed(job_name, conclusion):
    values = fixture()
    jobs = values[2][p.page_path(f"actions/runs/{q.PREDECESSOR_RUN_ID}/jobs?filter=all", 1)].data["jobs"]
    next(j for j in jobs if j["name"] == job_name)["conclusion"] = conclusion
    with pytest.raises(p.Blocked, match="PREDECESSOR_RUN_MISMATCH"):
        execute(values)


@pytest.mark.parametrize("which", ["first", "seventh"])
def test_success_and_failure_run_statuses_not_interchangeable(which):
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    target = next(r for r in runs if r["id"] == (p.lineage.ORIGINAL_RUN_ID if which == "first" else q.PREDECESSOR_RUN_ID))
    target["conclusion"] = "failure"
    with pytest.raises(p.Blocked, match="PREDECESSOR_RUN_MISMATCH"):
        execute(values)


@pytest.mark.parametrize("run_id", [16, q.PREDECESSOR_RUN_ID])
def test_current_and_historical_owner_reviews_independent(run_id):
    values = fixture("paid")
    values[2][f"actions/runs/{run_id}/approvals"].data.clear()
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")


@pytest.mark.parametrize("old_mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True}, {"shadow_projection": True}])
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
        p.validate_context(fixture()[0], shadow_review=True, shadow_single_target=True)
    assert p.main(["--phase", "paid", "--shadow-review", "--shadow-single-target"]) == 1


@pytest.mark.parametrize("remaining", [1200, 1199, 0, -1])
def test_new_deadline_full_twenty_minutes(monkeypatch, remaining):
    assert q.PRICE_VALID_UNTIL == 1793577600
    monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL - remaining)
    if remaining == 1200:
        assert execute(fixture())["ok"]
    else:
        with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
            execute(fixture())


def test_new_admission_carries_exact_seventh_settlement(tmp_path):
    ctx = p.validate_context(fixture("paid")[0], shadow_single_target=True)
    path = tmp_path / "admission.json"
    p.write_admission(str(path), ctx)
    record = json.loads(path.read_text())
    q.validate_lineage(record)
    assert record["prior_request_count"] == 58
    assert record["prior_settled_micro_cny"] == 429280
    assert record["correction_ordinal"] == 7
    with pytest.raises(p.lineage.ContinuationError):
        shadow.q.validate_lineage(record)


def test_fsync_expiry_refuses_success(monkeypatch, tmp_path):
    ctx = p.validate_context(fixture()[0], shadow_single_target=True)
    real = p.os.fsync
    def late(fd):
        real(fd)
        monkeypatch.setattr(p.time, "time", lambda: q.PRICE_VALID_UNTIL)
    monkeypatch.setattr(p.os, "fsync", late)
    with pytest.raises(p.Blocked, match="PRICE_REVIEW_EXPIRED"):
        p.write_admission(str(tmp_path / "admission.json"), ctx)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_cli_reads_seventh_and_requires_current_approval_only_when_paid(tmp_path, monkeypatch, phase):
    env, event, data = fixture(phase)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path, output = tmp_path / "event.json", tmp_path / "admission.json"
    path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    calls = []
    def reader(token, *, shadow_single_target=False):
        assert shadow_single_target is True
        def read(path):
            calls.append(path)
            return deepcopy(data[path])
        return read
    monkeypatch.setattr(p, "GitHubReader", reader)
    args = ["--phase", phase, "--shadow-single-target"]
    if phase == "paid":
        args += ["--admission-file", str(output)]
    assert p.main(args) == 0
    if phase == "paid":
        q.validate_lineage(json.loads(output.read_text()))
    else:
        assert not output.exists()
    assert f"actions/artifacts/{q.PREDECESSOR_ARTIFACT_ID}" in calls
    assert ("actions/runs/16/approvals" in calls) == (phase == "paid")


@pytest.mark.parametrize("mode", [{}, {"shadow_review": True}, {"shadow_quote_v2": True}, {"shadow_projection": True}, {"shadow_single_target": True}])
def test_seventh_artifact_reader_allowlist_is_protocol_scoped(monkeypatch, mode):
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
    if mode.get("shadow_single_target"):
        assert reader(path).data == {}
    else:
        with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
            reader(path)


def test_seventh_exact_ledger_verification_cannot_be_omitted(monkeypatch):
    def corrupt():
        raise p.lineage.ContinuationError("SYNTHETIC_CORRUPT_SEVENTH")
    monkeypatch.setattr(q, "lineage_fields", corrupt)
    with pytest.raises(p.Blocked, match="SYNTHETIC_CORRUPT_SEVENTH"):
        execute(fixture())


def test_arbitrary_additional_paid_failure_is_not_the_pinned_seventh():
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    # Reidentify the exact seventh run while retaining its consumed paid job shape.
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
    {"shadow_quote_v2": True, "shadow_single_target": True},
    {"shadow_review": True, "shadow_single_target": True},
    {"shadow_review": True, "shadow_quote_v2": True},
    {"shadow_single_target": 1}, {"shadow_single_target": "true"},
    {"shadow_quote_v2": 0}, {"shadow_review": None},
])
def test_profile_flags_exact_bool_and_exclusive(flags):
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.validate_context(fixture()[0], **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.Context(old.H, old.R, 16, old.REPO_ID, **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.marker(old.H, **flags)
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.GitHubReader("synthetic-no-network", **flags)


def test_seventh_paid_batch_forbidden_to_prior_projection_protocol():
    env, event, data = fixture()
    event["head_commit"]["message"] = p.marker(old.H, shadow_projection=True)
    data[f"git/commits/{old.R}"].data["message"] = event["head_commit"]["message"]
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        p.preflight(phase="before", env=env, event=event, read=data.__getitem__, shadow_projection=True)


@pytest.mark.parametrize("attempt", ["2", "0", "", "01"])
def test_first_attempt_required(attempt):
    env, _, _ = fixture()
    env["GITHUB_RUN_ATTEMPT"] = attempt
    with pytest.raises(p.Blocked, match="RERUN_FORBIDDEN"):
        p.validate_context(env, shadow_single_target=True)


@pytest.mark.parametrize("damage", ["parent", "tree", "sha", "message"])
def test_request_must_be_exact_empty_child_of_helper(damage):
    values = fixture()
    commit = values[2][f"git/commits/{old.R}"].data
    if damage == "parent":
        commit["parents"] = [{"sha": "e" * 40}]
    elif damage == "tree":
        commit["tree"] = {"sha": "e" * 40}
    elif damage == "sha":
        commit["sha"] = "e" * 40
    else:
        commit["message"] += "\nextra approval text"
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("damage", ["missing", "pending", "rerun", "new_paid"])
def test_historical_run_closure_and_complete_history_required(damage):
    values = fixture()
    runs = values[2][p.page_path(old.RUNS_PATH, 1)].data["workflow_runs"]
    target = next(row for row in runs if row["id"] == q.PREDECESSOR_RUN_ID)
    if damage == "missing":
        runs.remove(target)
        old.add_pages(values[2], old.RUNS_PATH, "workflow_runs", runs)
    elif damage == "pending":
        target.update(status="in_progress", conclusion=None)
    elif damage == "rerun":
        target["run_attempt"] = 2
    else:
        # A nonhistorical helper's paid job is not authorized by this batch.
        jobs = values[2][p.page_path("actions/runs/1/jobs?filter=all", 1)].data["jobs"]
        next(job for job in jobs if job["name"] == p.PAID_JOB)["conclusion"] = "success"
    with pytest.raises(p.Blocked):
        execute(values)


def test_runner_rejects_old_profile_admission(tmp_path):
    from scripts.companion_ai_validation import run as runner
    env, _, _ = fixture("paid")
    ctx = p.validate_context(env, shadow_single_target=True)
    path = tmp_path / "new-admission.json"
    p.write_admission(str(path), ctx)
    record = json.loads(path.read_text())
    assert runner.check_admission(record, env, q.PRICE_VALID_UNTIL - 3600,
                                   shadow_single_target=True) == old.R
    for mode in ({}, {"shadow_review": True}, {"shadow_quote_v2": True}, {"shadow_projection": True}):
        with pytest.raises(runner.GateError, match="admission_lineage"):
            runner.check_admission(record, env, q.PRICE_VALID_UNTIL - 3600, **mode)
    old_record = dict(record, **shadow.q.lineage_fields())
    with pytest.raises(runner.GateError, match="admission_lineage"):
        runner.check_admission(old_record, env, q.PRICE_VALID_UNTIL - 3600,
                               shadow_single_target=True)


@pytest.mark.parametrize("mode", ["shadow_review", "shadow_quote_v2", "shadow_projection"])
def test_new_profile_exclusive_with_every_previous_mode(mode):
    flags = {"shadow_single_target": True, mode: True}
    with pytest.raises(p.Blocked, match="PROTOCOL_CONFLICT"):
        p.validate_context(fixture()[0], **flags)
    assert p.main(["--phase", "paid", "--shadow-single-target", "--" + mode.replace("_", "-")]) == 1


def test_seven_batches_preserve_two_consumed_failures_and_independent_identity():
    batches = p.historical_batches(shadow_single_target=True)
    assert len(batches) == 7
    assert len(q.ALL_HISTORICAL_IDENTITIES) == 14
    assert [batch.conclusion for batch in batches] == [
        "success", "success", "success", "success", "failure", "failure", "success"]
    assert batches[-1].run_id == 41032423741
    assert batches[-1].helper_run_id == 41022003105
    assert batches[-1].artifact_id == 12968888464
    assert q.OPERATIONS != shadow.q.OPERATIONS
    assert q.OPERATIONS_SHA256 != shadow.q.OPERATIONS_SHA256


@pytest.mark.parametrize("elapsed", [121, 3600])
def test_runner_expired_admission_never_rejuvenated(tmp_path, elapsed):
    from scripts.companion_ai_validation import run as runner
    env, _, _ = fixture("paid")
    path = tmp_path / "admission.json"
    p.write_admission(str(path), p.validate_context(env, shadow_single_target=True))
    record = json.loads(path.read_text())
    with pytest.raises(runner.GateError, match="admission_expired"):
        runner.check_admission(record, env, q.PRICE_VALID_UNTIL - 3600 + elapsed,
                               shadow_single_target=True)


@pytest.mark.parametrize("job_name,phase", [("preflight", "before"), ("paid", "paid")])
def test_deployed_lineage_uses_frozen_project_interpreter(job_name, phase):
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[3]
    job = yaml.safe_load((root / p.WORKFLOW).read_text())["jobs"][job_name]
    steps = job["steps"]
    checks = [(i, step) for i, step in enumerate(steps)
              if f"preflight --phase {phase}" in step.get("run", "")]
    assert len(checks) == 1
    index, check = checks[0]
    assert check["run"].startswith("uv run --frozen python -m scripts.companion_ai_validation.preflight ")
    setup = [i for i, step in enumerate(steps)
             if step.get("uses") == "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e"
             and step.get("with") == {"version": "0.12.19"}]
    sync = [i for i, step in enumerate(steps) if step.get("run") == "uv sync --frozen"]
    assert len(setup) == len(sync) == 1 and setup[0] < sync[0] < index
    for step in steps[:index + 1]:
        assert "OFFERPILOT_COMPANION_DEEPSEEK_20261021" not in step.get("env", {})


def test_stdlib_only_interpreter_cannot_silently_skip_seventh_archive():
    # Match setup-python without project installation; never borrow pytest's site-packages.
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, "-S", "-c",
        "from scripts.companion_ai_validation.preflight import verified_lineage; "
        "verified_lineage(shadow_single_target=True)"], cwd=root,
        capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'regex'" in result.stderr
