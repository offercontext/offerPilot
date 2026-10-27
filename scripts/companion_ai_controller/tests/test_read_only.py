from __future__ import annotations

import hashlib
import io
import json
import stat
from urllib.error import HTTPError
import zipfile

import pytest

from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_controller import read_only as r


def response(value, **kwargs):
    return r.Response(json.dumps(value).encode(), **kwargs)


def run(run_id=r.H_RUN, sha=p.SOURCE_SHA):
    return {"id": run_id, "workflow_id": r.WORKFLOW, "head_sha": sha, "run_attempt": 1,
            "head_branch": r.BRANCH, "event": "push", "status": "completed",
            "conclusion": "success", "repository": {"id": p.REPOSITORY_ID,
            "full_name": "offercontext/offerPilot"}}


def jobs(run_id=r.H_RUN, sha=p.SOURCE_SHA, paid=False):
    return [{"id": run_id * 10 + i, "run_id": run_id, "head_sha": sha,
             "name": name, "status": "completed",
             "conclusion": "success" if name == r.old_gate.OFFLINE_JOB or paid else "skipped"}
            for i, name in enumerate(sorted(r.old_gate.JOBS))]


def fixtures():
    return {"": response({"id": p.REPOSITORY_ID, "full_name": "offercontext/offerPilot", "private": False}),
        f"/git/commits/{p.SOURCE_SHA}": response({"sha": p.SOURCE_SHA, "tree": {"sha": p.SOURCE_TREE}}),
        f"/actions/runs/{r.H_RUN}": response(run()),
        f"/actions/runs/{r.H_RUN}/jobs?per_page=100&page=1": response({"total_count": 3, "jobs": jobs()}),
        f"/actions/artifacts/{r.ARTIFACT}": response({"id": r.ARTIFACT,
            "name": f"companion-ai-offline-{p.SOURCE_SHA}", "size_in_bytes": r.ZIP_SIZE,
            "expired": False, "digest": "sha256:" + r.ZIP_SHA256,
            "workflow_run": {"id": r.H_RUN, "head_sha": p.SOURCE_SHA, "head_branch": r.BRANCH}})}


def mutate(mapping, path, key, value):
    data = json.loads(mapping[path].body)
    data[key] = value
    mapping[path] = response(data)


def test_metadata_only_explicitly_incomplete():
    data = fixtures()
    report = r.collect_source(data.__getitem__)
    assert report["artifact_download_unverified"] is True
    assert report["source_evidence_verified"] is False
    assert report["live_allowed"] is False and report["production_admission"] is False
    assert report["lineage"]["scope"] == "not_scanned"


@pytest.mark.parametrize("path,key,value", [
    ("", "id", 1), ("", "private", True),
    (f"/git/commits/{p.SOURCE_SHA}", "tree", {"sha": "b"*40}),
    (f"/actions/runs/{r.H_RUN}", "run_attempt", 2),
    (f"/actions/runs/{r.H_RUN}", "head_sha", "b"*40),
    (f"/actions/runs/{r.H_RUN}", "workflow_id", 1),
    (f"/actions/runs/{r.H_RUN}", "head_branch", "master"),
    (f"/actions/artifacts/{r.ARTIFACT}", "expired", True),
    (f"/actions/artifacts/{r.ARTIFACT}", "digest", "sha256:" + "b"*64),
    (f"/actions/artifacts/{r.ARTIFACT}", "workflow_run", {"id": 1}),
])
def test_identity_mismatch(path, key, value):
    data = fixtures()
    mutate(data, path, key, value)
    with pytest.raises(p.Blocked):
        r.collect_source(data.__getitem__)


@pytest.mark.parametrize("name", [r.old_gate.PAID_JOB, r.old_gate.PREFLIGHT_JOB])
def test_h_preflight_and_paid_must_be_skipped(name):
    data = fixtures()
    rows = jobs()
    next(row for row in rows if row["name"] == name)["conclusion"] = "success"
    data[f"/actions/runs/{r.H_RUN}/jobs?per_page=100&page=1"] = response({"total_count": 3, "jobs": rows})
    with pytest.raises(p.Blocked, match="JOB_STATE_INVALID"):
        r.collect_source(data.__getitem__)


