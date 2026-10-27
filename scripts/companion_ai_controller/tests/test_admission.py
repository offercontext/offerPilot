from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from scripts.companion_ai_controller import admission as a
from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_controller import read_only as ro
from scripts.companion_ai_controller.tests import test_read_only as h

NOW = p.DEADLINE-3600


def make_fixture():
    anchors = (a.DryRunAnchor(40447671170, a.DRY_WORKFLOW,
        "3e03dad887c17845c4e4414ce537ffc25c4bb1f5", "9a34228203f01b44bcfe6ba10121215309bd9dbd",
        "Controller fake-only contract rehearsal"),
        a.DryRunAnchor(40450655710, a.DRY_WORKFLOW,
        "95583e10e0199230abe9d6541d8f326269f799a4", "c5c4d6da2a9198004afb1443bdf023a31969f930"),
        a.DryRunAnchor(900005, a.DRY_WORKFLOW, "a"*40, "e"*40, a.CONTROL_DRY_JOB,
                       "workflow_dispatch", "ai-validation-control"))
    reg = a.Registration("a"*40, 900001, 900002, "fixture-controller", 900003,
                         anchors, (800001,), a.SETUP_ATTESTATION, a.CAMPAIGN_CHECKPOINT)
    ctx = a.RunContext(p.REPOSITORY_ID, p.CONTROL_REF, reg.controller_sha, reg.controller_sha,
                       900004, 1, "workflow_dispatch", p.OWNER_ID, p.OWNER_ID)
    data, _ = h.historical_fixtures()
    data["/git/ref/heads/ai-validation-control"] = h.response({"ref": p.CONTROL_REF,
        "object": {"type": "commit", "sha": reg.controller_sha}})
    data[f"/git/commits/{reg.controller_sha}"] = h.response({"sha": reg.controller_sha, "tree": {"sha": "b"*40}})
    data[f"/git/trees/{'b'*40}?recursive=1"] = h.response({"sha": "b"*40, "truncated": False,
        "tree": [{"path": path, "type": "blob", "mode": "100644", "sha": "e"*40}
                 for path in (a.WORKFLOW_PATH, a.DRY_PATH)]})
    for wid, path, state in ((ro.WORKFLOW, ro.old_gate.WORKFLOW, "disabled_manually"),
                            (reg.workflow_id, a.WORKFLOW_PATH, "active"),
                            (a.DRY_WORKFLOW, a.DRY_PATH, "active")):
        data[f"/actions/workflows/{wid}"] = h.response({"id": wid, "path": path, "state": state})
    data["/actions/workflows?per_page=100&page=1"] = h.response({"total_count": 4,
        "workflows": [{"id": ro.WORKFLOW, "path": ro.old_gate.WORKFLOW},
            {"id": reg.workflow_id, "path": a.WORKFLOW_PATH}, {"id": a.DRY_WORKFLOW, "path": a.DRY_PATH},
            {"id": 800001, "path": ".github/workflows/unrelated.yml"}]})
    dry = []
    for number, anchor in enumerate(anchors, 1):
        row = h.run(anchor.run_id, anchor.head_sha)
        row.update(workflow_id=a.DRY_WORKFLOW, run_number=number,
                   head_branch=anchor.head_branch, event=anchor.event)
        dry.append(row)
        data[f"/actions/runs/{anchor.run_id}"] = h.response(row)
        data[f"/actions/runs/{anchor.run_id}/jobs?per_page=100&page=1"] = h.response({"total_count": 1,
            "jobs": [{"id": anchor.run_id*10, "run_id": anchor.run_id, "head_sha": anchor.head_sha,
                      "name": anchor.job_name, "status": "completed", "conclusion": "success"}]})
        if number <= 2:
            tree_sha = ("c" if number == 1 else "d")*40
            data[f"/git/commits/{anchor.head_sha}"] = h.response({"sha": anchor.head_sha, "tree": {"sha": tree_sha}})
            data[f"/git/trees/{tree_sha}?recursive=1"] = h.response({"sha": tree_sha, "truncated": False,
                "tree": [{"path": a.DRY_PATH, "type": "blob", "mode": "100644", "sha": anchor.workflow_blob_sha}]})
        else:
            data[f"/actions/runs/{anchor.run_id}/approvals"] = h.response([{"state": "approved",
                "user": {"id": p.OWNER_ID, "type": "User", "login": "XiaoZheBrother"},
                "environments": [{"id": reg.environment_id, "name": reg.environment_name}]}])
    data[f"/actions/workflows/{a.DRY_WORKFLOW}/runs?per_page=100&page=1"] = h.response(
        {"total_count": len(dry), "workflow_runs": dry})
    row = h.run(ctx.run_id, reg.controller_sha)
    row.update(workflow_id=reg.workflow_id, run_number=1, head_branch="ai-validation-control",
               event="workflow_dispatch", status="in_progress", conclusion=None,
               actor={"id": p.OWNER_ID}, triggering_actor={"id": p.OWNER_ID})
    data[f"/actions/workflows/{reg.workflow_id}/runs?per_page=100&page=1"] = h.response(
        {"total_count": 1, "workflow_runs": [row]})
    data[f"/actions/runs/{ctx.run_id}/jobs?per_page=100&page=1"] = h.response({"total_count": 2,
        "jobs": [{"id": 900010, "run_id": ctx.run_id, "head_sha": reg.controller_sha,
                  "name": a.OFFLINE_JOB, "status": "completed", "conclusion": "success"},
                 {"id": 900011, "run_id": ctx.run_id, "head_sha": reg.controller_sha,
                  "name": a.PREFLIGHT_JOB, "status": "in_progress", "conclusion": None}]})
    data[f"/environments/{reg.environment_name}"] = h.response({"id": reg.environment_id,
        "name": reg.environment_name, "can_admins_bypass": False, "protection_rules": [
            {"type": "required_reviewers", "prevent_self_review": False, "reviewers": [
                {"type": "User", "reviewer": {"id": p.OWNER_ID, "type": "User", "login": "XiaoZheBrother"}}]},
            {"type": "branch_policy"}],
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}})
    data[f"/environments/{reg.environment_name}/deployment-branch-policies?per_page=100&page=1"] = h.response(
        {"total_count": 1, "branch_policies": [{"id": reg.branch_policy_id, "type": "branch",
                                                "name": "ai-validation-control"}]})
    data[f"/actions/runs/{ctx.run_id}/approvals"] = h.response([])
    return reg, ctx, data


