"""Offline fixture coverage for the pinned fourth-batch GitHub approval chain."""

from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts.companion_ai_validation import preflight as p
from scripts.companion_ai_validation import broker
from scripts.companion_ai_validation import continuation as c

H, R, TREE = "a" * 40, "b" * 40, "c" * 40
REPO_ID, ENV_ID, WORKFLOW_ID = 241031113, 88, 99
RUNS_PATH = "actions/workflows/companion-real-ai.yml/runs"
ENV_PATH = f"environments/{p.ENVIRONMENT}"
POLICIES_PATH = f"{ENV_PATH}/deployment-branch-policies"


@pytest.fixture(autouse=True)
def pinned_fake_workflow(monkeypatch):
    monkeypatch.setattr(p, "WORKFLOW_ID", WORKFLOW_ID)
    monkeypatch.setattr(p, "ENVIRONMENT_ID", ENV_ID)
    monkeypatch.setattr(p, "BRANCH_POLICY_ID", 66)
    # Quote checks stay deterministic after the real, non-sliding deadline.
    monkeypatch.setattr(p.time, "time", lambda: broker.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(p.time, "monotonic", lambda: 1000.0)


def run(number, sha, current=False, run_id=None):
    return {
        "id": number if run_id is None else run_id, "run_number": number, "run_attempt": 1, "workflow_id": WORKFLOW_ID,
        "path": p.WORKFLOW, "event": "push", "head_branch": p.BRANCH, "head_sha": sha,
        "status": "in_progress" if current else "completed",
        "conclusion": None if current else "success",
        "repository": {"id": REPO_ID, "full_name": p.REPOSITORY},
        "head_repository": {"id": REPO_ID, "full_name": p.REPOSITORY},
    }


def jobs_for(value, phase="before"):
    current = value["status"] == "in_progress"
    result = []
    for index, name in enumerate((p.OFFLINE_JOB, p.PREFLIGHT_JOB, p.PAID_JOB), 1):
        active = p.PREFLIGHT_JOB if phase == "before" else p.PAID_JOB
        status = "in_progress" if current and name == active else "completed"
        conclusion = None if status == "in_progress" else "success"
        if not current:
            if name == p.PAID_JOB and value["id"] not in (c.ORIGINAL_RUN_ID, c.SECOND_RUN_ID, c.PREDECESSOR_RUN_ID):
                conclusion = "skipped"
            if name == p.PREFLIGHT_JOB and value["id"] not in (c.ORIGINAL_RUN_ID, c.SECOND_RUN_ID, c.PREDECESSOR_RUN_ID):
                conclusion = "skipped"
        if name == p.PAID_JOB and current and phase == "before":
            status, conclusion = "queued", None
        result.append({"id": value["id"] * 10 + index, "run_id": value["id"],
                       "head_sha": value["head_sha"], "name": name,
                       "status": status, "conclusion": conclusion})
    return result


def link(path, next_number):
    return f'<{p.API}{p.page_path(path, next_number)}>; rel="next"'


def add_pages(data, path, key, values):
    count = len(values)
    for index in range(max(1, (count + p.PAGE_SIZE - 1) // p.PAGE_SIZE)):
        data[p.page_path(path, index + 1)] = p.Response(
            {"total_count": count, key: values[index * 100:(index + 1) * 100]},
            link(path, index + 2) if (index + 1) * 100 < count else "",
        )


def historical_cases():
    original_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {c.ORIGINAL_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021")
    correction_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {c.SECOND_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021-correction-1\n"
        f"OfferPilot-Companion-AI-Predecessor: {c.ORIGINAL_RUN_ID}\n"
        f"OfferPilot-Companion-AI-Ledger: {c.ORIGINAL_LEDGER_SHA256}")
    third_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {c.PREDECESSOR_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021-correction-2\n"
        f"OfferPilot-Companion-AI-Predecessor: {c.SECOND_RUN_ID}\n"
        f"OfferPilot-Companion-AI-Ledger: {c.SECOND_LEDGER_SHA256}")
    return (
        {"run_id": c.ORIGINAL_RUN_ID, "helper_run_id": c.ORIGINAL_HELPER_RUN_ID,
         "source_sha": c.ORIGINAL_SOURCE_SHA, "helper_sha": c.ORIGINAL_HELPER_SHA,
         "tree_sha": c.ORIGINAL_TREE_SHA, "artifact_id": c.ORIGINAL_ARTIFACT_ID,
         "archive_size": c.ORIGINAL_ARCHIVE_SIZE, "archive_sha256": c.ORIGINAL_ARCHIVE_SHA256,
         "marker": original_marker},
        {"run_id": c.SECOND_RUN_ID, "helper_run_id": c.SECOND_HELPER_RUN_ID,
         "source_sha": c.SECOND_SOURCE_SHA, "helper_sha": c.SECOND_HELPER_SHA,
         "tree_sha": c.SECOND_TREE_SHA, "artifact_id": c.SECOND_ARTIFACT_ID,
         "archive_size": c.SECOND_ARCHIVE_SIZE, "archive_sha256": c.SECOND_ARCHIVE_SHA256,
         "marker": correction_marker},
        {"run_id": c.PREDECESSOR_RUN_ID, "helper_run_id": c.PREDECESSOR_HELPER_RUN_ID,
         "source_sha": c.PREDECESSOR_SOURCE_SHA, "helper_sha": c.PREDECESSOR_HELPER_SHA,
         "tree_sha": c.PREDECESSOR_TREE_SHA, "artifact_id": c.PREDECESSOR_ARTIFACT_ID,
         "archive_size": c.ARCHIVE_SIZE, "archive_sha256": c.ARCHIVE_SHA256,
         "marker": third_marker},
    )


def fixture(phase="before", count=8):
    assert count >= 8
    env = {
        p.HELPER_VAR: H, p.REQUEST_VAR: R, "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": p.REPOSITORY,
        "GITHUB_REF": f"refs/heads/{p.BRANCH}", "GITHUB_SHA": R,
        "GITHUB_WORKFLOW_REF": f"{p.REPOSITORY}/{p.WORKFLOW}@refs/heads/{p.BRANCH}",
        "GITHUB_WORKFLOW_SHA": R, "GITHUB_RUN_ID": str(count),
        "GITHUB_REPOSITORY_ID": str(REPO_ID),
    }
    event = {
        "ref": env["GITHUB_REF"], "before": H, "after": R, "forced": False,
        "deleted": False, "created": False,
        "repository": {"id": REPO_ID, "full_name": p.REPOSITORY},
        "head_commit": {"id": R, "message": p.marker(H)},
    }
    runs = [run(1, c.ORIGINAL_HELPER_SHA, run_id=c.ORIGINAL_HELPER_RUN_ID),
            run(2, c.ORIGINAL_SOURCE_SHA, run_id=c.ORIGINAL_RUN_ID),
            run(3, c.SECOND_HELPER_SHA, run_id=c.SECOND_HELPER_RUN_ID),
            run(4, c.SECOND_SOURCE_SHA, run_id=c.SECOND_RUN_ID),
            run(5, c.PREDECESSOR_HELPER_SHA, run_id=c.PREDECESSOR_HELPER_RUN_ID),
            run(6, c.PREDECESSOR_SOURCE_SHA, run_id=c.PREDECESSOR_RUN_ID),
            run(7, H, run_id=1)]
    runs.extend(run(n, f"{n:040x}") for n in range(8, count))
    runs.append(run(count, R, current=True))
    data = {
        f"git/commits/{R}": p.Response({"sha": R, "tree": {"sha": TREE},
                                       "parents": [{"sha": H}], "message": p.marker(H)}),
        f"git/commits/{H}": p.Response({"sha": H, "tree": {"sha": TREE}}),
        f"git/ref/heads/{p.BRANCH}": p.Response({"ref": env["GITHUB_REF"],
                                                "object": {"sha": R, "type": "commit"}}),
        ENV_PATH: p.Response({
            "name": p.ENVIRONMENT, "id": ENV_ID, "can_admins_bypass": False,
            "protection_rules": [{"type": "required_reviewers", "prevent_self_review": False,
                                  "reviewers": [{"type": "User", "reviewer": {
                                      "type": "User", "id": p.REVIEWER_ID,
                                      "login": p.REVIEWER_LOGIN}}]}],
            "deployment_branch_policy": {"protected_branches": False,
                                         "custom_branch_policies": True},
        }),
        "actions/workflows/companion-real-ai.yml": p.Response(
            {"id": WORKFLOW_ID, "path": p.WORKFLOW, "state": "active"}),
        f"actions/runs/{count}": p.Response(deepcopy(runs[-1])),
        f"actions/runs/{count}/approvals": p.Response([{
            "state": "approved", "user": {"id": p.REVIEWER_ID, "login": p.REVIEWER_LOGIN,
                                           "type": "User"},
            "environments": [{"id": ENV_ID, "name": p.ENVIRONMENT}],
        }]),
    }
    for batch in historical_cases():
        data[f"git/commits/{batch['helper_sha']}"] = p.Response({
            "sha": batch['helper_sha'], "tree": {"sha": batch['tree_sha']}})
        data[f"git/commits/{batch['source_sha']}"] = p.Response({
            "sha": batch['source_sha'], "tree": {"sha": batch['tree_sha']},
            "parents": [{"sha": batch['helper_sha']}], "message": batch['marker']})
        data[f"actions/runs/{batch['run_id']}/approvals"] = deepcopy(data[f"actions/runs/{count}/approvals"])
        data[f"actions/artifacts/{batch['artifact_id']}"] = p.Response({
            "id": batch['artifact_id'], "name": f"companion-ai-{batch['source_sha']}",
            "size_in_bytes": batch['archive_size'], "digest": f"sha256:{batch['archive_sha256']}",
            "expired": False, "url": p.API + f"actions/artifacts/{batch['artifact_id']}",
            "workflow_run": {"id": batch['run_id'], "repository_id": REPO_ID,
                             "head_repository_id": REPO_ID, "head_branch": p.BRANCH,
                             "head_sha": batch['source_sha']}})
    add_pages(data, POLICIES_PATH, "branch_policies", [{"id": p.BRANCH_POLICY_ID, "name": p.BRANCH, "type": "branch"}])
    add_pages(data, RUNS_PATH, "workflow_runs", runs)
    for value in runs:
        add_pages(data, f"actions/runs/{value['id']}/jobs?filter=all", "jobs", jobs_for(value, phase))
    return env, event, data


def execute(values, phase="before", calls=None):
    env, event, data = values

    def read(path):
        if calls is not None:
            calls.append(path)
        return deepcopy(data[path])

    return p.preflight(phase=phase, env=env, event=event, read=read)


def history(values):
    return values[2][p.page_path(RUNS_PATH, 1)].data["workflow_runs"]


def jobs(values, run_id=1):
    return values[2][p.page_path(f"actions/runs/{run_id}/jobs?filter=all", 1)].data["jobs"]


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_success_is_bound_to_exact_published_helper_and_current_request(phase):
    calls = []
    assert execute(fixture(phase), phase, calls) == {"ok": True, "helper_sha": H, "request_sha": R}
    assert ("actions/runs/8/approvals" in calls) is (phase == "paid")
    assert not any("secrets" in value or "variables" in value for value in calls)


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("remaining", [0, broker.SESSION_SECONDS - 1])
def test_expired_or_short_quote_rejects_before_github_reads(phase, remaining, monkeypatch):
    monkeypatch.setattr(p.time, "time", lambda: broker.PRICE_VALID_UNTIL - remaining)
    calls = []
    with pytest.raises(p.Blocked, match="^PRICE_REVIEW_EXPIRED$"):
        execute(fixture(phase), phase, calls)
    assert calls == []


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_full_session_quote_boundary_is_accepted(phase, monkeypatch):
    monkeypatch.setattr(p.time, "time", lambda: broker.PRICE_VALID_UNTIL - broker.SESSION_SECONDS)
    assert execute(fixture(phase), phase)["ok"]


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("wall_rollback", [False, True])
def test_slow_metadata_rechecks_quote_without_wall_clock_extension(phase, wall_rollback, monkeypatch):
    wall, monotonic = [broker.PRICE_VALID_UNTIL - broker.SESSION_SECONDS - 10], [1000.0]
    monkeypatch.setattr(p.time, "time", lambda: wall[0])
    monkeypatch.setattr(p.time, "monotonic", lambda: monotonic[0])
    env, event, data = fixture(phase)

    def read(path):
        monotonic[0] = 1011.0
        wall[0] = broker.PRICE_VALID_UNTIL - broker.SESSION_SECONDS + (1 if not wall_rollback else -3600)
        return deepcopy(data[path])

    with pytest.raises(p.Blocked, match="^PRICE_REVIEW_EXPIRED$"):
        p.preflight(phase=phase, env=env, event=event, read=read)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_expired_cli_writes_no_admission_and_never_reads_github(phase, tmp_path, monkeypatch, capsys):
    env, event, _ = fixture(phase)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(p.time, "time", lambda: broker.PRICE_VALID_UNTIL)

    def no_read(path):
        pytest.fail("Expired preflight must not perform GitHub reads")

    monkeypatch.setattr(p, "GitHubReader", lambda token: no_read)
    args = ["--phase", phase]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error_code": "PRICE_REVIEW_EXPIRED"}
    assert not admission.exists()


@pytest.mark.parametrize("stage", ["event", "admission_open", "admission_fsync"])
@pytest.mark.parametrize("wall_rollback", [False, True])
def test_cli_keeps_quote_anchor_across_slow_event_and_admission(stage, wall_rollback, tmp_path,
                                                              monkeypatch, capsys):
    env, event, data = fixture("paid")
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    wall, monotonic = [broker.PRICE_VALID_UNTIL - broker.SESSION_SECONDS - 10], [1000.0]
    monkeypatch.setattr(p.time, "time", lambda: wall[0])
    monkeypatch.setattr(p.time, "monotonic", lambda: monotonic[0])
    reader_constructions = []

    def reader(token):
        reader_constructions.append(True)
        return data.__getitem__

    def stall():
        monotonic[0] = 1011.0
        wall[0] = broker.PRICE_VALID_UNTIL - broker.SESSION_SECONDS + (1 if not wall_rollback else -3600)

    monkeypatch.setattr(p, "GitHubReader", reader)
    read_text, open_file, fsync = p.Path.read_text, p.os.open, p.os.fsync

    def slow_read(path, *args, **kwargs):
        value = read_text(path, *args, **kwargs)
        if path == event_path:
            stall()
        return value

    def slow_open(path, *args, **kwargs):
        descriptor = open_file(path, *args, **kwargs)
        if path == str(admission):
            stall()
        return descriptor

    def slow_fsync(descriptor):
        fsync(descriptor)
        stall()

    if stage == "event":
        monkeypatch.setattr(p.Path, "read_text", slow_read)
    elif stage == "admission_open":
        monkeypatch.setattr(p.os, "open", slow_open)
    else:
        monkeypatch.setattr(p.os, "fsync", slow_fsync)
    assert p.main(["--phase", "paid", "--admission-file", str(admission)]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error_code": "PRICE_REVIEW_EXPIRED"}
    if stage == "event":
        assert reader_constructions == [] and not admission.exists()
    # A late failed admission write cannot make the next secret step runnable.


def test_before_accepts_unmaterialized_paid_but_never_missing_preflight():
    values = fixture()
    response = values[2][p.page_path("actions/runs/8/jobs?filter=all", 1)]
    response.data["jobs"].pop()
    response.data["total_count"] -= 1
    assert execute(values)["ok"]
    response.data["jobs"].pop()
    response.data["total_count"] -= 1
    with pytest.raises(p.Blocked, match="CURRENT_JOBS_INVALID"):
        execute(values)


@pytest.mark.parametrize(("key", "value"), [
    ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_EVENT_NAME", "pull_request_target"),
    ("GITHUB_REPOSITORY", "attacker/offerPilot"), ("GITHUB_REF", "refs/heads/master"),
    ("GITHUB_REF", f"refs/tags/{p.BRANCH}"), ("GITHUB_SHA", H),
    ("GITHUB_WORKFLOW_REF", f"{p.REPOSITORY}/{p.WORKFLOW}@refs/heads/master"),
    ("GITHUB_WORKFLOW_REF", f"{p.REPOSITORY}/.github/workflows/desktop-real-ai.yml@refs/heads/{p.BRANCH}"),
    ("GITHUB_WORKFLOW_SHA", H), ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", ""),
    ("GITHUB_RUN_ID", "0"), ("GITHUB_REPOSITORY_ID", "77.0"),
    (p.HELPER_VAR, ""), (p.REQUEST_VAR, "B" * 40), (p.REQUEST_VAR, H),
])
def test_untrusted_runner_context_is_rejected_before_api_reads(key, value):
    values, calls = fixture(), []
    values[0][key] = value
    with pytest.raises(p.Blocked):
        execute(values, calls=calls)
    assert calls == []


@pytest.mark.parametrize(("key", "value"), [
    ("before", TREE), ("after", H), ("forced", True), ("deleted", True), ("created", True),
    ("ref", "refs/heads/master"), ("head_commit", {"id": R, "message": p.marker(H) + " extra"}),
    ("repository", {"id": REPO_ID + 1, "full_name": p.REPOSITORY}),
])
def test_push_event_must_match_exact_nonforced_request(key, value):
    values = fixture()
    values[1][key] = value
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize(("field", "value"), [
    ("parents", []), ("parents", [{"sha": TREE}]), ("parents", [{"sha": H}, {"sha": TREE}]),
    ("tree", {"sha": "d" * 40}), ("tree", {"sha": ""}), ("sha", H),
    ("message", p.marker(H) + "\n\n"), ("message", " " + p.marker(H)),
])
def test_request_is_single_parent_empty_tree_child_with_exact_marker(field, value):
    values = fixture()
    values[2][f"git/commits/{R}"].data[field] = value
    with pytest.raises(p.Blocked, match="REQUEST_MUST_BE_EMPTY_CHILD"):
        execute(values)


def test_stale_branch_head_and_noncommit_ref_are_rejected():
    for override in ({"sha": H, "type": "commit"}, {"sha": R, "type": "tag"}):
        values = fixture()
        values[2][f"git/ref/heads/{p.BRANCH}"].data["object"] = override
        with pytest.raises(p.Blocked, match="REQUEST_MUST_BE_EMPTY_CHILD"):
            execute(values)


@pytest.mark.parametrize("mutation", [
    lambda e: e.update(id=p.ENVIRONMENT_ID + 1),
    lambda e: e.pop("can_admins_bypass"), lambda e: e.update(can_admins_bypass=True),
    lambda e: e.update(name="offerpilot-real-ai-validation"),
    lambda e: e.update(protection_rules=[]),
    lambda e: e["protection_rules"][0].update(prevent_self_review=True),
    lambda e: e["protection_rules"][0]["reviewers"].append({"type": "User"}),
    lambda e: e["protection_rules"][0]["reviewers"][0].update(type="Team"),
    lambda e: e["protection_rules"][0]["reviewers"][0]["reviewer"].update(id=1),
    lambda e: e["protection_rules"][0]["reviewers"][0]["reviewer"].update(login="somebody"),
    lambda e: e["deployment_branch_policy"].update(protected_branches=True),
    lambda e: e["deployment_branch_policy"].update(custom_branch_policies=False),
])
def test_environment_cannot_weaken_owner_or_bypass_requirements(mutation):
    values = fixture()
    mutation(values[2][ENV_PATH].data)
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize(("key", "value"), [("id", p.BRANCH_POLICY_ID + 1), ("type", "tag"), ("name", "feat/*"), ("name", "*")])
def test_deployment_policy_is_exact_branch_not_tags_or_wildcards(key, value):
    values = fixture()
    values[2][p.page_path(POLICIES_PATH, 1)].data["branch_policies"][0][key] = value
    with pytest.raises(p.Blocked, match="EXACT_BRANCH_POLICY_REQUIRED"):
        execute(values)


def test_extra_deployment_policy_is_rejected_even_with_allowed_branch():
    values = fixture()
    policy = values[2][p.page_path(POLICIES_PATH, 1)].data
    policy["branch_policies"].append({"id": 43, "name": "master", "type": "branch"})
    policy["total_count"] = 2
    with pytest.raises(p.Blocked, match="EXACT_BRANCH_POLICY_REQUIRED"):
        execute(values)


@pytest.mark.parametrize("mutation", [
    lambda r: r.clear(), lambda r: r.append(deepcopy(r[0])),
    lambda r: r[0].update(state="rejected"), lambda r: r[0].update(state="pending"),
    lambda r: r[0]["user"].update(id=1), lambda r: r[0]["user"].update(login="somebody"),
    lambda r: r[0]["user"].update(type="Bot"),
    lambda r: r[0]["environments"][0].update(id=ENV_ID + 1),
    lambda r: r[0]["environments"][0].update(name="other"),
])
def test_paid_phase_requires_actual_current_run_owner_environment_approval(mutation):
    values = fixture("paid")
    mutation(values[2]["actions/runs/8/approvals"].data)
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        execute(values, "paid")


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out",
                                         "neutral", "action_required", "unknown", None])
def test_every_prior_nonskipped_paid_result_consumes_budget(conclusion):
    values = fixture()
    jobs(values)[-1]["conclusion"] = conclusion
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        execute(values)


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[0].update(run_attempt=2),
    lambda rows: rows[0].update(run_number=3),
    lambda rows: rows[0].update(path=f"{p.WORKFLOW}@refs/heads/master"),
    lambda rows: rows[0].update(path=".github/workflows/desktop-real-ai.yml"),
    lambda rows: rows[0].update(workflow_id=100),
    lambda rows: rows[0].update(event="workflow_dispatch"),
    lambda rows: rows[0].update(head_branch="master"),
    lambda rows: rows[0].update(head_repository={"full_name": "attacker/offerPilot", "id": REPO_ID}),
    lambda rows: rows[0].update(status="queued"),
    lambda rows: rows[0].update(conclusion="failure"),
    lambda rows: rows[-1].update(status="completed"),
])
def test_history_ref_provenance_reruns_gaps_and_unresolved_runs_fail_closed(mutation):
    values = fixture()
    mutation(history(values))
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("mutation", [
    lambda j: j.pop(), lambda j: j.append({**j[-1], "id": 12345}),
    lambda j: j[0].update(name="Unexpected paid alternative"),
    lambda j: j[0].update(run_id=999), lambda j: j[0].update(head_sha=R),
    lambda j: j[0].update(run_attempt=2), lambda j: j[-1].update(status="unknown"),
    lambda j: j[0].update(conclusion="failure"),
])
def test_missing_duplicate_unknown_or_wrong_identity_jobs_are_rejected(mutation):
    values = fixture()
    mutation(jobs(values))
    values[2][p.page_path("actions/runs/1/jobs?filter=all", 1)].data["total_count"] = len(jobs(values))
    with pytest.raises(p.Blocked):
        execute(values)


@pytest.mark.parametrize("phase", ["before", "paid"])
def test_current_run_cannot_hide_additional_jobs(phase):
    values = fixture(phase)
    current_jobs = jobs(values, 8)
    current_jobs.append({**current_jobs[-1], "id": 987, "name": "Surprise provider request"})
    values[2][p.page_path("actions/runs/8/jobs?filter=all", 1)].data["total_count"] += 1
    with pytest.raises(p.Blocked, match="JOB_HISTORY_INVALID"):
        execute(values, phase)


def test_complete_multpage_history_checks_every_run_including_second_page():
    values, calls = fixture(count=102), []
    assert execute(values, calls=calls)["ok"]
    assert p.page_path(RUNS_PATH, 2) in calls
    assert len([path for path in calls if "/jobs?" in path]) == 102
    jobs(values, 101)[-1]["conclusion"] = "cancelled"
    with pytest.raises(p.Blocked, match="PAID_BUDGET_ALREADY_CONSUMED"):
        execute(values)


def test_complete_job_pages_are_collected_not_silently_truncated():
    data = {}
    path = "actions/runs/1/jobs?filter=all"
    add_pages(data, path, "jobs", [{"id": i} for i in range(1, 102)])
    assert len(p.read_pages(data.__getitem__, path, "jobs")) == 101
    # Even a paginated surprise job list is fully read and then denied by the gate.
    with pytest.raises(p.Blocked, match="JOB_HISTORY_INVALID"):
        p.validate_jobs(run(1, H), p.read_pages(data.__getitem__, path, "jobs"))


@pytest.mark.parametrize("failure", ["missing_link", "missing_page", "short_page", "changed_count",
                                     "duplicate", "extra_link", "wrong_page", "foreign_url"])
def test_missing_or_inconsistent_history_pages_are_rejected(failure):
    values = fixture(count=101)
    first = p.page_path(RUNS_PATH, 1)
    second = p.page_path(RUNS_PATH, 2)
    data = values[2]
    if failure == "missing_link":
        data[first] = p.Response(data[first].data)
    elif failure == "missing_page":
        data[second] = p.Response({"total_count": 101, "workflow_runs": []})
    elif failure == "short_page":
        data[first].data["workflow_runs"].pop()
    elif failure == "changed_count":
        data[second].data["total_count"] = 102
    elif failure == "duplicate":
        data[second].data["workflow_runs"] = [deepcopy(data[first].data["workflow_runs"][0])]
    elif failure == "extra_link":
        data[second] = p.Response(data[second].data, link(RUNS_PATH, 3))
    elif failure == "wrong_page":
        data[first] = p.Response(data[first].data, link(RUNS_PATH, 3))
    elif failure == "foreign_url":
        data[first] = p.Response(data[first].data, '<https://attacker.invalid/?page=2>; rel="next"')
    with pytest.raises(p.Blocked):
        execute(values)


def test_deleted_run_cannot_be_hidden_by_adjusting_total_count():
    values = fixture(count=9)
    response = values[2][p.page_path(RUNS_PATH, 1)]
    response.data["workflow_runs"].pop(0)
    response.data["total_count"] -= 1
    with pytest.raises(p.Blocked, match="RUN_HISTORY_GAP"):
        execute(values)


@pytest.mark.parametrize("path", [
    "actions/secrets", f"environments/{p.ENVIRONMENT}/secrets", "actions/variables",
    "../other", "https://attacker.invalid/", "/git/commits/" + H,
    "actions/runs/2/cancel", "actions/runs/2/rerun", "git/commits/" + H + "?token=x",
])
def test_network_reader_has_narrow_get_only_allowlist(path, monkeypatch):
    def no_open(*args, **kwargs):
        pytest.fail("Denied path must not create an HTTP request")

    reader = p.GitHubReader("synthetic-github-token")
    monkeypatch.setattr(reader.opener, "open", no_open)
    with pytest.raises(p.Blocked, match="GITHUB_READ_PATH_DENIED"):
        reader(path)


def test_reader_uses_verified_tls_and_public_environment_get_without_token(monkeypatch):
    requests, handlers = [], []

    class Reply(io.BytesIO):
        status = 200
        headers = {}

    def open_request(request, timeout):
        requests.append(request)
        assert request.method == "GET" and timeout == 20
        return Reply(b'{"ok":true}')

    def build(*args):
        handlers.extend(args)
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(p, "build_opener", build)
    reader = p.GitHubReader("synthetic-github-token")
    reader(ENV_PATH)
    reader("actions/runs/2")
    assert "Authorization" not in requests[0].headers
    assert requests[1].headers["Authorization"] == "Bearer synthetic-github-token"
    tls = next(handler for handler in handlers if isinstance(handler, p.HTTPSHandler))
    assert tls._context.verify_mode == ssl.CERT_REQUIRED and tls._context.check_hostname
    assert any(isinstance(handler, p.NoRedirect) for handler in handlers)


def test_redirects_and_http_failures_do_not_expose_remote_error_bodies(monkeypatch):
    with pytest.raises(p.Blocked, match="GITHUB_REDIRECT_DENIED"):
        p.NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.invalid")
    reader = p.GitHubReader("synthetic-github-token")

    def fail(*args, **kwargs):
        raise HTTPError("secret-url", 403, "sensitive server body", {}, None)

    monkeypatch.setattr(reader.opener, "open", fail)
    with pytest.raises(p.Blocked, match="^GITHUB_READ_UNAVAILABLE$"):
        reader("actions/runs/2")


def test_admission_is_minimal_fresh_and_exclusive(tmp_path, monkeypatch):
    path = tmp_path / "admission.json"
    monkeypatch.setattr(p.time, "time", lambda: 1792594800)
    p.write_admission(str(path), p.Context(H, R, 2, REPO_ID))
    assert json.loads(path.read_text()) == {**c.lineage_fields(), "schema": 2,
                                           "source_sha": R, "helper_sha": H,
                                           "run_id": "2", "run_attempt": "1", "checked_at": 1792594800,
                                           "checked_at_monotonic_ns": 1000_000_000_000,
                                           "quote_deadline_monotonic_ns": int(
                                               (1000 + broker.PRICE_VALID_UNTIL - 1792594800) * 1e9)}
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    with pytest.raises(p.Blocked, match="ADMISSION_WRITE_BLOCKED"):
        p.write_admission(str(path), p.Context(H, R, 3, REPO_ID))
    assert path.read_bytes() == original


def test_cli_only_writes_admission_after_paid_validation(tmp_path, monkeypatch, capsys):
    env, event, data = fixture("paid")
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GH_TOKEN", "synthetic-github-token")
    monkeypatch.setattr(p, "GitHubReader", lambda token: data.__getitem__)
    assert p.main(["--phase", "paid", "--helper-sha", H, "--request-sha", R,
                   "--admission-file", str(admission)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "helper_sha": H, "request_sha": R}
    assert json.loads(admission.read_text())["run_id"] == "8"
    assert p.main(["--phase", "before", "--admission-file", str(tmp_path / "no.json")]) == 1
    assert not (tmp_path / "no.json").exists()
    assert json.loads(capsys.readouterr().out)["error_code"] == "ADMISSION_PHASE_INVALID"
    data["actions/runs/8/approvals"].data[0]["state"] = "rejected"
    assert p.main(["--phase", "paid", "--admission-file", str(tmp_path / "denied.json")]) == 1
    assert not (tmp_path / "denied.json").exists()
    assert "synthetic-github-token" not in capsys.readouterr().out


def test_cli_failure_output_is_one_safe_json_without_secret_or_api_body(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert p.main(["--phase", "paid"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error_code": "RERUN_FORBIDDEN"}
    assert p.main(["--not-an-option", "sensitive-value"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": False, "error_code": "CLI_INVALID"}
    assert captured.err == ""


@pytest.mark.parametrize("pin", [0, 100])
def test_missing_or_recreated_workflow_id_is_blocked(monkeypatch, pin):
    monkeypatch.setattr(p, "WORKFLOW_ID", pin)
    env, event, data = fixture()
    with pytest.raises(p.Blocked, match="WORKFLOW_IDENTITY_MISMATCH"):
        p.preflight(phase="before", env=env, event=event, read=lambda path: data[path])


def test_registration_inspection_cannot_arm_or_write_admission(monkeypatch):
    # Harmless registration evidence remains readable after quote expiration.
    monkeypatch.setattr(p.time, "time", lambda: broker.PRICE_VALID_UNTIL + 1)
    _, _, data = fixture()
    calls = []

    def read(path):
        calls.append(path)
        return data[path]

    result = p.inspect_metadata(read)
    assert result == {'registration_only': True, 'workflow_id': WORKFLOW_ID,
                      'environment_id': ENV_ID, 'branch_policy_id': 66,
                      'reviewer_id': p.REVIEWER_ID, 'protection_shape_verified': True}
    assert len(calls) == 3
    assert not any('/secrets' in call or '/approvals' in call for call in calls)


@pytest.mark.parametrize('phase', ['before', 'paid'])
def test_only_three_exact_paid_runs_are_exempt_and_current_approval_is_separate(phase):
    calls = []
    assert execute(fixture(phase), phase, calls)['ok']
    assert f'actions/runs/{c.ORIGINAL_RUN_ID}/approvals' in calls
    assert f'actions/artifacts/{c.ORIGINAL_ARTIFACT_ID}' in calls
    assert f'actions/runs/{c.SECOND_RUN_ID}/approvals' in calls
    assert f'actions/artifacts/{c.SECOND_ARTIFACT_ID}' in calls
    assert f'actions/runs/{c.PREDECESSOR_RUN_ID}/approvals' in calls
    assert ('actions/runs/8/approvals' in calls) is (phase == 'paid')
    assert f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID}' in calls


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('mutation', ['missing', 'sha', 'failure', 'rerun', 'paid_skipped',
                                      'paid_unknown', 'helper_sha', 'helper_failure', 'order'])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_all_pinned_histories_are_mandatory_not_any_successful_runs(phase, mutation, batch_index):
    values = fixture(phase)
    rows = history(values)
    batch = historical_cases()[batch_index]
    original = next(row for row in rows if row['id'] == batch['run_id'])
    helper = next(row for row in rows if row['id'] == batch['helper_run_id'])
    if mutation == 'missing':
        original['id'] += 1
        previous_jobs = deepcopy(jobs(values, batch['run_id']))
        for job in previous_jobs:
            job['run_id'] = original['id']
        add_pages(values[2], f"actions/runs/{original['id']}/jobs?filter=all", 'jobs', previous_jobs)
    elif mutation == 'sha':
        original['head_sha'] = 'f' * 40
    elif mutation == 'failure':
        original['conclusion'] = 'failure'
    elif mutation == 'rerun':
        original['run_attempt'] = 2
    elif mutation == 'paid_skipped':
        jobs(values, batch['run_id'])[-1]['conclusion'] = 'skipped'
    elif mutation == 'paid_unknown':
        jobs(values, batch['run_id'])[-1]['conclusion'] = None
    elif mutation == 'helper_sha':
        helper['head_sha'] = H
    elif mutation == 'helper_failure':
        helper['conclusion'] = 'failure'
    else:
        original['run_number'], helper['run_number'] = helper['run_number'], original['run_number']
    with pytest.raises(p.Blocked):
        execute(values, phase)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('field,value', [
    ('id', 12408325853), ('name', 'some-other-artifact'), ('size_in_bytes', 0),
    ('digest', None), ('digest', 'sha256:' + '0' * 64), ('expired', True),
    ('url', 'https://untrusted.invalid/artifact'),
])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_artifact_must_remain_available_and_exact_in_both_phases(phase, field, value, batch_index):
    values = fixture(phase)
    artifact_id = historical_cases()[batch_index]['artifact_id']
    values[2][f'actions/artifacts/{artifact_id}'].data[field] = value
    with pytest.raises(p.Blocked, match='PREDECESSOR_ARTIFACT_MISMATCH'):
        execute(values, phase)


@pytest.mark.parametrize('field,value', [
    ('id', 1), ('repository_id', 1), ('head_repository_id', 1),
    ('head_branch', 'master'), ('head_sha', H),
])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_artifact_cannot_belong_to_another_run_or_source(field, value, batch_index):
    values = fixture()
    artifact_id = historical_cases()[batch_index]['artifact_id']
    values[2][f'actions/artifacts/{artifact_id}'].data['workflow_run'][field] = value
    with pytest.raises(p.Blocked, match='PREDECESSOR_ARTIFACT_MISMATCH'):
        execute(values)


@pytest.mark.parametrize('field,value', [
    ('sha', H), ('tree', {'sha': TREE}), ('parents', []),
    ('parents', [{'sha': H}]), ('message', 'same source but unreviewed'),
])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_each_request_source_is_bound_to_its_exact_helper_tree(field, value, batch_index):
    values = fixture()
    source_sha = historical_cases()[batch_index]['source_sha']
    values[2][f'git/commits/{source_sha}'].data[field] = value
    with pytest.raises(p.Blocked, match='PREDECESSOR_SOURCE_MISMATCH'):
        execute(values)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_either_missing_historical_approval_is_not_forgiven_by_current_approval(phase, batch_index):
    values = fixture(phase)
    run_id = historical_cases()[batch_index]['run_id']
    values[2][f'actions/runs/{run_id}/approvals'].data.clear()
    with pytest.raises(p.Blocked, match='OWNER_APPROVAL_UNVERIFIED'):
        execute(values, phase)


@pytest.mark.parametrize('path_name', ['ORIGINAL_EVIDENCE_PATH', 'SECOND_EVIDENCE_PATH', 'EVIDENCE_PATH'])
def test_missing_local_evidence_blocks_before_github_and_admission(tmp_path, monkeypatch, path_name):
    monkeypatch.setattr(c, path_name, tmp_path / 'absent.zip')
    calls = []
    with pytest.raises(p.Blocked, match='PREDECESSOR_EVIDENCE_UNAVAILABLE'):
        execute(fixture(), calls=calls)
    assert calls == []
    with pytest.raises(p.Blocked, match='PREDECESSOR_EVIDENCE_UNAVAILABLE'):
        p.write_admission(str(tmp_path / 'admission.json'), p.Context(H, R, 4, REPO_ID))
    assert not (tmp_path / 'admission.json').exists()


@pytest.mark.parametrize('batch_index', [0, 1, 2])
def test_correction_marker_cannot_reuse_either_historical_request_marker(batch_index):
    values = fixture()
    source_sha = historical_cases()[batch_index]['source_sha']
    marker = values[2][f'git/commits/{source_sha}'].data['message']
    values[1]['head_commit']['message'] = marker
    values[2][f'git/commits/{R}'].data['message'] = marker
    with pytest.raises(p.Blocked):
        execute(values)
    assert 'correction-3' in p.marker(H)
    assert str(c.PREDECESSOR_RUN_ID) in p.marker(H) and c.LEDGER_SHA256 in p.marker(H)


@pytest.mark.parametrize('path', [
    f'actions/artifacts/{c.ORIGINAL_ARTIFACT_ID + 1}',
    f'actions/artifacts/{c.ORIGINAL_ARTIFACT_ID}/zip',
    f'actions/artifacts/{c.ORIGINAL_ARTIFACT_ID}?alternate=true',
    f'actions/artifacts/{c.SECOND_ARTIFACT_ID + 1}',
    f'actions/artifacts/{c.SECOND_ARTIFACT_ID}/zip',
    f'actions/artifacts/{c.SECOND_ARTIFACT_ID}?alternate=true',
    f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID + 1}',
    f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID}/zip',
    f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID}?alternate=true',
])
def test_artifact_extension_is_exact_metadata_only_no_download_fallback(path, monkeypatch):
    reader = p.GitHubReader('synthetic-token')
    monkeypatch.setattr(reader.opener, 'open', lambda *a, **k: pytest.fail('Must not send'))
    with pytest.raises(p.Blocked, match='GITHUB_READ_PATH_DENIED'):
        reader(path)


@pytest.mark.parametrize('key', [p.HELPER_VAR, p.REQUEST_VAR])
@pytest.mark.parametrize('sha', [c.ORIGINAL_HELPER_SHA, c.ORIGINAL_SOURCE_SHA,
                                  c.SECOND_HELPER_SHA, c.SECOND_SOURCE_SHA,
                                  c.PREDECESSOR_HELPER_SHA, c.PREDECESSOR_SOURCE_SHA])
def test_previous_helper_or_request_cannot_be_rearmed(key, sha):
    values, calls = fixture(), []
    values[0][key] = sha
    with pytest.raises(p.Blocked, match='EXACT_SHA_APPROVAL_MISSING'):
        execute(values, calls=calls)
    assert calls == []


@pytest.mark.parametrize('target', ['artifact_id', 'artifact_size', 'artifact_run', 'artifact_repo',
                                   'workflow', 'reviewer', 'review_environment', 'job_attempt'])
def test_numeric_metadata_must_be_integer_not_float_or_boolean(target):
    values = fixture('paid')
    artifact = values[2][f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID}'].data
    if target.startswith('artifact_'):
        if target == 'artifact_id':
            artifact['id'] = float(artifact['id'])
        elif target == 'artifact_size':
            artifact['size_in_bytes'] = float(artifact['size_in_bytes'])
        elif target == 'artifact_run':
            artifact['workflow_run']['id'] = float(c.PREDECESSOR_RUN_ID)
        else:
            artifact['workflow_run']['repository_id'] = float(REPO_ID)
    elif target == 'workflow':
        values[2]['actions/workflows/companion-real-ai.yml'].data['id'] = float(WORKFLOW_ID)
    elif target == 'reviewer':
        values[2]['actions/runs/8/approvals'].data[0]['user']['id'] = float(p.REVIEWER_ID)
    elif target == 'review_environment':
        values[2]['actions/runs/8/approvals'].data[0]['environments'][0]['id'] = float(ENV_ID)
    else:
        jobs(values, 8)[0]['run_attempt'] = True
    with pytest.raises(p.Blocked):
        execute(values, 'paid')


def test_real_admission_producer_matches_live_runner_schema_without_key(tmp_path):
    from scripts.companion_ai_validation import run as live

    env, _, _ = fixture('paid')
    target = tmp_path / 'admission.json'
    p.write_admission(str(target), p.validate_context(env))
    record = json.loads(target.read_bytes())
    assert set(record) == c.LINEAGE_KEYS | {
        'schema', 'source_sha', 'helper_sha', 'run_id', 'run_attempt', 'checked_at',
        'checked_at_monotonic_ns', 'quote_deadline_monotonic_ns'}
    assert record['schema'] == 2 and record['run_attempt'] == '1'
    assert live.check_admission(record, env, p.time.time()) == R


@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan'), -1.0, True, 10**20, 10**1000])
def test_invalid_monotonic_handoff_values_fail_closed(value):
    with pytest.raises(p.Blocked, match='ADMISSION_CLOCK_INVALID'):
        p.monotonic_ns(value)


@pytest.mark.parametrize('deadline', [float('inf'), float('nan'), -1.0, True, 10**1000])
def test_corrupt_quote_anchor_never_writes_admission(tmp_path, deadline):
    target = tmp_path / 'admission.json'
    with pytest.raises(p.Blocked, match='PRICE_REVIEW_EXPIRED'):
        p.write_admission(str(target), p.Context(H, R, 4, REPO_ID), deadline)
    assert not target.exists()


def test_handoff_preserves_original_deadline_after_wall_rollback(tmp_path, monkeypatch):
    wall, monotonic = [broker.PRICE_VALID_UNTIL - 3600], [1000.0]
    monkeypatch.setattr(p.time, 'time', lambda: wall[0])
    monkeypatch.setattr(p.time, 'monotonic', lambda: monotonic[0])
    original_deadline = p.start_quote_window()
    wall[0] -= 3600
    monotonic[0] += 10
    target = tmp_path / 'admission.json'
    p.write_admission(str(target), p.Context(H, R, 4, REPO_ID), original_deadline)
    record = json.loads(target.read_bytes())
    assert record['checked_at_monotonic_ns'] == 1010_000_000_000
    assert record['quote_deadline_monotonic_ns'] == 4600_000_000_000
    assert record['quote_deadline_monotonic_ns'] < p.monotonic_ns(p.start_quote_window())


@pytest.mark.parametrize('stage', ['lineage', 'open', 'fsync'])
def test_slow_admission_handoff_cannot_rejuvenate_freshness_with_wall_rollback(tmp_path, monkeypatch, stage):
    wall, monotonic = [broker.PRICE_VALID_UNTIL - 3600], [1000.0]
    monkeypatch.setattr(p.time, 'time', lambda: wall[0])
    monkeypatch.setattr(p.time, 'monotonic', lambda: monotonic[0])
    target = tmp_path / 'admission.json'
    original_lineage, original_open, original_fsync = p.verified_lineage, p.os.open, p.os.fsync

    def stall():
        monotonic[0] += 121
        # Wall remains at the initial value after a rollback of elapsed time.

    def delayed_lineage():
        result = original_lineage()
        stall()
        return result

    def delayed_open(path, *args):
        result = original_open(path, *args)
        if path == str(target):
            stall()
        return result

    def delayed_fsync(fd):
        original_fsync(fd)
        stall()

    if stage == 'lineage':
        monkeypatch.setattr(p, 'verified_lineage', delayed_lineage)
    elif stage == 'open':
        monkeypatch.setattr(p.os, 'open', delayed_open)
    else:
        monkeypatch.setattr(p.os, 'fsync', delayed_fsync)
    with pytest.raises(p.Blocked, match='ADMISSION_EXPIRED'):
        p.write_admission(str(target), p.Context(H, R, 4, REPO_ID))
    if stage == 'lineage':
        assert not target.exists()
    else:
        record = json.loads(target.read_bytes())
        assert record['checked_at_monotonic_ns'] == 1000_000_000_000
        assert record['quote_deadline_monotonic_ns'] == 4600_000_000_000


def test_step_delay_between_real_producer_and_runner_does_not_refresh_admission(tmp_path, monkeypatch):
    from scripts.companion_ai_validation import run as live

    env, _, _ = fixture('paid')
    wall, monotonic = [broker.PRICE_VALID_UNTIL - 3600], [1000.0]
    monkeypatch.setattr(p.time, 'time', lambda: wall[0])
    monkeypatch.setattr(p.time, 'monotonic', lambda: monotonic[0])
    target = tmp_path / 'admission.json'
    p.write_admission(str(target), p.validate_context(env))
    record = json.loads(target.read_bytes())
    assert live.check_admission(record, env, wall[0]) == R
    monotonic[0] += 121
    with pytest.raises(broker.GateError, match='admission_expired'):
        live.check_admission(record, env, wall[0])


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('left', [0, 1, 2, 3, 4, 5])
def test_every_historical_chain_boundary_is_strictly_ordered(phase, left):
    values = fixture(phase)
    rows = history(values)
    rows[left]['run_number'], rows[left + 1]['run_number'] = (
        rows[left + 1]['run_number'], rows[left]['run_number'])
    with pytest.raises(p.Blocked, match='PREDECESSOR_HISTORY_ORDER'):
        execute(values, phase)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('index', [0, 1, 2, 3, 4, 5])
def test_neither_missing_pair_can_be_hidden_as_an_ordinary_skipped_run(phase, index):
    values = fixture(phase)
    row = history(values)[index]
    previous_jobs = deepcopy(jobs(values, row['id']))
    row['id'] = 99999
    for job in previous_jobs:
        job['run_id'] = row['id']
        if job['name'] in (p.PREFLIGHT_JOB, p.PAID_JOB):
            job['conclusion'] = 'skipped'
    add_pages(values[2], f"actions/runs/{row['id']}/jobs?filter=all", 'jobs', previous_jobs)
    with pytest.raises(p.Blocked, match='PREDECESSOR_HISTORY_REQUIRED'):
        execute(values, phase)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('source', ['original', 'second', 'predecessor', 'new'])
@pytest.mark.parametrize('conclusion', ['success', 'failure', 'cancelled', 'timed_out',
                                      'neutral', 'action_required', 'unknown', None])
def test_fourth_paid_run_is_never_exempt_even_with_a_pinned_source(phase, source, conclusion):
    values = fixture(phase, count=9)
    row = history(values)[-2]
    sha = {'original': c.ORIGINAL_SOURCE_SHA, 'second': c.SECOND_SOURCE_SHA, 'predecessor': c.PREDECESSOR_SOURCE_SHA,
           'new': 'f' * 40}[source]
    row['head_sha'] = sha
    for job in jobs(values, row['id']):
        job['head_sha'] = sha
    jobs(values, row['id'])[-1]['conclusion'] = conclusion
    with pytest.raises(p.Blocked, match='PAID_BUDGET_ALREADY_CONSUMED'):
        execute(values, phase)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('batch_index', [0, 1, 2])
@pytest.mark.parametrize('field,value', [('sha', H), ('tree', {'sha': TREE})])
def test_each_historical_helper_must_match_its_pinned_tree(phase, batch_index, field, value):
    values = fixture(phase)
    helper_sha = historical_cases()[batch_index]['helper_sha']
    values[2][f'git/commits/{helper_sha}'].data[field] = value
    with pytest.raises(p.Blocked, match='PREDECESSOR_SOURCE_MISMATCH'):
        execute(values, phase)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('mutation', ['campaign', 'predecessor', 'ledger', 'extra_line'])
def test_first_correction_marker_must_refer_to_original_run_and_ledger(phase, mutation):
    values = fixture(phase)
    request = values[2][f'git/commits/{c.SECOND_SOURCE_SHA}'].data
    message = request['message']
    if mutation == 'campaign':
        message = message.replace('correction-1', 'correction-2')
    elif mutation == 'predecessor':
        message = message.replace(str(c.ORIGINAL_RUN_ID), str(c.PREDECESSOR_RUN_ID))
    elif mutation == 'ledger':
        message = message.replace(c.ORIGINAL_LEDGER_SHA256, c.LEDGER_SHA256)
    else:
        message += '\n\n'
    request['message'] = message
    with pytest.raises(p.Blocked, match='PREDECESSOR_SOURCE_MISMATCH'):
        execute(values, phase)


def test_third_correction_marker_and_quote_deadline_are_explicit_fixed_pins():
    assert p.marker(H) == (
        'test: request OfferPilot companion real AI validation\n\n'
        f'OfferPilot-Companion-AI-Helper: {H}\n'
        'OfferPilot-Companion-AI-Request: one-shot-20261021-correction-3\n'
        'OfferPilot-Companion-AI-Predecessor: 40060633208\n'
        f'OfferPilot-Companion-AI-Ledger: {c.LEDGER_SHA256}')
    assert p.PRICE_VALID_UNTIL == broker.PRICE_VALID_UNTIL == c.PRICE_VALID_UNTIL == int(
        datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())


def test_reader_permits_both_pinned_artifact_metadata_ids_and_no_download(monkeypatch):
    requests = []

    class Reply(io.BytesIO):
        status = 200
        headers = {}

    def open_request(request, timeout):
        requests.append(request)
        return Reply(b'{"ok":true}')

    reader = p.GitHubReader('synthetic-token')
    monkeypatch.setattr(reader.opener, 'open', open_request)
    reader(f'actions/artifacts/{c.ORIGINAL_ARTIFACT_ID}')
    reader(f'actions/artifacts/{c.SECOND_ARTIFACT_ID}')
    reader(f'actions/artifacts/{c.PREDECESSOR_ARTIFACT_ID}')
    assert [request.full_url for request in requests] == [
        p.API + 'actions/artifacts/12408325852', p.API + 'actions/artifacts/12412545645',
        p.API + 'actions/artifacts/12547790764']
    assert all(request.method == 'GET' for request in requests)


@pytest.mark.parametrize('phase', ['before', 'paid'])
@pytest.mark.parametrize('mutation', ['campaign', 'predecessor', 'ledger', 'extra_line'])
def test_second_correction_marker_must_refer_to_second_run_and_ledger(phase, mutation):
    values = fixture(phase)
    request = values[2][f'git/commits/{c.PREDECESSOR_SOURCE_SHA}'].data
    message = request['message']
    if mutation == 'campaign':
        message = message.replace('correction-2', 'correction-3')
    elif mutation == 'predecessor':
        message = message.replace(str(c.SECOND_RUN_ID), str(c.ORIGINAL_RUN_ID))
    elif mutation == 'ledger':
        message = message.replace(c.SECOND_LEDGER_SHA256, c.ORIGINAL_LEDGER_SHA256)
    else:
        message += '\n\n'
    request['message'] = message
    with pytest.raises(p.Blocked, match='PREDECESSOR_SOURCE_MISMATCH'):
        execute(values, phase)