@pytest.mark.parametrize("path", ["https://evil.invalid", "//evil.invalid", "/../secrets",
    "/actions/secrets", "/actions/artifacts/12707161298/zip", "/actions/runs/1\r\nAuthorization:x",
    "/actions/runs/1?token=x", "/actions/runs/1/approvals", "/environments/anything"])
def test_get_allowlist_rejects_external_or_sensitive_paths(path):
    reader = r.PublicReader()
    with pytest.raises(p.Blocked, match="GET_PATH_NOT_ALLOWED"):
        reader(path)


def test_transport_is_credential_free_fixed_get(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "sentinel-gh-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "sentinel-gh-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://evil.invalid")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/must-not-be-used")
    monkeypatch.setenv("SSLKEYLOGFILE", "/nonexistent/must-not-be-created")
    reader = r.PublicReader()
    captured = []
    class Stream:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def geturl(self): return r.ROOT
        def read(self, limit):
            assert limit == r.MAX_RESPONSE + 1
            return b'{}'
    def open_request(request, timeout):
        captured.append(request)
        assert timeout == 15
        return Stream()
    monkeypatch.setattr(reader.opener, "open", open_request)
    assert reader("").body == b'{}'
    request = captured[0]
    assert request.get_method() == "GET"
    assert "authorization" not in {key.lower() for key in request.headers}
    assert "sentinel" not in str(request.headers)
    assert not any(handler.__class__.__name__ == "ProxyHandler" and handler.proxies
                   for handler in reader.opener.handlers)


def test_redirect_handler_never_follows_or_emits_location():
    with pytest.raises(p.Blocked, match="^REDIRECT_FORBIDDEN$"):
        r.NoRedirect().redirect_request(None, None, 302, "x", {}, "https://evil.invalid/?secret=123")


def test_http_error_text_not_leaked(monkeypatch):
    reader = r.PublicReader()
    def fail(*args, **kwargs):
        raise HTTPError("https://secret.invalid", 403, "sentinel-secret", {}, None)
    monkeypatch.setattr(reader.opener, "open", fail)
    with pytest.raises(p.Blocked, match="^PUBLIC_GET_FAILED$"):
        reader("")


@pytest.mark.parametrize("resp", [r.Response(b'{}', 302, {"location": "https://evil.invalid"}),
    r.Response(b'{}', 429), r.Response(b'{}', 200, {"retry-after": "1"}),
    r.Response(b'{}', 200, {"x-ratelimit-remaining": "0"}),
    r.Response(b'{}', 200, {"x-header": "bad\r\nInjected:x"}),
    r.Response(b'{"id":1,"id":2}'), r.Response(b'{"v":NaN}'),
    r.Response(b' ' * (r.MAX_RESPONSE + 1))])
def test_fail_closed_http_json_and_limits(resp):
    with pytest.raises(p.Blocked):
        r.collect_source(lambda _: resp)


def test_pagination_success_and_replay_rejection():
    path = f"/actions/runs/{r.H_RUN}/jobs"
    rows = [{"id": i} for i in range(1, 102)]
    data = {path+"?per_page=100&page=1": response({"total_count": 101, "jobs": rows[:100]},
        headers={"link": f'<{r.ROOT}{path}?per_page=100&page=2>; rel="next"'}),
        path+"?per_page=100&page=2": response({"total_count": 101, "jobs": rows[100:]})}
    assert len(r._pages(data.__getitem__, path, "jobs")) == 101
    data[path+"?per_page=100&page=2"] = response({"total_count": 101, "jobs": [rows[0]]})
    with pytest.raises(p.Blocked, match="PAGINATION_DUPLICATE"):
        r._pages(data.__getitem__, path, "jobs")


@pytest.mark.parametrize("link", [
    '<https://evil.invalid/anything>; rel="next"',
    f'<{r.ROOT}/actions/runs/{r.H_RUN}/jobs?per_page=100&page=1>; rel="next"',
    f'<{r.ROOT}/actions/runs/{r.H_RUN}/jobs?per_page=100&page=2&token=x>; rel="next"',
    f'<{r.ROOT}/actions/runs/{r.H_RUN}/jobs?per_page=100&page=2#bad>; rel="next"',
    'malformed',
])
def test_pagination_links_are_not_arbitrary_network_capabilities(link):
    with pytest.raises((p.Blocked, ValueError)):
        r._next(link, f"/actions/runs/{r.H_RUN}/jobs", 1)


def test_missing_pagination_is_not_complete():
    with pytest.raises(p.Blocked, match="PAGINATION_INCOMPLETE"):
        r._pages(lambda _: response({"total_count": 4, "jobs": [{"id": 1}]}),
                 f"/actions/runs/{r.H_RUN}/jobs", "jobs")


def archive(aggregate):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        info = zipfile.ZipInfo("companion-ai-offline.json")
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        output.writestr(info, json.dumps(aggregate).encode())
    return stream.getvalue()


def aggregate():
    nodes = [f"test::{i}" for i in range(r.NODES)]
    return {"passed": True, "exit_code": 0, "collected": nodes, "deselected": [],
            "finished": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}
                         for node in nodes}}