def approve_fixture(reg, ctx, data):
    data = deepcopy(data)
    path = f"/actions/runs/{ctx.run_id}/jobs?per_page=100&page=1"
    value = json.loads(data[path].body)
    value["jobs"][1].update(status="completed", conclusion="success")
    value["jobs"].append({"id": 900012, "run_id": ctx.run_id, "head_sha": reg.controller_sha,
                          "name": a.PAID_JOB, "status": "in_progress", "conclusion": None})
    value["total_count"] = 3
    data[path] = h.response(value)
    data[f"/actions/runs/{ctx.run_id}/approvals"] = h.response([{"state": "approved",
        "user": {"id": p.OWNER_ID, "type": "User", "login": "XiaoZheBrother"},
        "environments": [{"id": reg.environment_id, "name": reg.environment_name}]}])
    return data


def test_real_structure_before_and_paid_with_fake_http():
    reg, ctx, data = make_fixture()
    before = a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)
    paid_data = approve_fixture(reg, ctx, data)
    paid = a.verify_paid(reg, ctx, before, reader=paid_data.__getitem__, now=NOW+1)
    assert before.phase == "before" and paid.phase == "paid"
    assert before.binding_digest == paid.binding_digest
    assert paid.expires_at == NOW+121
    assert paid.prior_calls == 48 and paid.settled_micro_cny == 411934
    assert paid.payload_hashes == p.REQUEST_HASHES


