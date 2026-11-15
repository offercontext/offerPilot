"""Read-only, fail-closed approval gate for the new one-shot simplification run.

Only GitHub runner metadata, the push event and explicitly injected Actions vars
are inputs. No product settings, local credentials, historical desktop helpers,
secret endpoints, write APIs or provider SDKs are used. Importing is offline.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import ssl
from typing import Any, Callable, Mapping, NoReturn, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from scripts.simplification_ai_validation.broker import (
    SESSION_SECONDS, GateError, PriceWindow, boot_identity, require_linux,
)

REPOSITORY = "offercontext/offerPilot"
BRANCH = "feat/20261021-product-simplification"
WORKFLOW = ".github/workflows/simplification-real-ai.yml"
# Registered by the first offline-only publication on 2026-10-21.
# A deleted/recreated workflow must not silently reset this one-shot budget.
WORKFLOW_ID = 391853346
ENVIRONMENT = "offerpilot-simplification-ai-20261021"
ENVIRONMENT_ID = 24929248876
BRANCH_POLICY_ID = 63850625
REVIEWER_LOGIN = "XiaoZheBrother"
REVIEWER_ID = 112178718
OFFLINE_JOB = "Simplification offline safety"
PREFLIGHT_JOB = "Simplification request preflight"
PAID_JOB = "Simplification approved real AI"
JOBS = frozenset((OFFLINE_JOB, PREFLIGHT_JOB, PAID_JOB))
HELPER_VAR = "OFFERPILOT_SIMPLIFICATION_AI_HELPER_SHA"
REQUEST_VAR = "OFFERPILOT_SIMPLIFICATION_AI_REQUEST_SHA"
API = f"https://api.github.com/repos/{REPOSITORY}/"
SHA = re.compile(r"[0-9a-f]{40}\Z")
PAGE_SIZE = 100


class Blocked(Exception):
    """Only constant, non-sensitive error codes cross the CLI boundary."""


def demand(condition: bool, code: str) -> None:
    if not condition:
        raise Blocked(code)


def check_price_window(deadline: PriceWindow | None = None) -> PriceWindow:
    """Keep the same quote clock through every preflight read and its handoff."""
    require_linux()
    try:
        window = deadline if deadline is not None else PriceWindow.capture()
        window.check(SESSION_SECONDS)
        return window
    except GateError:
        raise Blocked("PRICE_REVIEW_EXPIRED") from None


def positive(value: Any) -> bool:
    return type(value) is int and value > 0


def obj(value: Any) -> dict[str, Any]:
    demand(isinstance(value, dict), "INVALID_METADATA")
    return cast(dict[str, Any], value)


def marker(helper_sha: str) -> str:
    return (
        "test: request OfferPilot simplification real AI validation\n\n"
        f"OfferPilot-Simplification-AI-Helper: {helper_sha}\n"
        "OfferPilot-Simplification-AI-Request: one-shot-20261021"
    )


def exact_marker(value: Any, helper_sha: str) -> bool:
    # Git commit messages can include their terminal LF, not arbitrary whitespace.
    return value in (marker(helper_sha), marker(helper_sha) + "\n")


@dataclass(frozen=True)
class Context:
    helper_sha: str
    request_sha: str
    run_id: int
    repository_id: int


def validate_context(env: Mapping[str, str]) -> Context:
    demand(env.get("GITHUB_RUN_ATTEMPT") == "1", "RERUN_FORBIDDEN")
    helper, request = env.get(HELPER_VAR, ""), env.get(REQUEST_VAR, "")
    demand(bool(SHA.fullmatch(helper) and SHA.fullmatch(request)) and helper != request,
           "EXACT_SHA_APPROVAL_MISSING")
    demand(
        env.get("GITHUB_EVENT_NAME") == "push"
        and env.get("GITHUB_REPOSITORY") == REPOSITORY
        and env.get("GITHUB_REF") == f"refs/heads/{BRANCH}"
        and env.get("GITHUB_SHA") == request
        and env.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/{WORKFLOW}@refs/heads/{BRANCH}"
        and env.get("GITHUB_WORKFLOW_SHA") == request,
        "TRIGGER_NOT_AUTHORIZED",
    )
    run_id, repository_id = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_REPOSITORY_ID", "")
    demand(bool(re.fullmatch(r"[1-9][0-9]*", run_id))
           and bool(re.fullmatch(r"[1-9][0-9]*", repository_id)), "INVALID_RUN_IDENTITY")
    return Context(helper, request, int(run_id), int(repository_id))


def trusted_repo(value: Any, repository_id: int) -> bool:
    return (isinstance(value, dict) and value.get("full_name") == REPOSITORY
            and value.get("id") == repository_id and type(value.get("id")) is int)


def workflow_path(value: Any) -> bool:
    return value in (WORKFLOW, f"{WORKFLOW}@{BRANCH}", f"{WORKFLOW}@refs/heads/{BRANCH}")


def validate_identity(ctx: Context, event: Any, request: Any, helper: Any, branch: Any) -> None:
    event, request, helper, branch = map(obj, (event, request, helper, branch))
    demand(
        event.get("ref") == f"refs/heads/{BRANCH}"
        and event.get("before") == ctx.helper_sha and event.get("after") == ctx.request_sha
        and event.get("forced") is False and event.get("deleted") is False
        and event.get("created") is False and trusted_repo(event.get("repository"), ctx.repository_id)
        and obj(event.get("head_commit")).get("id") == ctx.request_sha
        and exact_marker(event["head_commit"].get("message"), ctx.helper_sha),
        "PUSH_IDENTITY_MISMATCH",
    )
    parents = request.get("parents")
    tree = obj(request.get("tree")).get("sha")
    demand(
        request.get("sha") == ctx.request_sha and helper.get("sha") == ctx.helper_sha
        and branch.get("ref") == f"refs/heads/{BRANCH}"
        and obj(branch.get("object")).get("type") == "commit"
        and branch["object"].get("sha") == ctx.request_sha
        and isinstance(parents, list) and len(parents) == 1
        and obj(parents[0]).get("sha") == ctx.helper_sha
        and isinstance(tree, str) and bool(SHA.fullmatch(tree))
        and tree == obj(helper.get("tree")).get("sha")
        and exact_marker(request.get("message"), ctx.helper_sha),
        "REQUEST_MUST_BE_EMPTY_CHILD",
    )


def validate_environment(environment: Any, policies: list[dict[str, Any]]) -> int:
    environment = obj(environment)
    demand(environment.get("name") == ENVIRONMENT and environment.get("id") == ENVIRONMENT_ID,
           "PROTECTED_ENVIRONMENT_MISSING")
    demand(environment.get("can_admins_bypass") is False, "ENVIRONMENT_BYPASS_NOT_DISABLED")
    rules = environment.get("protection_rules")
    demand(isinstance(rules, list), "REQUIRED_REVIEWER_MISSING")
    reviewers = [obj(rule) for rule in rules if obj(rule).get("type") == "required_reviewers"]
    demand(len(reviewers) == 1, "REQUIRED_REVIEWER_MISSING")
    configured = reviewers[0].get("reviewers")
    demand(isinstance(configured, list) and len(configured) == 1, "EXACT_OWNER_REVIEWER_REQUIRED")
    assert isinstance(configured, list)
    reviewer = obj(configured[0])
    person = obj(reviewer.get("reviewer"))
    demand(reviewer.get("type") == "User" and person.get("type") == "User"
           and person.get("login") == REVIEWER_LOGIN and person.get("id") == REVIEWER_ID,
           "EXACT_OWNER_REVIEWER_REQUIRED")
    # A single-owner run must remain approvable by that owner. Approval itself is
    # verified independently in the paid phase; bypass is never an approval.
    demand(reviewers[0].get("prevent_self_review") is False, "OWNER_REVIEW_CONFIGURATION_INVALID")
    policy = obj(environment.get("deployment_branch_policy"))
    demand(policy.get("protected_branches") is False and policy.get("custom_branch_policies") is True
           and len(policies) == 1 and obj(policies[0]).get("id") == BRANCH_POLICY_ID
           and policies[0].get("name") == BRANCH
           and policies[0].get("type") == "branch", "EXACT_BRANCH_POLICY_REQUIRED")
    return ENVIRONMENT_ID


def validate_review(reviews: Any, environment_id: int) -> None:
    demand(isinstance(reviews, list) and len(reviews) == 1, "OWNER_APPROVAL_UNVERIFIED")
    review = obj(reviews[0])
    environments = review.get("environments")
    user = obj(review.get("user"))
    demand(
        review.get("state") == "approved" and user.get("type") == "User"
        and user.get("id") == REVIEWER_ID and user.get("login") == REVIEWER_LOGIN
        and isinstance(environments, list) and len(environments) == 1
        and obj(environments[0]).get("id") == environment_id
        and environments[0].get("name") == ENVIRONMENT,
        "OWNER_APPROVAL_UNVERIFIED",
    )


def validate_run(ctx: Context, run: dict[str, Any], workflow_id: int) -> None:
    demand(positive(run.get("id")) and positive(run.get("run_number")), "INVALID_RUN_IDENTITY")
    demand(type(run.get("run_attempt")) is int and run["run_attempt"] == 1, "RERUN_FORBIDDEN")
    demand(run.get("workflow_id") == workflow_id and workflow_path(run.get("path"))
           and run.get("event") == "push" and run.get("head_branch") == BRANCH
           and trusted_repo(run.get("repository"), ctx.repository_id)
           and trusted_repo(run.get("head_repository"), ctx.repository_id)
           and isinstance(run.get("head_sha"), str) and bool(SHA.fullmatch(run["head_sha"])),
           "RUN_PROVENANCE_MISMATCH")


def validate_jobs(run: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids, names = set(), set()
    for job in jobs:
        job = obj(job)
        demand(positive(job.get("id")) and job["id"] not in ids
               and job.get("name") in JOBS and job["name"] not in names
               and job.get("run_id") == run["id"] and job.get("head_sha") == run["head_sha"],
               "JOB_HISTORY_INVALID")
        # Some REST API versions omit run_attempt from jobs; the run is checked
        # above and filter=all exposes duplicate jobs from all attempts.
        demand(job.get("run_attempt", 1) == 1, "RERUN_FORBIDDEN")
        ids.add(job["id"])
        names.add(job["name"])
    return {job["name"]: job for job in jobs}


def completed(job: Mapping[str, Any], conclusion: str) -> bool:
    return job.get("status") == "completed" and job.get("conclusion") == conclusion


def validate_history(ctx: Context, runs: list[dict[str, Any]],
                     jobs_by_run: Mapping[int, list[dict[str, Any]]],
                     current: Any, workflow: Any, phase: str) -> None:
    current, workflow = obj(current), obj(workflow)
    demand(phase in ("before", "paid"), "INVALID_PHASE")
    demand(positive(WORKFLOW_ID) and workflow.get("id") == WORKFLOW_ID
           and workflow.get("path") == WORKFLOW
           and workflow.get("state") == "active", "WORKFLOW_IDENTITY_MISMATCH")
    demand(bool(runs) and len({obj(run).get("id") for run in runs}) == len(runs), "RUN_HISTORY_INCOMPLETE")
    for run in runs + [current]:
        validate_run(ctx, obj(run), workflow["id"])
    numbers = sorted(run["run_number"] for run in runs)
    demand(numbers == list(range(1, len(runs) + 1)), "RUN_HISTORY_GAP")
    selected = [run for run in runs if run["id"] == ctx.run_id]
    demand(len(selected) == 1 and current.get("id") == ctx.run_id
           and current.get("head_sha") == ctx.request_sha
           and current.get("status") == "in_progress" and current.get("conclusion") is None
           and selected[0]["head_sha"] == ctx.request_sha
           and selected[0].get("status") == "in_progress"
           and selected[0].get("conclusion") is None
           and selected[0]["run_number"] == current["run_number"] == numbers[-1],
           "CURRENT_RUN_UNVERIFIED")
    demand(set(jobs_by_run) == {run["id"] for run in runs}, "JOB_HISTORY_INCOMPLETE")
    helper_runs = []
    for run in runs:
        jobs = validate_jobs(run, jobs_by_run[run["id"]])
        if run["id"] == ctx.run_id:
            expected = JOBS if phase == "paid" else {OFFLINE_JOB, PREFLIGHT_JOB}
            demand(expected.issubset(jobs) and set(jobs).issubset(JOBS), "CURRENT_JOBS_INVALID")
            demand(completed(jobs[OFFLINE_JOB], "success"), "CURRENT_OFFLINE_REQUIRED")
            if phase == "paid":
                demand(completed(jobs[PREFLIGHT_JOB], "success")
                       and jobs[PAID_JOB].get("status") == "in_progress"
                       and jobs[PAID_JOB].get("conclusion") is None, "CURRENT_JOBS_INVALID")
            else:
                demand(jobs[PREFLIGHT_JOB].get("status") == "in_progress"
                       and jobs[PREFLIGHT_JOB].get("conclusion") is None, "CURRENT_JOBS_INVALID")
                if PAID_JOB in jobs:
                    demand(jobs[PAID_JOB].get("status") in ("queued", "waiting", "pending")
                           and jobs[PAID_JOB].get("conclusion") is None, "CURRENT_JOBS_INVALID")
            continue
        demand(run.get("status") == "completed", "PRIOR_RUN_UNRESOLVED")
        demand(set(jobs) == JOBS and all(job.get("status") == "completed" for job in jobs.values()),
               "JOB_HISTORY_INCOMPLETE")
        demand(completed(jobs[PAID_JOB], "skipped"), "PAID_BUDGET_ALREADY_CONSUMED")
        if run["head_sha"] == ctx.helper_sha:
            demand(run.get("conclusion") == "success" and completed(jobs[OFFLINE_JOB], "success"),
                   "HELPER_OFFLINE_SUCCESS_REQUIRED")
            helper_runs.append(run)
    demand(len(helper_runs) == 1, "HELPER_OFFLINE_SUCCESS_REQUIRED")


@dataclass(frozen=True)
class Response:
    data: Any
    link: str = ""


Reader = Callable[[str], Response]


def page_path(path: str, page: int) -> str:
    return f"{path}{'&' if '?' in path else '?'}per_page={PAGE_SIZE}&page={page}"


def next_page(link: str, path: str, page: int) -> bool:
    """Validate link targets but never follow arbitrary server-supplied URLs."""
    if not link:
        return False
    found = False
    expected = urlsplit(API + page_path(path, page + 1))
    for part in link.split(","):
        match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
        demand(match is not None, "PAGINATION_INVALID")
        assert match is not None
        target, relation = match.groups()
        parsed = urlsplit(target)
        query = parse_qs(parsed.query, keep_blank_values=True)
        expected_query = parse_qs(expected.query)
        demand(parsed.scheme == expected.scheme and parsed.netloc == expected.netloc
               and parsed.path == expected.path and not parsed.fragment
               and set(query) == set(expected_query)
               and all(query[key] == value for key, value in expected_query.items() if key != "page")
               and len(query.get("page", [])) == 1
               and bool(re.fullmatch(r"[1-9][0-9]*", query["page"][0])), "PAGINATION_INVALID")
        if relation == "next":
            demand(not found and query == expected_query, "PAGINATION_INVALID")
            found = True
    return found


def read_pages(read: Reader, path: str, key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    total, page = None, 1
    seen: set[int] = set()
    while True:
        response = read(page_path(path, page))
        data = obj(response.data)
        count, items = data.get("total_count"), data.get(key)
        demand(type(count) is int and count >= 0 and isinstance(items, list)
               and len(items) <= PAGE_SIZE, "HISTORY_INCOMPLETE")
        assert isinstance(count, int) and isinstance(items, list)
        demand(total is None or count == total, "HISTORY_CHANGED")
        total = count
        for item in items:
            item = obj(item)
            demand(positive(item.get("id")) and item["id"] not in seen, "HISTORY_DUPLICATE")
            seen.add(item["id"])
            result.append(item)
        demand(len(result) <= total, "HISTORY_INCOMPLETE")
        more = next_page(response.link, path, page)
        if len(result) == total:
            demand(not more, "HISTORY_INCOMPLETE")
            return result
        demand(more and len(items) == PAGE_SIZE, "HISTORY_INCOMPLETE")
        page += 1


def single(read: Reader, path: str) -> Any:
    response = read(path)
    demand(not response.link, "HISTORY_INCOMPLETE")
    return response.data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any,
                         headers: Any, newurl: Any) -> None:
        raise Blocked("GITHUB_REDIRECT_DENIED")


class GitHubReader:
    """Fixed repository GET allowlist, verified TLS, no redirect or proxy reuse."""

    def __init__(self, token: str):
        demand(isinstance(token, str) and bool(token) and "\n" not in token and "\r" not in token,
               "GITHUB_READ_TOKEN_MISSING")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, path: str) -> Response:
        allowed = (
            r"git/commits/[0-9a-f]{40}",
            re.escape(f"git/ref/heads/{BRANCH}"),
            re.escape(f"environments/{ENVIRONMENT}"),
            re.escape(f"environments/{ENVIRONMENT}/deployment-branch-policies")
            + r"\?per_page=100&page=[1-9][0-9]*",
            re.escape(f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}"),
            re.escape(f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}/runs")
            + r"\?per_page=100&page=[1-9][0-9]*",
            r"actions/runs/[1-9][0-9]*(?:/approvals)?",
            r"actions/runs/[1-9][0-9]*/jobs\?filter=all&per_page=100&page=[1-9][0-9]*",
        )
        demand(any(re.fullmatch(pattern, path) for pattern in allowed), "GITHUB_READ_PATH_DENIED")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "offerpilot-simplification-preflight"}
        # Public environment metadata needs no elevated repository administration
        # permission. No authentication fallback or secret metadata endpoint exists.
        if not path.startswith("environments/"):
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            request = Request(API + path, headers=headers, method="GET")
            with self.opener.open(request, timeout=20) as response:
                demand(response.status == 200, "GITHUB_READ_UNAVAILABLE")
                body = response.read(8 * 1024 * 1024 + 1)
                demand(len(body) <= 8 * 1024 * 1024, "GITHUB_READ_TOO_LARGE")
                return Response(json.loads(body), response.headers.get("Link", ""))
        except Blocked:
            raise
        except Exception:
            raise Blocked("GITHUB_READ_UNAVAILABLE") from None


def preflight(*, phase: str, env: Mapping[str, str], event: Any, read: Reader,
              price_deadline: PriceWindow | None = None) -> dict[str, Any]:
    require_linux()
    ctx = validate_context(env)
    demand(phase in ("before", "paid"), "INVALID_PHASE")
    price_deadline = check_price_window(price_deadline)
    workflow_path = f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}"
    current = single(read, f"actions/runs/{ctx.run_id}")
    workflow = single(read, workflow_path)
    validate_identity(ctx, event, single(read, f"git/commits/{ctx.request_sha}"),
                      single(read, f"git/commits/{ctx.helper_sha}"),
                      single(read, f"git/ref/heads/{BRANCH}"))
    environment_id = validate_environment(
        single(read, f"environments/{ENVIRONMENT}"),
        read_pages(read, f"environments/{ENVIRONMENT}/deployment-branch-policies", "branch_policies"),
    )
    runs = read_pages(read, f"{workflow_path}/runs", "workflow_runs")
    jobs = {run["id"]: read_pages(read, f"actions/runs/{run['id']}/jobs?filter=all", "jobs")
            for run in runs}
    validate_history(ctx, runs, jobs, current, workflow, phase)
    if phase == "paid":
        validate_review(single(read, f"actions/runs/{ctx.run_id}/approvals"), environment_id)
    # GitHub pagination and owner-review reads may consume the remaining quote.
    check_price_window(price_deadline)
    return {"ok": True, "helper_sha": ctx.helper_sha, "request_sha": ctx.request_sha}


def write_admission(path: str, ctx: Context, price_deadline: PriceWindow | None = None) -> None:
    """Only a successful preflight makes this local handoff usable, never its existence."""
    price_deadline = check_price_window(price_deadline)
    boot = boot_identity()
    checked_at, checked_monotonic = price_deadline.check(SESSION_SECONDS)
    record = {"schema": 2, "source_sha": ctx.request_sha, "helper_sha": ctx.helper_sha,
              "run_id": str(ctx.run_id), "checked_at": checked_at,
              "checked_monotonic": checked_monotonic, "boot_id": boot,
              "price_window": price_deadline.record()}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        raise Blocked("ADMISSION_WRITE_BLOCKED") from None
    # The exclusive file may remain after failure; the workflow must not consume it.
    check_price_window(price_deadline)


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Blocked("CLI_INVALID")


def main(argv: list[str] | None = None) -> int:
    try:
        require_linux()
        parser = SafeParser(add_help=False)
        parser.add_argument("--phase", choices=("before", "paid"), required=True)
        parser.add_argument("--helper-sha")
        parser.add_argument("--request-sha")
        parser.add_argument("--admission-file")
        args = parser.parse_args(argv)
        demand(not args.admission_file or args.phase == "paid", "ADMISSION_PHASE_INVALID")
        # Deliberately select metadata, rather than snapshotting the environment.
        keys = (HELPER_VAR, REQUEST_VAR, "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME",
                "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_SHA", "GITHUB_WORKFLOW_REF",
                "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_ID", "GITHUB_REPOSITORY_ID")
        env = {key: os.environ.get(key, "") for key in keys}
        if args.helper_sha is not None:
            env[HELPER_VAR] = args.helper_sha
        if args.request_sha is not None:
            env[REQUEST_VAR] = args.request_sha
        ctx = validate_context(env)
        # Refuse before reading GH_TOKEN or constructing even the read-only client.
        price_deadline = check_price_window()
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        check_price_window(price_deadline)
        result = preflight(phase=args.phase, env=env, event=event,
                           read=GitHubReader(os.environ.get("GH_TOKEN", "")),
                           price_deadline=price_deadline)
        if args.admission_file:
            write_admission(args.admission_file, ctx, price_deadline)
        check_price_window(price_deadline)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, (Blocked, GateError)) else "VALIDATION_BLOCKED"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
