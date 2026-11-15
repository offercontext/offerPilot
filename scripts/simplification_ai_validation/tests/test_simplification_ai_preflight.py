"""Offline fixture coverage for the one-shot GitHub approval boundary."""

from copy import deepcopy
import io
import json
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts.simplification_ai_validation import broker as gate
from scripts.simplification_ai_validation import preflight as p

H, R, TREE = "a" * 40, "b" * 40, "c" * 40
REPO_ID, ENV_ID, WORKFLOW_ID = 77, p.ENVIRONMENT_ID, 99
RUNS_PATH = "actions/workflows/simplification-real-ai.yml/runs"
ENV_PATH = f"environments/{p.ENVIRONMENT}"
POLICIES_PATH = f"{ENV_PATH}/deployment-branch-policies"


@pytest.fixture(autouse=True)
def pinned_fake_workflow(monkeypatch):
    monkeypatch.setattr(p, "WORKFLOW_ID", WORKFLOW_ID)
    # Offline history tests must outlive the fixed live quote.
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - 3600)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 1000.0)


def run(number, sha, current=False):
    return {
        "id": number, "run_number": number, "run_attempt": 1, "workflow_id": WORKFLOW_ID,
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
        if name == p.PAID_JOB and not current:
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


def fixture(phase="before", count=2):
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
    runs = [run(n, H if n == 1 else f"{n:040x}") for n in range(1, count)]
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
        "actions/workflows/simplification-real-ai.yml": p.Response(
            {"id": WORKFLOW_ID, "path": p.WORKFLOW, "state": "active"}),
        f"actions/runs/{count}": p.Response(deepcopy(runs[-1])),
        f"actions/runs/{count}/approvals": p.Response([{
            "state": "approved", "user": {"id": p.REVIEWER_ID, "login": p.REVIEWER_LOGIN,
                                           "type": "User"},
            "environments": [{"id": ENV_ID, "name": p.ENVIRONMENT}],
        }]),
    }
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
    assert ("actions/runs/2/approvals" in calls) is (phase == "paid")
    assert not any("secrets" in value or "variables" in value for value in calls)


def test_before_accepts_unmaterialized_paid_but_never_missing_preflight():
    values = fixture()
    response = values[2][p.page_path("actions/runs/2/jobs?filter=all", 1)]
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
    mutation(values[2]["actions/runs/2/approvals"].data)
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
    lambda rows: rows[1].update(status="completed"),
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
    current_jobs = jobs(values, 2)
    current_jobs.append({**current_jobs[-1], "id": 987, "name": "Surprise provider request"})
    values[2][p.page_path("actions/runs/2/jobs?filter=all", 1)].data["total_count"] += 1
    with pytest.raises(p.Blocked, match="JOB_HISTORY_INVALID"):
        execute(values, phase)


def test_complete_multpage_history_checks_every_run_including_second_page():
    values, calls = fixture(count=101), []
    assert execute(values, calls=calls)["ok"]
    assert p.page_path(RUNS_PATH, 2) in calls
    assert len([path for path in calls if "/jobs?" in path]) == 101
    jobs(values, 100)[-1]["conclusion"] = "cancelled"
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
    values = fixture(count=3)
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
    monkeypatch.setattr(gate.time, "time", lambda: 1792594800)
    p.write_admission(str(path), p.Context(H, R, 2, REPO_ID))
    assert json.loads(path.read_text()) == {
        "schema": 2, "source_sha": R, "helper_sha": H, "run_id": "2",
        "checked_at": 1792594800, "checked_monotonic": 1000.0,
        "boot_id": gate.boot_identity(), "price_window": gate.PriceWindow.capture().record(),
    }
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
    assert json.loads(admission.read_text())["run_id"] == "2"
    assert p.main(["--phase", "before", "--admission-file", str(tmp_path / "no.json")]) == 1
    assert not (tmp_path / "no.json").exists()
    assert json.loads(capsys.readouterr().out)["error_code"] == "ADMISSION_PHASE_INVALID"
    data["actions/runs/2/approvals"].data[0]["state"] = "rejected"
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


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("remaining", [-1, 89, 1199],
                         ids=["expired", "under-90-seconds", "under-20-minutes"])