def test_default_missing_registration_does_not_read():
    _, ctx, _ = make_fixture()
    with pytest.raises(p.Blocked, match="PRODUCTION_UNCONFIGURED"):
        a.verify_before(None, ctx, reader=lambda _: pytest.fail("read before configured"), now=NOW)


def test_registration_json_external_sha_and_handoff_roundtrip():
    reg, ctx, data = make_fixture()
    value = json.loads(json.dumps(asdict(reg)))
    value.pop("controller_sha")
    assert a.Registration.from_mapping(value, controller_sha=reg.controller_sha) == reg
    before = a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)
    assert a.ProductionAdmission.from_mapping(json.loads(json.dumps(asdict(before)))) == before
    value["controller_sha"] = reg.controller_sha
    with pytest.raises(p.Blocked):
        a.Registration.from_mapping(value, controller_sha=reg.controller_sha)


@pytest.mark.parametrize("field,value", [("attempt", 2), ("ref", "refs/heads/master"),
    ("sha", "f"*40), ("workflow_sha", "f"*40), ("event", "push"),
    ("actor_id", 123), ("triggering_actor_id", 123)])
def test_runtime_identity(field, value):
    reg, ctx, data = make_fixture()
    with pytest.raises(p.Blocked, match="RUNTIME_CONTEXT_INVALID"):
        a.verify_before(reg, replace(ctx, **{field: value}), reader=data.__getitem__, now=NOW)


@pytest.mark.parametrize("what", ["active_old", "ref", "env_bypass", "wrong_reviewer", "branch_tag",
    "extra_control_workflow", "other_reaches_control", "truncated_tree", "dry_blob", "new_workflow"])
def test_gate_rejections(what):
    reg, ctx, data = make_fixture()
    if what == "active_old":
        h.mutate(data, f"/actions/workflows/{ro.WORKFLOW}", "state", "active")
    elif what == "ref":
        h.mutate(data, "/git/ref/heads/ai-validation-control", "object", {"type": "commit", "sha": "f"*40})
    elif what == "env_bypass":
        h.mutate(data, f"/environments/{reg.environment_name}", "can_admins_bypass", True)
    elif what == "wrong_reviewer":
        h.mutate(data, f"/environments/{reg.environment_name}", "protection_rules", [])
    elif what == "branch_tag":
        path = f"/environments/{reg.environment_name}/deployment-branch-policies?per_page=100&page=1"
        value = json.loads(data[path].body)
        value["branch_policies"][0]["type"] = "tag"
        data[path] = h.response(value)
    elif what in ("extra_control_workflow", "truncated_tree"):
        path = f"/git/trees/{'b'*40}?recursive=1"
        value = json.loads(data[path].body)
        if what == "truncated_tree":
            value["truncated"] = True
        else:
            value["tree"].append({"path": ".github/workflows/evil.yml", "mode": "100644", "type": "blob", "sha": "f"*40})
        data[path] = h.response(value)
    elif what == "dry_blob":
        path = f"/git/trees/{'c'*40}?recursive=1"
        value = json.loads(data[path].body)
        value["tree"][0]["sha"] = "f"*40
        data[path] = h.response(value)
    else:
        path = "/actions/workflows?per_page=100&page=1"
        value = json.loads(data[path].body)
        if what == "new_workflow":
            value["workflows"].append({"id": 99999, "path": "evil.yml"})
            value["total_count"] += 1
        else:
            value["workflows"][-1]["path"] = a.WORKFLOW_PATH
        data[path] = h.response(value)
    with pytest.raises(p.Blocked):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)


