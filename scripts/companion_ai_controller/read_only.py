"""Credential-free public GitHub evidence collection, never live admission.

Only fixed-repository GET metadata is supported. Artifact download redirects are
intentionally unsupported: provide independently obtained pinned ZIP bytes or
receive an explicitly incomplete report. No candidate code is loaded.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path
import re
import ssl
import stat
import time
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import zipfile

from scripts.companion_ai_controller import history
from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_validation import preflight as old_gate

ROOT = "https://api.github.com/repos/offercontext/offerPilot"
H_RUN = 40441381236
WORKFLOW = 391917473
ARTIFACT = 12707161298
ZIP_SHA256 = "1c3c763823ed078155257cdcff1f9e951a6c20334fda779dccdfbfd732792c63"
ZIP_SIZE = 38414
AGGREGATE_SHA256 = "fcf3fdeb247f6b2e9131b219b81708121c22835e0665aa88214de66dcd740687"
MAX_RESPONSE = 8 * 1024 * 1024
MAX_PAGES = 100
BRANCH = "feat/20261021-companion-life-v1"
NODES = 2267
Blocked = p.Blocked
LEGACY_BATCHES = old_gate.historical_batches(shadow_review=True)
ARTIFACT_IDS = (ARTIFACT,) + tuple(batch.artifact_id for batch in LEGACY_BATCHES)


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)


Reader = Callable[[str], Response]


def _allowed(path: str) -> bool:
    return bool(re.fullmatch(
        r"(?:|/git/commits/" + p.SOURCE_SHA +
        r"|/actions/runs/[1-9][0-9]*(?:/jobs\?per_page=100&page=[1-9][0-9]*)?"
        r"|/actions/artifacts/(?:" + "|".join(map(str, ARTIFACT_IDS)) + ")" +
        r"|/actions/workflows/" + str(WORKFLOW) + r"/runs\?per_page=100&page=[1-9][0-9]*)", path))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        raise Blocked("REDIRECT_FORBIDDEN")


class PublicReader:
    """No credentials, environment token lookup, proxy or signed-URL handling."""
    def __init__(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # A fixed Linux trust store avoids SSL_CERT_FILE/SSL_CERT_DIR and keylog
        # environment overrides. Failure is terminal, never a TLS downgrade.
        context.load_verify_locations(cafile="/etc/ssl/certs/ca-certificates.crt")
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
        self.started = time.monotonic()
        self.requests = 0

    def __call__(self, path: str) -> Response:
        p.demand(type(path) is str and _allowed(path), "GET_PATH_NOT_ALLOWED")
        p.demand(self.requests < 256 and 0 <= time.monotonic() - self.started < 120,
                 "PUBLIC_READ_BUDGET_EXHAUSTED")
        self.requests += 1
        request = Request(ROOT + path, method="GET", headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "OfferPilot-read-only-evidence"})
        try:
            with self.opener.open(request, timeout=15) as response:
                p.demand(response.geturl() == ROOT + path, "RESPONSE_URL_CHANGED")
                raw = response.read(MAX_RESPONSE + 1)
                p.demand(0 <= time.monotonic() - self.started < 120, "PUBLIC_READ_BUDGET_EXHAUSTED")
                return Response(raw, response.status, {str(k).lower(): str(v)
                                                       for k, v in response.headers.items()})
        except Blocked:
            raise
        except (HTTPError, URLError, OSError, TimeoutError):
            # Do not emit exception text, headers, redirect locations or URLs.
            raise Blocked("PUBLIC_GET_FAILED") from None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        p.demand(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    p.demand(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE, "RESPONSE_SIZE_INVALID")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Blocked("JSON_NONFINITE")))
    except (ValueError, UnicodeError, RecursionError):
        raise Blocked("JSON_INVALID") from None


def _read(reader: Reader, path: str) -> tuple[dict[str, Any], Mapping[str, str]]:
    p.demand(_allowed(path), "GET_PATH_NOT_ALLOWED")
    response = reader(path)
    p.demand(isinstance(response, Response) and type(response.status) is int
             and response.status == 200, "GET_STATUS_INVALID")
    headers = {key.lower(): value for key, value in response.headers.items()}
    p.demand(not any("\r" in str(k) + str(v) or "\n" in str(k) + str(v)
                     for k, v in headers.items()), "HEADER_INVALID")
    p.demand("location" not in headers and "retry-after" not in headers,
             "REDIRECT_OR_RATE_LIMIT")
    p.demand(headers.get("x-ratelimit-remaining") != "0", "RATE_LIMIT_EXHAUSTED")
    data = _json(response.body)
    p.demand(type(data) is dict, "OBJECT_REQUIRED")
    return data, headers


def _next(link: str, path: str, page: int) -> bool:
    if not link:
        return False
    relations: set[str] = set()
    for part in link.split(","):
        match = re.fullmatch(r'\s*<([^>]+)>; rel="(next|prev|first|last)"\s*', part)
        p.demand(match is not None, "PAGINATION_LINK_INVALID")
        assert match is not None
        url, relation = match.groups()
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, strict_parsing=True)
        p.demand(parsed.scheme == "https" and parsed.netloc == "api.github.com"
                 and parsed.path == "/repos/offercontext/offerPilot" + path
                 and not parsed.fragment and set(query) == {"per_page", "page"}
                 and query["per_page"] == ["100"] and len(query["page"]) == 1
                 and bool(re.fullmatch(r"[1-9][0-9]*", query["page"][0]))
                 and relation not in relations, "PAGINATION_LINK_INVALID")
        relations.add(relation)
        if relation == "next":
            p.demand(int(query["page"][0]) == page + 1, "PAGINATION_REPLAY")
    return "next" in relations


def _pages(reader: Reader, path: str, key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        data, headers = _read(reader, f"{path}?per_page=100&page={page}")
        count, rows = data.get("total_count"), data.get(key)
        p.demand(type(count) is int and 0 <= count <= MAX_PAGES * 100
                 and (total is None or total == count) and type(rows) is list and len(rows) <= 100,
                 "PAGINATION_COUNT_INVALID")
        assert isinstance(count, int) and isinstance(rows, list)
        total = count
        for row in rows:
            p.demand(type(row) is dict and type(row.get("id")) is int and row["id"] > 0
                     and row["id"] not in seen, "PAGINATION_DUPLICATE")
            seen.add(row["id"])
            result.append(row)
        more = _next(headers.get("link", ""), path, page)
        if not more:
            p.demand(len(result) == total, "PAGINATION_INCOMPLETE")
            return result
        p.demand(len(rows) == 100 and len(result) < total, "PAGINATION_INCOMPLETE")
    raise Blocked("PAGINATION_LIMIT")


def _identity(run: Mapping[str, Any], run_id: int, source: str) -> None:
    expected = {"id": run_id, "workflow_id": WORKFLOW, "head_sha": source,
                "run_attempt": 1, "head_branch": BRANCH, "event": "push",
                "status": "completed", "conclusion": "success"}
    p.demand(all(type(run.get(key)) is type(value) and run[key] == value
                 for key, value in expected.items()), "RUN_IDENTITY_INVALID")
    repository = run.get("repository")
    p.demand(type(repository) is dict and type(repository.get("id")) is int
             and repository["id"] == p.REPOSITORY_ID
             and repository.get("full_name") == "offercontext/offerPilot", "RUN_REPOSITORY_INVALID")


def _jobs(reader: Reader, run_id: int, source: str, *, paid: bool) -> None:
    rows = _pages(reader, f"/actions/runs/{run_id}/jobs", "jobs")
    by_name = {row.get("name"): row for row in rows}
    p.demand(len(by_name) == len(rows) == 3 and set(by_name) == old_gate.JOBS,
             "JOB_SET_INVALID")
    for name, job in by_name.items():
        expected = "success" if name == old_gate.OFFLINE_JOB or paid else "skipped"
        p.demand(type(job.get("run_id")) is int and job["run_id"] == run_id and job.get("head_sha") == source
                 and job.get("status") == "completed" and job.get("conclusion") == expected,
                 "JOB_STATE_INVALID")


def _verify_aggregate(raw: bytes) -> None:
    p.demand(type(raw) is bytes and len(raw) == ZIP_SIZE
             and hashlib.sha256(raw).hexdigest() == ZIP_SHA256, "H_ARTIFACT_BYTES_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            p.demand(len(members) == 1 and members[0].filename == "companion-ai-offline.json",
                     "H_ARTIFACT_MEMBERS_INVALID")
            info = members[0]
            p.demand(not info.is_dir() and not info.flag_bits & 1
                     and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
                     and 0 < info.file_size <= MAX_RESPONSE, "H_ARTIFACT_MEMBER_INVALID")
            data = archive.read(info)
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile):
        raise Blocked("H_ARTIFACT_ZIP_INVALID") from None
    p.demand(hashlib.sha256(data).hexdigest() == AGGREGATE_SHA256, "H_AGGREGATE_DIGEST_INVALID")
    aggregate = _json(data)
    p.demand(type(aggregate) is dict and set(aggregate) == {
        "passed", "exit_code", "collected", "finished", "deselected"}
        and aggregate["passed"] is True and type(aggregate["exit_code"]) is int
        and aggregate["exit_code"] == 0 and aggregate["deselected"] == [], "H_AGGREGATE_INVALID")
    nodes, finished = aggregate["collected"], aggregate["finished"]
    p.demand(type(nodes) is list and len(nodes) == NODES and all(type(node) is str for node in nodes)
             and len(set(nodes)) == NODES and type(finished) is dict and set(finished) == set(nodes)
             and all(phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
                     for phases in finished.values()), "H_AGGREGATE_INCOMPLETE")


def scan_legacy_history(reader: Reader) -> dict[str, Any]:
    """Read old workflow runs; this is not environment/owner/new-entry admission."""
    rows = _pages(reader, f"/actions/workflows/{WORKFLOW}/runs", "workflow_runs")
    numbers = [row.get("run_number") for row in rows]
    p.demand(all(type(number) is int for number in numbers)
             and sorted(cast(list[int], numbers)) == list(range(1, len(rows) + 1)), "LEGACY_RUN_HISTORY_GAP")
    sources = {batch.run_id: batch.source_sha for batch in LEGACY_BATCHES}
    helpers = {batch.helper_run_id: batch.helper_sha for batch in LEGACY_BATCHES}
    helpers[H_RUN] = p.SOURCE_SHA
    found: set[int] = set()
    order: dict[int, int] = {}
    for row in rows:
        run_id = row["id"]
        p.demand(type(row.get("workflow_id")) is int and row["workflow_id"] == WORKFLOW
                 and type(row.get("run_attempt")) is int and row["run_attempt"] == 1
                 and row.get("status") == "completed", "LEGACY_RUN_UNRESOLVED")
        repository = row.get("repository")
        p.demand(type(repository) is dict and type(repository.get("id")) is int
                 and repository["id"] == p.REPOSITORY_ID
                 and repository.get("full_name") == "offercontext/offerPilot"
                 and row.get("head_branch") == BRANCH and row.get("event") == "push"
                 and type(row.get("head_sha")) is str
                 and re.fullmatch(r"[0-9a-f]{40}", row["head_sha"]) is not None,
                 "LEGACY_RUN_IDENTITY_INVALID")
        order[run_id] = row["run_number"]
        if run_id in sources:
            _identity(row, run_id, sources[run_id])
            _jobs(reader, run_id, sources[run_id], paid=True)
            found.add(run_id)
        elif run_id in helpers:
            _identity(row, run_id, helpers[run_id])
            _jobs(reader, run_id, helpers[run_id], paid=False)
            found.add(run_id)
        else:
            jobs = _pages(reader, f"/actions/runs/{run_id}/jobs", "jobs")
            p.demand(len(jobs) == 3 and {job.get("name") for job in jobs} == old_gate.JOBS
                     and all(type(job.get("run_id")) is int and job["run_id"] == run_id
                             and job.get("head_sha") == row.get("head_sha")
                             and job.get("status") == "completed" for job in jobs), "LEGACY_JOBS_INVALID")
            paid_jobs = [job for job in jobs if job.get("name") == old_gate.PAID_JOB]
            p.demand(len(paid_jobs) == 1 and paid_jobs[0].get("status") == "completed"
                     and paid_jobs[0].get("conclusion") == "skipped", "UNKNOWN_PAID_OR_UNRESOLVED")
    p.demand(found == set(sources) | set(helpers), "LEGACY_RUNS_MISSING")
    chain = [identity for batch in LEGACY_BATCHES for identity in (batch.helper_run_id, batch.run_id)]
    chain.append(H_RUN)
    p.demand(all(order[left] < order[right] for left, right in zip(chain, chain[1:], strict=False)),
             "LEGACY_ANCHOR_ORDER_INVALID")
    for batch in LEGACY_BATCHES:
        artifact, _ = _read(reader, f"/actions/artifacts/{batch.artifact_id}")
        owner = artifact.get("workflow_run")
        p.demand(type(artifact.get("id")) is int and artifact["id"] == batch.artifact_id
                 and artifact.get("name") == f"companion-ai-{batch.source_sha}"
                 and type(artifact.get("size_in_bytes")) is int
                 and artifact["size_in_bytes"] == batch.archive_size
                 and artifact.get("digest") == "sha256:" + batch.archive_sha256
                 and artifact.get("expired") is False
                 and artifact.get("url") == ROOT + f"/actions/artifacts/{batch.artifact_id}"
                 and type(owner) is dict and type(owner.get("id")) is int
                 and owner["id"] == batch.run_id
                 and type(owner.get("repository_id")) is int and owner["repository_id"] == p.REPOSITORY_ID
                 and type(owner.get("head_repository_id")) is int
                 and owner["head_repository_id"] == p.REPOSITORY_ID
                 and owner.get("head_branch") == BRANCH and owner.get("head_sha") == batch.source_sha,
                 "LEGACY_ARTIFACT_METADATA_INVALID")
    balance = history.verified_archives()
    return {"scope": "old_workflow_only", "runs_checked": len(rows),
            "prior_calls": balance.calls, "settled_micro_cny": balance.settled_micro_cny,
            "new_workflows_scanned": False, "owner_approvals_verified": False}


def _collect(reader: Reader, artifact_bytes: bytes | None, scan_history: bool) -> dict[str, Any]:
    repo, _ = _read(reader, "")
    p.demand(type(repo.get("id")) is int and repo["id"] == p.REPOSITORY_ID
             and repo.get("full_name") == "offercontext/offerPilot" and repo.get("private") is False,
             "REPOSITORY_IDENTITY_INVALID")
    commit, _ = _read(reader, f"/git/commits/{p.SOURCE_SHA}")
    p.demand(commit.get("sha") == p.SOURCE_SHA and type(commit.get("tree")) is dict
             and commit["tree"].get("sha") == p.SOURCE_TREE, "SOURCE_COMMIT_INVALID")
    run, _ = _read(reader, f"/actions/runs/{H_RUN}")
    _identity(run, H_RUN, p.SOURCE_SHA)
    _jobs(reader, H_RUN, p.SOURCE_SHA, paid=False)
    artifact, _ = _read(reader, f"/actions/artifacts/{ARTIFACT}")
    owner = artifact.get("workflow_run")
    p.demand(type(artifact.get("id")) is int and artifact["id"] == ARTIFACT
             and artifact.get("name") == f"companion-ai-offline-{p.SOURCE_SHA}"
             and type(artifact.get("size_in_bytes")) is int and artifact["size_in_bytes"] == ZIP_SIZE
             and artifact.get("expired") is False and type(owner) is dict
             and type(owner.get("id")) is int and owner["id"] == H_RUN
             and owner.get("head_sha") == p.SOURCE_SHA
             and owner.get("head_branch") == BRANCH, "ARTIFACT_IDENTITY_INVALID")
    if artifact.get("digest") is not None:
        p.demand(artifact["digest"] == "sha256:" + ZIP_SHA256, "ARTIFACT_METADATA_DIGEST_INVALID")
    if artifact_bytes is not None:
        _verify_aggregate(artifact_bytes)
    lineage = scan_legacy_history(reader) if scan_history else {"scope": "not_scanned"}
    return {"mode": "read_only_evidence", "live_allowed": False, "production_admission": False,
        "source_sha": p.SOURCE_SHA, "source_tree": p.SOURCE_TREE, "ci_run_id": H_RUN,
        "ci_attempt": 1, "artifact_id": ARTIFACT, "artifact_sha256": ZIP_SHA256,
        "artifact_bytes_verified": artifact_bytes is not None,
        "artifact_download_unverified": artifact_bytes is None,
        "source_evidence_verified": artifact_bytes is not None,
        "legacy_history_scanned": scan_history, "all_entrypoints_history_scanned": False,
        "aggregate_node_count": NODES if artifact_bytes is not None else None,
        "lineage": lineage, "environment_verified": False, "owner_approval_verified": False,
        "new_controller_ci_verified": False}


def collect_source(reader: Reader | None = None, *, artifact_bytes: bytes | None = None,
                   scan_history: bool = False) -> dict[str, Any]:
    """Injected readers are tests/connectors, not permission to produce admission."""
    try:
        return _collect(PublicReader() if reader is None else reader, artifact_bytes, scan_history)
    except Blocked:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError, OSError):
        raise Blocked("READ_ONLY_EVIDENCE_INVALID") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-artifact", type=Path)
    parser.add_argument("--scan-legacy-history", action="store_true")
    args = parser.parse_args()
    try:
        raw = None
        if args.offline_artifact is not None:
            path = args.offline_artifact
            p.demand(path.is_file() and not path.is_symlink(), "LOCAL_ARTIFACT_INVALID")
            with path.open("rb") as stream:
                raw = stream.read(ZIP_SIZE + 1)
        result = collect_source(artifact_bytes=raw, scan_history=args.scan_legacy_history)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["source_evidence_verified"] else 2
    except Blocked as exc:
        print(json.dumps({"mode": "read_only_evidence", "live_allowed": False,
                          "source_evidence_verified": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