def test_price_window_refuses_before_github_reads(monkeypatch, phase, remaining):
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - remaining)
    calls = []
    with pytest.raises(p.Blocked, match="^PRICE_REVIEW_EXPIRED$"):
        execute(fixture(phase), phase, calls)
    assert calls == []


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("remaining", [-1, 89, 1199],
                         ids=["expired", "under-90-seconds", "under-20-minutes"])
def test_cli_price_window_refuses_before_reader_credentials_or_admission(
        tmp_path, monkeypatch, capsys, phase, remaining):
    env, _, _ = fixture(phase)

    def forbidden(*args, **kwargs):
        pytest.fail("Expired quote must not read credentials, create a reader or write admission")

    class GuardedEnvironment(dict):
        def get(self, key, default=None):
            if key == "GH_TOKEN" or key.startswith("OFFERPILOT_SIMPLIFICATION_DEEPSEEK"):
                forbidden()
            return super().get(key, default)

        def __getitem__(self, key):
            if key == "GH_TOKEN" or key.startswith("OFFERPILOT_SIMPLIFICATION_DEEPSEEK"):
                forbidden()
            return super().__getitem__(key)

    monkeypatch.setattr(p.os, "environ", GuardedEnvironment(env))
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - remaining)
    monkeypatch.setattr(p, "GitHubReader", forbidden)
    monkeypatch.setattr(p, "write_admission", forbidden)
    admission = tmp_path / "admission.json"
    args = ["--phase", phase]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error_code": "PRICE_REVIEW_EXPIRED",
    }
    assert not admission.exists()


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("remaining", [-1, 89, 1199],
                         ids=["expired", "under-90-seconds", "under-20-minutes"])
def test_quote_window_rechecked_after_github_and_owner_checks(
        tmp_path, monkeypatch, capsys, phase, remaining):
    env, event, data = fixture(phase)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["GH_TOKEN"] = "synthetic-github-token"
    now, calls = [gate.PRICE_VALID_UNTIL - 3600], []
    last = ("actions/runs/2/approvals" if phase == "paid"
            else p.page_path("actions/runs/2/jobs?filter=all", 1))

    def read(path):
        calls.append(path)
        if path == last:
            now[0] = gate.PRICE_VALID_UNTIL - remaining
        return deepcopy(data[path])

    def forbidden(*args, **kwargs):
        pytest.fail("A quote that expires during GitHub checks must not write admission")

    monkeypatch.setattr(p.os, "environ", env)
    monkeypatch.setattr(gate.time, "time", lambda: now[0])
    monkeypatch.setattr(p, "GitHubReader", lambda token: read)
    monkeypatch.setattr(p, "write_admission", forbidden)
    args = ["--phase", phase]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error_code": "PRICE_REVIEW_EXPIRED",
    }
    assert calls[-1] == last
    assert ("actions/runs/2/approvals" in calls) is (phase == "paid")
    assert not admission.exists()


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("remaining", [1200, 1201])
def test_full_session_window_remains_admissible(monkeypatch, phase, remaining):
    monkeypatch.setattr(gate.time, "time", lambda: gate.PRICE_VALID_UNTIL - remaining)
    assert execute(fixture(phase), phase) == {"ok": True, "helper_sha": H, "request_sha": R}