def test_approval_and_toctou_rechecked():
    reg, ctx, data = make_fixture()
    before = a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)
    paid = approve_fixture(reg, ctx, data)
    path = f"/actions/runs/{ctx.run_id}/approvals"
    paid[path] = h.response([])
    with pytest.raises(p.Blocked, match="OWNER_APPROVAL_UNVERIFIED"):
        a.verify_paid(reg, ctx, before, reader=paid.__getitem__, now=NOW+1)
    paid = approve_fixture(reg, ctx, data)
    h.mutate(paid, f"/environments/{reg.environment_name}", "updated_at", "changed-after-review")
    with pytest.raises(p.Blocked, match="APPROVAL_TOCTOU_MISMATCH"):
        a.verify_paid(reg, ctx, before, reader=paid.__getitem__, now=NOW+1)


@pytest.mark.parametrize("status", ["completed", "cancelled", "in_progress"])
def test_prior_paid_start_blocks_even_renamed_batch(status):
    reg, ctx, data = make_fixture()
    path = f"/actions/workflows/{reg.workflow_id}/runs?per_page=100&page=1"
    value = json.loads(data[path].body)
    prior = deepcopy(value["workflow_runs"][0])
    prior.update(id=999001, run_number=1, status=status)
    value["workflow_runs"][0]["run_number"] = 2
    value["workflow_runs"].insert(0, prior)
    value["total_count"] = 2
    data[path] = h.response(value)
    data["/actions/runs/999001/jobs?per_page=100&page=1"] = h.response({"total_count": 1,
        "jobs": [{"id": 999002, "run_id": 999001, "head_sha": reg.controller_sha,
                  "name": a.PAID_JOB, "status": status, "conclusion": "failure"}]})
    with pytest.raises(p.Blocked, match="PRIOR_PAID_STARTED_OR_UNKNOWN"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)


def test_unknown_dry_run_no_exemption():
    reg, ctx, data = make_fixture()
    path = f"/actions/workflows/{a.DRY_WORKFLOW}/runs?per_page=100&page=1"
    value = json.loads(data[path].body)
    row = deepcopy(value["workflow_runs"][0])
    row["id"] = 99901
    value["workflow_runs"].append(row)
    value["total_count"] += 1
    data[path] = h.response(value)
    with pytest.raises(p.Blocked, match="UNREGISTERED_DRY_RUN"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)


@pytest.mark.parametrize("now", [p.DEADLINE-1199, p.DEADLINE, float("nan"), True])
def test_expired_and_window(now):
    reg, ctx, data = make_fixture()
    with pytest.raises(p.Blocked, match="DEADLINE_OR_WINDOW_EXPIRED"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=now)


def test_owner_checkpoint_not_implicit():
    reg, ctx, data = make_fixture()
    with pytest.raises(p.Blocked, match="REGISTRATION_INVALID"):
        a.verify_before(replace(reg, owner_campaign_checkpoint=""), ctx, reader=data.__getitem__, now=NOW)


def test_metadata_token_profile_is_explicit_and_dormant():
    reg, ctx, _ = make_fixture()
    assert reg.reader_mode == "anonymous"
    assert type(a.build_reader(reg, ctx)) is ro.PublicReader
    with pytest.raises(p.Blocked, match="UNEXPECTED_METADATA_TOKEN"):
        a.build_reader(reg, ctx, metadata_token="ghs_" + "s"*36)
    enabled = replace(reg, reader_mode="github_actions_read")
    with pytest.raises(p.Blocked, match="EXPLICIT_METADATA_TOKEN_REQUIRED"):
        a.build_reader(enabled, ctx)
    reader = a.build_reader(enabled, ctx, metadata_token="ghs_" + "s"*36)
    assert type(reader) is ro.GitHubActionsReadReader
    assert "ghs_" not in repr(reader)


def test_explicit_token_transport_fixed_host_only(monkeypatch):
    token = "ghs_" + "s"*36
    reader = ro.GitHubActionsReadReader(token=token)
    requests = []
    class Stream:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def geturl(self): return ro.ROOT
        def read(self, _): return b'{}'
    def opened(req, timeout):
        requests.append(req)
        return Stream()
    monkeypatch.setattr(reader.opener, "open", opened)
    reader("")
    assert requests[0].full_url == ro.ROOT
    assert requests[0].get_method() == "GET"
    assert requests[0].headers["Authorization"] == "Bearer " + token
    with pytest.raises(p.Blocked, match="GET_PATH_NOT_ALLOWED"):
        reader("https://evil.invalid")
    assert len(requests) == 1
    with pytest.raises(p.Blocked, match="REDIRECT_FORBIDDEN"):
        ro.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid")