def pin_test_archive(monkeypatch, raw, value):
    # Independent parser unit fixture, not a change to collector production pins.
    monkeypatch.setattr(r, "ZIP_SIZE", len(raw))
    monkeypatch.setattr(r, "ZIP_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(r, "AGGREGATE_SHA256", hashlib.sha256(json.dumps(value).encode()).hexdigest())


def test_full_node_aggregate_parser(monkeypatch):
    value = aggregate()
    raw = archive(value)
    pin_test_archive(monkeypatch, raw, value)
    r._verify_aggregate(raw)
    data = fixtures()
    report = r.collect_source(data.__getitem__, artifact_bytes=raw)
    assert report["source_evidence_verified"] is True and report["aggregate_node_count"] == 2267
    assert report["live_allowed"] is False and report["environment_verified"] is False


@pytest.mark.parametrize("mutation", ["skip", "deselected", "missing", "duplicate", "false_exit"])
def test_passed_boolean_not_enough(monkeypatch, mutation):
    value = aggregate()
    if mutation == "skip":
        value["finished"][value["collected"][0]]["call"] = "skipped"
    if mutation == "deselected":
        value["deselected"] = ["test::skip"]
    if mutation == "missing":
        value["finished"].pop(value["collected"][0])
    if mutation == "duplicate":
        value["collected"][1] = value["collected"][0]
    if mutation == "false_exit":
        value["exit_code"] = False
    raw = archive(value)
    pin_test_archive(monkeypatch, raw, value)
    with pytest.raises(p.Blocked):
        r._verify_aggregate(raw)


def test_local_zip_wrong_hash_is_never_executed():
    data = fixtures()
    with pytest.raises(p.Blocked, match="H_ARTIFACT_BYTES_INVALID"):
        r.collect_source(data.__getitem__, artifact_bytes=b'print("malicious")')


def historical_fixtures():
    data = fixtures()
    rows = []
    for batch in r.LEGACY_BATCHES:
        for run_id, sha, paid in ((batch.helper_run_id, batch.helper_sha, False),
                                   (batch.run_id, batch.source_sha, True)):
            entry = run(run_id, sha)
            entry["run_number"] = len(rows) + 1
            rows.append(entry)
            data[f"/actions/runs/{run_id}/jobs?per_page=100&page=1"] = response({"total_count": 3,
                "jobs": jobs(run_id, sha, paid=paid)})
        data[f"/actions/artifacts/{batch.artifact_id}"] = response({"id": batch.artifact_id,
            "name": f"companion-ai-{batch.source_sha}", "size_in_bytes": batch.archive_size,
            "digest": "sha256:" + batch.archive_sha256, "expired": False,
            "url": r.ROOT + f"/actions/artifacts/{batch.artifact_id}",
            "workflow_run": {"id": batch.run_id, "repository_id": p.REPOSITORY_ID,
                "head_repository_id": p.REPOSITORY_ID, "head_branch": r.BRANCH, "head_sha": batch.source_sha}})
    h = run()
    h["run_number"] = len(rows) + 1
    rows.append(h)
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    return data, rows


def test_four_history_runs_live_metadata_plus_real_archive_replay():
    data, _ = historical_fixtures()
    result = r.collect_source(data.__getitem__, scan_history=True)
    assert result["lineage"]["settled_micro_cny"] == 411934
    assert result["lineage"]["prior_calls"] == 48
    assert result["lineage"]["owner_approvals_verified"] is False


def test_unknown_paid_run_is_global_halt():
    data, rows = historical_fixtures()
    unknown = run(12345, "b"*40)
    unknown["run_number"] = len(rows) + 1
    rows.append(unknown)
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    data["/actions/runs/12345/jobs?per_page=100&page=1"] = response(
        {"total_count": 3, "jobs": jobs(12345, "b"*40, paid=True)})
    with pytest.raises(p.Blocked, match="UNKNOWN_PAID_OR_UNRESOLVED"):
        r.collect_source(data.__getitem__, scan_history=True)


def test_deleted_history_gap():
    data, rows = historical_fixtures()
    rows[1]["run_number"] = 3
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    with pytest.raises(p.Blocked, match="LEGACY_RUN_HISTORY_GAP"):
        r.collect_source(data.__getitem__, scan_history=True)


@pytest.mark.parametrize("kind", ["extra", "symlink", "traversal", "duplicate"])
def test_malicious_zip_members_after_digest_validation(monkeypatch, kind):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        name = "../companion-ai-offline.json" if kind == "traversal" else "companion-ai-offline.json"
        info = zipfile.ZipInfo(name)
        info.external_attr = ((stat.S_IFLNK if kind == "symlink" else stat.S_IFREG) | 0o600) << 16
        output.writestr(info, b'{}')
        if kind in ("extra", "duplicate"):
            output.writestr(name if kind == "duplicate" else "execute.py", b'print("bad")')
    raw = stream.getvalue()
    monkeypatch.setattr(r, "ZIP_SIZE", len(raw))
    monkeypatch.setattr(r, "ZIP_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(p.Blocked):
        r._verify_aggregate(raw)


def test_unknown_run_repository_replay_rejected():
    data, rows = historical_fixtures()
    unknown = run(12345, "b" * 40)
    unknown["run_number"] = len(rows) + 1
    unknown["repository"]["id"] = 1
    rows.append(unknown)
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    with pytest.raises(p.Blocked, match="LEGACY_RUN_IDENTITY_INVALID"):
        r.collect_source(data.__getitem__, scan_history=True)


@pytest.mark.parametrize("missing_id", [batch.helper_run_id for batch in r.LEGACY_BATCHES] + [r.H_RUN])
def test_every_helper_and_h_anchor_required_even_without_gap(missing_id):
    data, rows = historical_fixtures()
    rows = [row for row in rows if row["id"] != missing_id]
    for number, row in enumerate(rows, 1):
        row["run_number"] = number
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    with pytest.raises(p.Blocked, match="LEGACY_RUNS_MISSING"):
        r.collect_source(data.__getitem__, scan_history=True)


def test_anchor_order_cannot_be_replayed():
    data, rows = historical_fixtures()
    rows[0]["run_number"], rows[1]["run_number"] = rows[1]["run_number"], rows[0]["run_number"]
    data[f"/actions/workflows/{r.WORKFLOW}/runs?per_page=100&page=1"] = response(
        {"total_count": len(rows), "workflow_runs": rows})
    with pytest.raises(p.Blocked, match="LEGACY_ANCHOR_ORDER_INVALID"):
        r.collect_source(data.__getitem__, scan_history=True)


@pytest.mark.parametrize("batch", r.LEGACY_BATCHES)
@pytest.mark.parametrize("failure", ["missing", "expired", "wrong_owner", "wrong_hash"])
def test_all_remote_archive_metadata_required(batch, failure):
    data, _ = historical_fixtures()
    path = f"/actions/artifacts/{batch.artifact_id}"
    if failure == "missing":
        data[path] = response({"message": "Not Found"}, status=404)
    elif failure == "expired":
        mutate(data, path, "expired", True)
    elif failure == "wrong_owner":
        mutate(data, path, "workflow_run", {"id": r.H_RUN})
    else:
        mutate(data, path, "digest", "sha256:" + "b"*64)
    with pytest.raises(p.Blocked):
        r.collect_source(data.__getitem__, scan_history=True)


@pytest.mark.parametrize("condition", ["elapsed", "requests"])
def test_public_read_session_budget_without_network(condition):
    reader = r.PublicReader()
    if condition == "elapsed":
        reader.started -= 121
    else:
        reader.requests = 256
    with pytest.raises(p.Blocked, match="PUBLIC_READ_BUDGET_EXHAUSTED"):
        reader("")