@pytest.mark.parametrize("phase", ["before", "paid"])
@pytest.mark.parametrize("stall", ["reader", "metadata"])
def test_monotonic_session_window_survives_wall_rollback_before_admission(
        tmp_path, monkeypatch, capsys, phase, stall):
    env, event, data = fixture(phase)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["GH_TOKEN"] = "synthetic-github-token"
    wall, mono, calls = [gate.PRICE_VALID_UNTIL - 1210], [1000.0], []
    last = ("actions/runs/2/approvals" if phase == "paid"
            else p.page_path("actions/runs/2/jobs?filter=all", 1))

    def rollback():
        wall[0] -= 3600
        mono[0] += 11

    def read(path):
        calls.append(path)
        if stall == "metadata" and path == last:
            rollback()
        return deepcopy(data[path])

    def reader(token):
        if stall == "reader":
            rollback()
        return read

    def forbidden(*args, **kwargs):
        pytest.fail("Wall-clock rollback must not grant a fresh session or admission")

    monkeypatch.setattr(p.os, "environ", env)
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    monkeypatch.setattr(gate.time, "monotonic", lambda: mono[0])
    monkeypatch.setattr(p, "GitHubReader", reader)
    monkeypatch.setattr(p, "write_admission", forbidden)
    args = ["--phase", phase]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error_code": "PRICE_REVIEW_EXPIRED",
    }
    if stall == "reader":
        assert calls == []
    else:
        assert calls[-1] == last
    assert not admission.exists()


@pytest.mark.parametrize(("phase", "boundary"), [
    ("before", "event"), ("paid", "event"),
    ("before", "validated"), ("paid", "validated"),
    ("paid", "fsync"), ("paid", "written"),
])
@pytest.mark.parametrize("clock_change", ["elapsed", "rollback"])
def test_stalled_handoffs_never_report_success_or_admit_expired_quote(
        tmp_path, monkeypatch, capsys, phase, boundary, clock_change):
    env, event, data = fixture(phase)
    event_path, admission = tmp_path / "event.json", tmp_path / "admission.json"
    event_path.write_text(json.dumps(event))
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["GH_TOKEN"] = "synthetic-github-token"
    wall, mono, readers, credentials = [gate.PRICE_VALID_UNTIL - 1210], [1000.0], [], []

    class GuardedEnvironment(dict):
        def get(self, key, default=None):
            if key == "GH_TOKEN":
                credentials.append(key)
            return super().get(key, default)

    def stall():
        wall[0] += 11 if clock_change == "elapsed" else -3600
        mono[0] += 11

    def reader(token):
        readers.append(token)
        return data.__getitem__

    read_text, validate, fsync, write = p.Path.read_text, p.preflight, p.os.fsync, p.write_admission

    def stalled_read(path, *args, **kwargs):
        result = read_text(path, *args, **kwargs)
        if path == event_path:
            stall()
        return result

    def stalled_validation(**kwargs):
        result = validate(**kwargs)
        stall()
        return result

    def stalled_fsync(fd):
        fsync(fd)
        stall()

    def stalled_write(*args, **kwargs):
        write(*args, **kwargs)
        stall()

    monkeypatch.setattr(p.os, "environ", GuardedEnvironment(env))
    monkeypatch.setattr(gate.time, "time", lambda: wall[0])
    monkeypatch.setattr(gate.time, "monotonic", lambda: mono[0])
    monkeypatch.setattr(p, "GitHubReader", reader)
    if boundary == "event":
        monkeypatch.setattr(p.Path, "read_text", stalled_read)
    elif boundary == "validated":
        monkeypatch.setattr(p, "preflight", stalled_validation)
    elif boundary == "fsync":
        monkeypatch.setattr(p.os, "fsync", stalled_fsync)
    else:
        monkeypatch.setattr(p, "write_admission", stalled_write)
    args = ["--phase", phase]
    if phase == "paid":
        args += ["--admission-file", str(admission)]
    assert p.main(args) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error_code": "PRICE_REVIEW_EXPIRED",
    }
    # A failed write may leave an exclusive file, but never a successful gate.
    assert admission.exists() is (boundary in {"fsync", "written"})
    if boundary == "event":
        assert readers == credentials == []