@pytest.mark.parametrize("token", [None, "", "abc\r\nAuthorization:bad", "short", "x"*257])
def test_token_header_injection_and_empty_fail_closed(token):
    with pytest.raises(p.Blocked):
        ro.GitHubActionsReadReader(token=token)


def test_token_never_falls_back_after_failure(monkeypatch):
    from urllib.error import HTTPError
    reader = ro.GitHubActionsReadReader(token="ghs_" + "s"*36)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError(ro.ROOT, 403, "ghs_secret_must_not_escape", {}, None)
    monkeypatch.setattr(reader.opener, "open", fail)
    with pytest.raises(p.Blocked, match="^PUBLIC_GET_FAILED$"):
        reader("")
    assert calls == [1]


@pytest.mark.parametrize("path", ["/actions/secrets", "/environments/fixture/secrets",
    "/actions/workflows/1/dispatches", "https://evil.invalid", "/git/refs/heads/master"])
def test_registration_cannot_expand_reader_to_write_or_secret_paths(path):
    with pytest.raises(p.Blocked, match="REGISTERED_GET_PATH_INVALID"):
        ro.PublicReader(registered_paths=frozenset({path}))


def test_unchanged_known_dry_anchors_are_mandatory():
    reg, ctx, data = make_fixture()
    anchors = (replace(reg.dry_run_anchors[0], head_sha="f"*40), reg.dry_run_anchors[1])
    with pytest.raises(p.Blocked, match="KNOWN_DRY_ANCHOR_CHANGED"):
        a.verify_before(replace(reg, dry_run_anchors=anchors), ctx, reader=data.__getitem__, now=NOW)


@pytest.mark.parametrize("path", [".github/workflows/extra.yaml", ".GitHub/Workflows/EXTRA.YML",
                                   ".github/workflows/README", ".github/workflows/extra/action.yml"])
def test_control_tree_rejects_all_extra_workflow_case_and_suffix_forms(path):
    reg, ctx, data = make_fixture()
    endpoint = f"/git/trees/{'b'*40}?recursive=1"
    value = json.loads(data[endpoint].body)
    value["tree"].append({"path": path, "mode": "100644", "type": "blob", "sha": "f"*40})
    data[endpoint] = h.response(value)
    with pytest.raises(p.Blocked, match="CONTROL_TREE_EXTRA_ENTRYPOINT"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)


def test_expired_paid_metadata_session(monkeypatch):
    reg, ctx, data = make_fixture()
    times = iter([NOW, NOW+121])
    monkeypatch.setattr(a.time, "time", lambda: next(times))
    with pytest.raises(p.Blocked, match="ADMISSION_READ_DEADLINE"):
        a.verify_before(reg, ctx, reader=data.__getitem__)


def test_public_probe_never_verifies_control():
    reg, _, data = make_fixture()
    env = json.loads(data[f"/environments/{reg.environment_name}"].body)
    env.update(id=24931188785, name="offerpilot-companion-ai-20261021")
    data["/environments/offerpilot-companion-ai-20261021"] = h.response(env)
    for anchor in a.KNOWN_DRY_ANCHORS:
        data[f"/actions/runs/{anchor.run_id}/approvals"] = h.response([])
    report = a.probe_existing_metadata(data.__getitem__)
    assert report["production_admission"] is False and report["live_allowed"] is False
    assert report["configured_control_verified"] is False
    assert report["owner_current_run_approval_verified"] is False
    assert [row["approval_record_count"] for row in report["dry_run_anchors"]] == [0, 0]


def platform_fixture():
    reg, ctx, data = make_fixture()
    # Initial deployment registration does not guess the platform run ID.
    reg = replace(reg, dry_run_anchors=reg.dry_run_anchors[:2])
    ctx = replace(ctx, run_id=900005)
    row = json.loads(data[f"/actions/runs/{ctx.run_id}"].body)
    row.update(status="in_progress", conclusion=None,
               actor={"id": p.OWNER_ID}, triggering_actor={"id": p.OWNER_ID})
    data[f"/actions/runs/{ctx.run_id}"] = h.response(row)
    path = f"/actions/runs/{ctx.run_id}/jobs?per_page=100&page=1"
    value = json.loads(data[path].body)
    value["jobs"][0].update(status="in_progress", conclusion=None)
    data[path] = h.response(value)
    return reg, ctx, data


def test_platform_probe_before_final_anchor_registration_is_not_paid():
    reg, ctx, data = platform_fixture()
    report = a.control_probe(reg, ctx, reader=data.__getitem__, now=NOW)
    assert report["mode"] == "platform_only"
    for key in ("production_admission", "live_allowed", "paid_started", "provider_access",
                "campaign_attempt_consumed", "hidden_protection_machine_verified"):
        assert report[key] is False
    assert report["environment_verified"] and report["owner_current_run_approval_verified"]
    assert not isinstance(report, a.ProductionAdmission)


def test_production_requires_completed_registered_platform_rehearsal():
    reg, ctx, data = make_fixture()
    reg = replace(reg, dry_run_anchors=reg.dry_run_anchors[:2])
    with pytest.raises(p.Blocked, match="PLATFORM_REHEARSAL_MISSING"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)


@pytest.mark.parametrize("mutation", ["no_registration", "wrong_job", "no_approval", "wrong_actor",
                                        "wrong_ref", "metadata_token"])
def test_platform_probe_rejections(mutation):
    reg, ctx, data = platform_fixture()
    kwargs = {}
    if mutation == "no_registration":
        reg = None
    elif mutation == "wrong_job":
        path = f"/actions/runs/{ctx.run_id}/jobs?per_page=100&page=1"
        value = json.loads(data[path].body)
        value["jobs"][0]["name"] = a.PAID_JOB
        data[path] = h.response(value)
    elif mutation == "no_approval":
        data[f"/actions/runs/{ctx.run_id}/approvals"] = h.response([])
    elif mutation == "wrong_actor":
        ctx = replace(ctx, actor_id=1)
    elif mutation == "wrong_ref":
        ctx = replace(ctx, ref="refs/heads/master")
    else:
        kwargs["metadata_token"] = "ghs_" + "s"*36
    with pytest.raises(p.Blocked):
        a.control_probe(reg, ctx, reader=data.__getitem__, now=NOW, **kwargs)


def test_platform_probe_never_loads_auth_profile(monkeypatch):
    reg, ctx, data = platform_fixture()
    reg = replace(reg, reader_mode="github_actions_read")
    class AnonymousFixtureReader:
        def __init__(self, *, registered_paths):
            self.registered_paths = registered_paths
        def __call__(self, path):
            return data[path]
    monkeypatch.setattr(ro, "PublicReader", AnonymousFixtureReader)
    monkeypatch.setattr(ro, "GitHubActionsReadReader", lambda **_: pytest.fail("token profile invoked"))
    assert a.control_probe(reg, ctx, now=NOW)["provider_access"] is False


@pytest.mark.parametrize("field,value", [("head_sha", "f"*40), ("event", "push"),
                                         ("head_branch", "master"), ("job_name", a.PAID_JOB)])
def test_control_dry_anchor_is_exact_not_a_free_exemption(field, value):
    reg, ctx, data = make_fixture()
    anchor = replace(reg.dry_run_anchors[-1], **{field: value})
    reg = replace(reg, dry_run_anchors=(*reg.dry_run_anchors[:-1], anchor))
    with pytest.raises(p.Blocked, match="DRY_ANCHORS_INVALID"):
        a.verify_before(reg, ctx, reader=data.__getitem__, now=NOW)
