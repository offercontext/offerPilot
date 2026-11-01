"""Read-only, fail-closed gate for the pinned third cumulative correction.

Only GitHub runner metadata, the push event and explicitly injected Actions vars
are inputs. No product settings, local credentials, historical desktop helpers,
secret endpoints, write APIs or provider SDKs are used. Importing is offline.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any, Callable, Mapping, NoReturn, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from scripts.companion_ai_validation.broker import PRICE_VALID_UNTIL, SESSION_SECONDS
from scripts.companion_ai_validation import continuation as lineage

REPOSITORY = "offercontext/offerPilot"
BRANCH = "feat/20261021-companion-life-v1"
WORKFLOW = ".github/workflows/companion-real-ai.yml"
# Independently verified by the unarmed registration run 39632913549.
# Recreated workflow/environment/policy identities never reset this budget.
# Exact helper/request SHA binding and owner approval remain mandatory.
WORKFLOW_ID = 391917473
ENVIRONMENT = "offerpilot-companion-ai-20261021"
ENVIRONMENT_ID = 24931188785
BRANCH_POLICY_ID = 63853345
REVIEWER_LOGIN = "XiaoZheBrother"
REVIEWER_ID = 112178718
OFFLINE_JOB = "Companion offline safety"
PREFLIGHT_JOB = "Companion request preflight"
PAID_JOB = "Companion approved real AI"
JOBS = frozenset((OFFLINE_JOB, PREFLIGHT_JOB, PAID_JOB))
HELPER_VAR = "OFFERPILOT_COMPANION_AI_HELPER_SHA"
REQUEST_VAR = "OFFERPILOT_COMPANION_AI_REQUEST_SHA"
API = f"https://api.github.com/repos/{REPOSITORY}/"
SHA = re.compile(r"[0-9a-f]{40}\Z")
PAGE_SIZE = 100


class Blocked(Exception):
    """Only constant, non-sensitive error codes cross the CLI boundary."""


def demand(condition: bool, code: str) -> None:
    if not condition:
        raise Blocked(code)


def positive(value: Any) -> bool:
    return type(value) is int and value > 0


def obj(value: Any) -> dict[str, Any]:
    demand(isinstance(value, dict), "INVALID_METADATA")
    return cast(dict[str, Any], value)


def validate_protocol(shadow_review: bool, shadow_quote_v2: bool, shadow_projection: bool, shadow_single_target: bool = False, experience: bool = False) -> None:
    flags = (shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    demand(all(type(flag) is bool for flag in flags) and sum(flags) <= 1, "PROTOCOL_CONFLICT")


def marker(helper_sha: str, *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> str:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    if experience:
        from scripts.companion_ai_validation import experience_continuation as exp
        return (
            "test: request OfferPilot companion product experience v1\n\n"
            f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
            "OfferPilot-Companion-AI-Request: one-shot-20261101-product-experience-v1-8\n"
            f"OfferPilot-Companion-AI-Predecessor: {exp.PREDECESSOR_RUN_ID}\n"
            f"OfferPilot-Companion-AI-Ledger: {exp.LEDGER_SHA256}"
        )
    if shadow_single_target:
        from scripts.companion_ai_validation import shadow_single_target_continuation as single
        return (
            "test: request OfferPilot companion single-target v0.2\n\n"
            f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
            "OfferPilot-Companion-AI-Request: one-shot-20261021-single-target-v02-7\n"
            f"OfferPilot-Companion-AI-Predecessor: {single.PREDECESSOR_RUN_ID}\n"
            f"OfferPilot-Companion-AI-Ledger: {single.LEDGER_SHA256}"
        )
    if shadow_projection:
        from scripts.companion_ai_validation import shadow_projection_continuation as projection
        return (
            "test: request OfferPilot companion shadow input projection\n\n"
            f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
            "OfferPilot-Companion-AI-Request: one-shot-20261021-shadow-input-projection-6\n"
            f"OfferPilot-Companion-AI-Predecessor: {projection.PREDECESSOR_RUN_ID}\n"
            f"OfferPilot-Companion-AI-Ledger: {projection.LEDGER_SHA256}"
        )
    if shadow_quote_v2:
        demand(not shadow_review, "PROTOCOL_CONFLICT")
        from scripts.companion_ai_validation import shadow_quote_continuation as quote
        return (
            "test: request OfferPilot companion shadow quote v2\n\n"
            f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
            "OfferPilot-Companion-AI-Request: one-shot-20261021-shadow-quote-v2-5\n"
            f"OfferPilot-Companion-AI-Predecessor: {quote.PREDECESSOR_RUN_ID}\n"
            f"OfferPilot-Companion-AI-Ledger: {quote.LEDGER_SHA256}"
        )
    if shadow_review:
        from scripts.companion_ai_validation import shadow_continuation as shadow
        return (
            "test: request OfferPilot companion shadow review\n\n"
            f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
            "OfferPilot-Companion-AI-Request: one-shot-20261021-shadow-review-4\n"
            f"OfferPilot-Companion-AI-Predecessor: {shadow.PREDECESSOR_RUN_ID}\n"
            f"OfferPilot-Companion-AI-Ledger: {shadow.LEDGER_SHA256}"
        )
    return (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {helper_sha}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021-correction-3\n"
        f"OfferPilot-Companion-AI-Predecessor: {lineage.PREDECESSOR_RUN_ID}\n"
        f"OfferPilot-Companion-AI-Ledger: {lineage.LEDGER_SHA256}"
    )


def exact_marker(value: Any, helper_sha: str, *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> bool:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    # Git commit messages can include their terminal LF, not arbitrary whitespace.
    expected = marker(helper_sha, shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience)
    return value in (expected, expected + "\n")


@dataclass(frozen=True)
class Context:
    helper_sha: str
    request_sha: str
    run_id: int
    repository_id: int
    shadow_review: bool = False
    shadow_quote_v2: bool = False
    shadow_projection: bool = False
    shadow_single_target: bool = False
    experience: bool = False

    def __post_init__(self) -> None:
        validate_protocol(self.shadow_review, self.shadow_quote_v2, self.shadow_projection, self.shadow_single_target, self.experience)


def validate_context(env: Mapping[str, str], *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> Context:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    demand(env.get("GITHUB_RUN_ATTEMPT") == "1", "RERUN_FORBIDDEN")
    helper, request = env.get(HELPER_VAR, ""), env.get(REQUEST_VAR, "")
    old_shas: tuple[str, ...] = (lineage.ORIGINAL_HELPER_SHA, lineage.ORIGINAL_SOURCE_SHA,
                lineage.SECOND_HELPER_SHA, lineage.SECOND_SOURCE_SHA,
                lineage.PREDECESSOR_HELPER_SHA, lineage.PREDECESSOR_SOURCE_SHA)
    if shadow_review:
        from scripts.companion_ai_validation import shadow_continuation as shadow
        old_shas += (shadow.PREDECESSOR_HELPER_SHA, shadow.PREDECESSOR_SOURCE_SHA)
    if shadow_quote_v2:
        from scripts.companion_ai_validation import shadow_quote_continuation as quote
        old_shas += tuple(quote.ALL_HISTORICAL_IDENTITIES)
    if shadow_projection:
        from scripts.companion_ai_validation import shadow_projection_continuation as projection
        old_shas += tuple(projection.ALL_HISTORICAL_IDENTITIES)
    if shadow_single_target:
        from scripts.companion_ai_validation import shadow_single_target_continuation as single
        old_shas += tuple(single.ALL_HISTORICAL_IDENTITIES)
    if experience:
        from scripts.companion_ai_validation import experience_continuation as exp
        old_shas += tuple(exp.ALL_HISTORICAL_IDENTITIES)
    demand(bool(SHA.fullmatch(helper) and SHA.fullmatch(request)) and helper != request
           and helper not in old_shas and request not in old_shas,
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
    return Context(helper, request, int(run_id), int(repository_id), shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)


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
        and exact_marker(event["head_commit"].get("message"), ctx.helper_sha,
                         shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {})),
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
        and exact_marker(request.get("message"), ctx.helper_sha, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {})),
        "REQUEST_MUST_BE_EMPTY_CHILD",
    )


def validate_environment(environment: Any, policies: list[dict[str, Any]]) -> int:
    environment = obj(environment)
    demand(positive(ENVIRONMENT_ID) and environment.get("name") == ENVIRONMENT
           and positive(environment.get("id")) and environment.get("id") == ENVIRONMENT_ID,
           "PROTECTED_ENVIRONMENT_MISSING")
    demand(environment.get("can_admins_bypass") is False, "ENVIRONMENT_BYPASS_NOT_DISABLED")
    rules = environment.get("protection_rules")
    demand(isinstance(rules, list), "REQUIRED_REVIEWER_MISSING")
    assert isinstance(rules, list)
    reviewers = [obj(rule) for rule in rules if obj(rule).get("type") == "required_reviewers"]
    demand(len(reviewers) == 1, "REQUIRED_REVIEWER_MISSING")
    configured = reviewers[0].get("reviewers")
    demand(isinstance(configured, list) and len(configured) == 1, "EXACT_OWNER_REVIEWER_REQUIRED")
    assert isinstance(configured, list)
    reviewer = obj(configured[0])
    person = obj(reviewer.get("reviewer"))
    demand(reviewer.get("type") == "User" and person.get("type") == "User"
           and person.get("login") == REVIEWER_LOGIN and positive(person.get("id"))
           and person.get("id") == REVIEWER_ID,
           "EXACT_OWNER_REVIEWER_REQUIRED")
    # A single-owner run must remain approvable by that owner. Approval itself is
    # verified independently in the paid phase; bypass is never an approval.
    demand(reviewers[0].get("prevent_self_review") is False, "OWNER_REVIEW_CONFIGURATION_INVALID")
    policy = obj(environment.get("deployment_branch_policy"))
    demand(policy.get("protected_branches") is False and policy.get("custom_branch_policies") is True
           and positive(BRANCH_POLICY_ID) and len(policies) == 1
           and positive(obj(policies[0]).get("id")) and policies[0].get("id") == BRANCH_POLICY_ID
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
        and positive(user.get("id")) and user.get("id") == REVIEWER_ID
        and user.get("login") == REVIEWER_LOGIN
        and isinstance(environments, list) and len(environments) == 1
        and positive(obj(environments[0]).get("id")) and environments[0].get("id") == environment_id
        and environments[0].get("name") == ENVIRONMENT,
        "OWNER_APPROVAL_UNVERIFIED",
    )


def validate_run(ctx: Context, run: dict[str, Any], workflow_id: int) -> None:
    demand(positive(run.get("id")) and positive(run.get("run_number")), "INVALID_RUN_IDENTITY")
    demand(type(run.get("run_attempt")) is int and run["run_attempt"] == 1, "RERUN_FORBIDDEN")
    demand(positive(run.get("workflow_id")) and run.get("workflow_id") == workflow_id
           and workflow_path(run.get("path"))
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
               and positive(job.get("run_id")) and job.get("run_id") == run["id"]
               and job.get("head_sha") == run["head_sha"],
               "JOB_HISTORY_INVALID")
        # Some REST API versions omit run_attempt from jobs; the run is checked
        # above and filter=all exposes duplicate jobs from all attempts.
        demand(type(job.get("run_attempt", 1)) is int and job.get("run_attempt", 1) == 1, "RERUN_FORBIDDEN")
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
    demand(positive(WORKFLOW_ID) and positive(workflow.get("id"))
           and workflow.get("id") == WORKFLOW_ID
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
    pinned = historical_batches(shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    paid_batches = {batch.run_id: batch for batch in pinned}
    helper_batches = {batch.helper_run_id: batch for batch in pinned}
    predecessor_runs: dict[int, dict[str, Any]] = {}
    predecessor_helpers: dict[int, dict[str, Any]] = {}
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
        if run["id"] in paid_batches:
            batch = paid_batches[run["id"]]
            demand(run["head_sha"] == batch.source_sha
                   and run.get("conclusion") == batch.conclusion
                   and all(completed(job, batch.conclusion if name == PAID_JOB else "success")
                           for name, job in jobs.items()),
                   "PREDECESSOR_RUN_MISMATCH")
            predecessor_runs[run["id"]] = run
        else:
            demand(completed(jobs[PAID_JOB], "skipped"), "PAID_BUDGET_ALREADY_CONSUMED")
        if run["id"] in helper_batches:
            batch = helper_batches[run["id"]]
            demand(run["head_sha"] == batch.helper_sha
                   and run.get("conclusion") == "success"
                   and completed(jobs[OFFLINE_JOB], "success")
                   and completed(jobs[PREFLIGHT_JOB], "skipped"), "PREDECESSOR_HELPER_MISMATCH")
            predecessor_helpers[run["id"]] = run
        if run["head_sha"] == ctx.helper_sha:
            demand(run.get("conclusion") == "success" and completed(jobs[OFFLINE_JOB], "success"),
                   "HELPER_OFFLINE_SUCCESS_REQUIRED")
            helper_runs.append(run)
    demand(len(helper_runs) == 1, "HELPER_OFFLINE_SUCCESS_REQUIRED")
    demand(set(predecessor_runs) == set(paid_batches)
           and set(predecessor_helpers) == set(helper_batches), "PREDECESSOR_HISTORY_REQUIRED")
    chain = [number for batch in pinned for number in (
        predecessor_helpers[batch.helper_run_id]["run_number"],
        predecessor_runs[batch.run_id]["run_number"])]
    chain.extend((helper_runs[0]["run_number"], current["run_number"]))
    demand(all(left < right for left, right in zip(chain, chain[1:])), "PREDECESSOR_HISTORY_ORDER")


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

    def __init__(self, token: str, *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False):
        validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
        self.experience = experience
        self.shadow_projection = shadow_projection
        self.shadow_single_target = shadow_single_target
        self.shadow_quote_v2 = shadow_quote_v2
        self.shadow_review = shadow_review
        demand(isinstance(token, str) and bool(token) and "\n" not in token and "\r" not in token,
               "GITHUB_READ_TOKEN_MISSING")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, path: str) -> Response:
        allowed: tuple[str, ...] = (
            r"git/commits/[0-9a-f]{40}",
            re.escape(f"git/ref/heads/{BRANCH}"),
            re.escape(f"environments/{ENVIRONMENT}"),
            re.escape(f"environments/{ENVIRONMENT}/deployment-branch-policies")
            + r"\?per_page=100&page=[1-9][0-9]*",
            re.escape(f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}"),
            re.escape(f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}/runs")
            + r"\?per_page=100&page=[1-9][0-9]*",
            r"actions/runs/[1-9][0-9]*(?:/approvals)?",
            re.escape(f"actions/artifacts/{lineage.ORIGINAL_ARTIFACT_ID}"),
            re.escape(f"actions/artifacts/{lineage.SECOND_ARTIFACT_ID}"),
            re.escape(f"actions/artifacts/{lineage.PREDECESSOR_ARTIFACT_ID}"),
            r"actions/runs/[1-9][0-9]*/jobs\?filter=all&per_page=100&page=[1-9][0-9]*",
        )
        if self.shadow_review or self.shadow_quote_v2 or self.shadow_projection or self.shadow_single_target or self.experience:
            from scripts.companion_ai_validation import shadow_continuation as shadow
            allowed += (re.escape(f"actions/artifacts/{shadow.PREDECESSOR_ARTIFACT_ID}"),)
        if self.shadow_quote_v2 or self.shadow_projection or self.shadow_single_target or self.experience:
            from scripts.companion_ai_validation import shadow_quote_continuation as quote
            allowed += (re.escape(f"actions/artifacts/{quote.PREDECESSOR_ARTIFACT_ID}"),)
        if self.shadow_projection or self.shadow_single_target or self.experience:
            from scripts.companion_ai_validation import shadow_projection_continuation as projection
            allowed += (re.escape(f"actions/artifacts/{projection.PREDECESSOR_ARTIFACT_ID}"),)
        if self.shadow_single_target or self.experience:
            from scripts.companion_ai_validation import shadow_single_target_continuation as single
            allowed += (re.escape(f"actions/artifacts/{single.PREDECESSOR_ARTIFACT_ID}"),)
        if self.experience:
            from scripts.companion_ai_validation import experience_continuation as exp
            allowed += (re.escape(f"actions/artifacts/{exp.PREDECESSOR_ARTIFACT_ID}"),)
        demand(any(re.fullmatch(pattern, path) for pattern in allowed), "GITHUB_READ_PATH_DENIED")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "offerpilot-companion-preflight"}
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


def verified_lineage(*, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> dict[str, Any]:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    try:
        if experience:
            from scripts.companion_ai_validation import experience_continuation
            return experience_continuation.lineage_fields()
        if shadow_single_target:
            from scripts.companion_ai_validation import shadow_single_target_continuation
            return shadow_single_target_continuation.lineage_fields()
        if shadow_projection:
            from scripts.companion_ai_validation import shadow_projection_continuation
            return shadow_projection_continuation.lineage_fields()
        if shadow_quote_v2:
            from scripts.companion_ai_validation import shadow_quote_continuation
            return shadow_quote_continuation.lineage_fields()
        if shadow_review:
            from scripts.companion_ai_validation import shadow_continuation
            return shadow_continuation.lineage_fields()
        return lineage.lineage_fields()
    except lineage.ContinuationError as error:
        raise Blocked(str(error)) from None


@dataclass(frozen=True)
class HistoricalBatch:
    run_id: int
    helper_run_id: int
    source_sha: str
    helper_sha: str
    tree_sha: str
    artifact_id: int
    archive_size: int
    archive_sha256: str
    request_marker: str
    conclusion: str = "success"


def historical_batches(*, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> tuple[HistoricalBatch, ...]:
    """Admit pinned history only; single-target adds the settled seventh batch."""
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    if experience:
        from scripts.companion_ai_validation import experience_continuation as exp
        return historical_batches(shadow_single_target=True) + (HistoricalBatch(
            exp.PREDECESSOR_RUN_ID, exp.PREDECESSOR_HELPER_RUN_ID,
            exp.PREDECESSOR_SOURCE_SHA, exp.PREDECESSOR_HELPER_SHA,
            exp.PREDECESSOR_TREE_SHA, exp.PREDECESSOR_ARTIFACT_ID,
            exp.ARCHIVE_SIZE, exp.ARCHIVE_SHA256,
            marker(exp.PREDECESSOR_HELPER_SHA, shadow_single_target=True), "failure"),)
    if shadow_single_target:
        from scripts.companion_ai_validation import shadow_single_target_continuation as single
        return historical_batches(shadow_projection=True) + (HistoricalBatch(
            single.PREDECESSOR_RUN_ID, single.PREDECESSOR_HELPER_RUN_ID,
            single.PREDECESSOR_SOURCE_SHA, single.PREDECESSOR_HELPER_SHA,
            single.PREDECESSOR_TREE_SHA, single.PREDECESSOR_ARTIFACT_ID,
            single.ARCHIVE_SIZE, single.ARCHIVE_SHA256,
            marker(single.PREDECESSOR_HELPER_SHA, shadow_projection=True)),)
    if shadow_projection:
        from scripts.companion_ai_validation import shadow_projection_continuation as projection
        return historical_batches(shadow_quote_v2=True) + (HistoricalBatch(
            projection.PREDECESSOR_RUN_ID, projection.PREDECESSOR_HELPER_RUN_ID,
            projection.PREDECESSOR_SOURCE_SHA, projection.PREDECESSOR_HELPER_SHA,
            projection.PREDECESSOR_TREE_SHA, projection.PREDECESSOR_ARTIFACT_ID,
            projection.ARCHIVE_SIZE, projection.ARCHIVE_SHA256,
            marker(projection.PREDECESSOR_HELPER_SHA, shadow_quote_v2=True), "failure"),)
    if shadow_quote_v2:
        demand(not shadow_review, "PROTOCOL_CONFLICT")
        from scripts.companion_ai_validation import shadow_quote_continuation as quote
        return historical_batches(shadow_review=True) + (HistoricalBatch(
            quote.PREDECESSOR_RUN_ID, quote.PREDECESSOR_HELPER_RUN_ID,
            quote.PREDECESSOR_SOURCE_SHA, quote.PREDECESSOR_HELPER_SHA,
            quote.PREDECESSOR_TREE_SHA, quote.PREDECESSOR_ARTIFACT_ID,
            quote.ARCHIVE_SIZE, quote.ARCHIVE_SHA256,
            marker(quote.PREDECESSOR_HELPER_SHA, shadow_review=True), "failure"),)
    if shadow_review:
        from scripts.companion_ai_validation import shadow_continuation as shadow
        return historical_batches() + (HistoricalBatch(
            shadow.PREDECESSOR_RUN_ID, shadow.PREDECESSOR_HELPER_RUN_ID,
            shadow.PREDECESSOR_SOURCE_SHA, shadow.PREDECESSOR_HELPER_SHA,
            shadow.PREDECESSOR_TREE_SHA, shadow.PREDECESSOR_ARTIFACT_ID,
            shadow.ARCHIVE_SIZE, shadow.ARCHIVE_SHA256,
            marker(shadow.PREDECESSOR_HELPER_SHA)),)
    original_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {lineage.ORIGINAL_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021"
    )
    second_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {lineage.SECOND_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021-correction-1\n"
        f"OfferPilot-Companion-AI-Predecessor: {lineage.ORIGINAL_RUN_ID}\n"
        f"OfferPilot-Companion-AI-Ledger: {lineage.ORIGINAL_LEDGER_SHA256}"
    )
    predecessor_marker = (
        "test: request OfferPilot companion real AI validation\n\n"
        f"OfferPilot-Companion-AI-Helper: {lineage.PREDECESSOR_HELPER_SHA}\n"
        "OfferPilot-Companion-AI-Request: one-shot-20261021-correction-2\n"
        f"OfferPilot-Companion-AI-Predecessor: {lineage.SECOND_RUN_ID}\n"
        f"OfferPilot-Companion-AI-Ledger: {lineage.SECOND_LEDGER_SHA256}"
    )
    return (
        HistoricalBatch(lineage.ORIGINAL_RUN_ID, lineage.ORIGINAL_HELPER_RUN_ID,
                        lineage.ORIGINAL_SOURCE_SHA, lineage.ORIGINAL_HELPER_SHA,
                        lineage.ORIGINAL_TREE_SHA, lineage.ORIGINAL_ARTIFACT_ID,
                        lineage.ORIGINAL_ARCHIVE_SIZE, lineage.ORIGINAL_ARCHIVE_SHA256,
                        original_marker),
        HistoricalBatch(lineage.SECOND_RUN_ID, lineage.SECOND_HELPER_RUN_ID,
                        lineage.SECOND_SOURCE_SHA, lineage.SECOND_HELPER_SHA,
                        lineage.SECOND_TREE_SHA, lineage.SECOND_ARTIFACT_ID,
                        lineage.SECOND_ARCHIVE_SIZE, lineage.SECOND_ARCHIVE_SHA256, second_marker),
        HistoricalBatch(lineage.PREDECESSOR_RUN_ID, lineage.PREDECESSOR_HELPER_RUN_ID,
                        lineage.PREDECESSOR_SOURCE_SHA, lineage.PREDECESSOR_HELPER_SHA,
                        lineage.PREDECESSOR_TREE_SHA, lineage.PREDECESSOR_ARTIFACT_ID,
                        lineage.ARCHIVE_SIZE, lineage.ARCHIVE_SHA256, predecessor_marker),
    )


def validate_predecessor_metadata(ctx: Context, read: Reader, environment_id: int) -> None:
    """All historical approvals are provenance, never current-run approval."""
    for batch in historical_batches(shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {})):
        request = obj(single(read, f"git/commits/{batch.source_sha}"))
        helper = obj(single(read, f"git/commits/{batch.helper_sha}"))
        parents = request.get("parents")
        demand(request.get("sha") == batch.source_sha and helper.get("sha") == batch.helper_sha
               and obj(request.get("tree")).get("sha") == batch.tree_sha
               and obj(helper.get("tree")).get("sha") == batch.tree_sha
               and isinstance(parents, list) and len(parents) == 1
               and obj(parents[0]).get("sha") == batch.helper_sha
               and request.get("message") in (batch.request_marker, batch.request_marker + "\n"),
               "PREDECESSOR_SOURCE_MISMATCH")
        validate_review(single(read, f"actions/runs/{batch.run_id}/approvals"), environment_id)
        artifact = obj(single(read, f"actions/artifacts/{batch.artifact_id}"))
        owner = obj(artifact.get("workflow_run"))
        demand(positive(artifact.get("id")) and artifact.get("id") == batch.artifact_id
               and artifact.get("name") == f"companion-ai-{batch.source_sha}"
               and positive(artifact.get("size_in_bytes")) and artifact.get("size_in_bytes") == batch.archive_size
               and artifact.get("digest") == f"sha256:{batch.archive_sha256}"
               and artifact.get("expired") is False
               and artifact.get("url") == API + f"actions/artifacts/{batch.artifact_id}"
               and positive(owner.get("id")) and owner.get("id") == batch.run_id
               and positive(owner.get("repository_id")) and owner.get("repository_id") == ctx.repository_id
               and positive(owner.get("head_repository_id")) and owner.get("head_repository_id") == ctx.repository_id
               and owner.get("head_branch") == BRANCH and owner.get("head_sha") == batch.source_sha,
               "PREDECESSOR_ARTIFACT_MISMATCH")


def price_deadline(shadow_review: bool = False, *, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> float:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    if experience:
        from scripts.companion_ai_validation import experience_continuation
        return float(experience_continuation.PRICE_VALID_UNTIL)
    if shadow_single_target:
        from scripts.companion_ai_validation import shadow_single_target_continuation
        return float(shadow_single_target_continuation.PRICE_VALID_UNTIL)
    if shadow_projection:
        from scripts.companion_ai_validation import shadow_projection_continuation
        return float(shadow_projection_continuation.PRICE_VALID_UNTIL)
    if shadow_quote_v2:
        demand(not shadow_review, "PROTOCOL_CONFLICT")
        from scripts.companion_ai_validation import shadow_quote_continuation
        return float(shadow_quote_continuation.PRICE_VALID_UNTIL)
    if shadow_review:
        from scripts.companion_ai_validation import shadow_continuation
        return float(shadow_continuation.PRICE_VALID_UNTIL)
    return PRICE_VALID_UNTIL


def start_quote_window(*, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> float:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    return time.monotonic() + (price_deadline(shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience) - time.time())


def check_quote_window(deadline: float, *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> None:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    wall, monotonic = time.time(), time.monotonic()
    demand(all(type(value) in (int, float) and 0 <= value < 9_223_372_036 and math.isfinite(value)
               for value in (wall, monotonic, deadline))
           and wall + SESSION_SECONDS <= price_deadline(shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience)
           and monotonic + SESSION_SECONDS <= deadline, "PRICE_REVIEW_EXPIRED")


def monotonic_ns(value: float) -> int:
    """Conservative integer handoff of the already-anchored monotonic window."""
    demand(type(value) in (int, float) and 0 <= value < 9_223_372_036 and math.isfinite(value),
           "ADMISSION_CLOCK_INVALID")
    return math.floor(value * 1_000_000_000)


def check_handoff_age(checked_wall: int, checked_ns: int) -> None:
    now_ns = monotonic_ns(time.monotonic())
    wall = time.time()
    demand(type(wall) in (int, float) and 0 <= wall < 9_223_372_036 and math.isfinite(wall)
           and 0 <= wall - checked_wall <= 120
           and 0 <= now_ns - checked_ns <= 120_000_000_000, "ADMISSION_EXPIRED")


def preflight(*, phase: str, env: Mapping[str, str], event: Any, read: Reader,
              quote_deadline: float | None = None, shadow_review: bool = False,
              shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False) -> dict[str, Any]:
    validate_protocol(shadow_review, shadow_quote_v2, shadow_projection, shadow_single_target, experience)
    ctx = validate_context(env, shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience)
    demand(phase in ("before", "paid"), "INVALID_PHASE")
    # This step has no provider key. Keep the CLI's same monotonic anchor across
    # event reads, metadata/approval reads and the durable admission handoff.
    deadline = (start_quote_window(shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
                if quote_deadline is None else quote_deadline)
    check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    (verified_lineage(experience=True) if ctx.experience else
     verified_lineage(shadow_single_target=True) if ctx.shadow_single_target else
     verified_lineage(shadow_projection=True) if ctx.shadow_projection else
     verified_lineage(shadow_quote_v2=True) if ctx.shadow_quote_v2 else
     verified_lineage(shadow_review=True) if ctx.shadow_review else verified_lineage())
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
    validate_predecessor_metadata(ctx, read, environment_id)
    if phase == "paid":
        validate_review(single(read, f"actions/runs/{ctx.run_id}/approvals"), environment_id)
    check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    return {"ok": True, "helper_sha": ctx.helper_sha, "request_sha": ctx.request_sha}


def inspect_metadata(read: Reader) -> dict[str, Any]:
    """Registration evidence only. Never writes admission or arms the live gate."""
    workflow = obj(single(read, f"actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}"))
    environment = obj(single(read, f"environments/{ENVIRONMENT}"))
    policies = read_pages(read, f"environments/{ENVIRONMENT}/deployment-branch-policies",
                          "branch_policies")
    demand(positive(workflow.get("id")) and workflow.get("path") == WORKFLOW
           and workflow.get("state") == "active", "WORKFLOW_IDENTITY_MISMATCH")
    demand(positive(environment.get("id")) and environment.get("name") == ENVIRONMENT,
           "PROTECTED_ENVIRONMENT_MISSING")
    demand(environment.get("can_admins_bypass") is False, "ENVIRONMENT_BYPASS_NOT_DISABLED")
    rules = environment.get("protection_rules")
    demand(isinstance(rules, list), "REQUIRED_REVIEWER_MISSING")
    assert isinstance(rules, list)
    reviewers = [obj(rule) for rule in rules if obj(rule).get("type") == "required_reviewers"]
    demand(len(reviewers) == 1 and reviewers[0].get("prevent_self_review") is False,
           "OWNER_REVIEW_CONFIGURATION_INVALID")
    configured = reviewers[0].get("reviewers")
    demand(isinstance(configured, list) and len(configured) == 1, "EXACT_OWNER_REVIEWER_REQUIRED")
    assert isinstance(configured, list)
    reviewer = obj(configured[0])
    person = obj(reviewer.get("reviewer"))
    demand(reviewer.get("type") == "User" and person.get("type") == "User"
           and person.get("login") == REVIEWER_LOGIN and person.get("id") == REVIEWER_ID,
           "EXACT_OWNER_REVIEWER_REQUIRED")
    policy = obj(environment.get("deployment_branch_policy"))
    demand(policy.get("protected_branches") is False and policy.get("custom_branch_policies") is True
           and len(policies) == 1 and positive(policies[0].get("id"))
           and policies[0].get("name") == BRANCH and policies[0].get("type") == "branch",
           "EXACT_BRANCH_POLICY_REQUIRED")
    return {"registration_only": True, "workflow_id": workflow["id"],
            "environment_id": environment["id"], "branch_policy_id": policies[0]["id"],
            "reviewer_id": REVIEWER_ID, "protection_shape_verified": True}


def write_admission(path: str, ctx: Context, quote_deadline: float | None = None) -> None:
    """An admission is a local handoff, not a reusable approval or budget reset."""
    deadline = (start_quote_window(shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
                if quote_deadline is None else quote_deadline)
    check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    # Stamp before verification/open/write/fsync. These are the same Linux
    # monotonic origin in the paid preflight and subsequent live entrypoint.
    checked_ns, deadline_ns = monotonic_ns(time.monotonic()), monotonic_ns(deadline)
    checked_wall_raw = time.time()
    demand(type(checked_wall_raw) in (int, float) and 0 <= checked_wall_raw < 9_223_372_036
           and math.isfinite(checked_wall_raw), "ADMISSION_CLOCK_INVALID")
    checked_wall = int(checked_wall_raw)
    fields = (verified_lineage(experience=True) if ctx.experience else
     verified_lineage(shadow_single_target=True) if ctx.shadow_single_target else
     verified_lineage(shadow_projection=True) if ctx.shadow_projection else
     verified_lineage(shadow_quote_v2=True) if ctx.shadow_quote_v2 else
     verified_lineage(shadow_review=True) if ctx.shadow_review else verified_lineage())
    record = {**fields, "schema": lineage.ADMISSION_SCHEMA,
              "source_sha": ctx.request_sha, "helper_sha": ctx.helper_sha,
              "run_id": str(ctx.run_id), "run_attempt": "1", "checked_at": checked_wall,
              "checked_at_monotonic_ns": checked_ns,
              "quote_deadline_monotonic_ns": deadline_ns}
    check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    check_handoff_age(checked_wall, checked_ns)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        raise Blocked("ADMISSION_WRITE_BLOCKED") from None
    # A slow open/flush/fsync must not report successful admission. The workflow
    # never exposes the provider secret after a nonzero preflight exit.
    check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
    check_handoff_age(checked_wall, checked_ns)


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Blocked("CLI_INVALID")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeParser(add_help=False)
        parser.add_argument("--phase", choices=("inspect", "before", "paid"), required=True)
        protocols = parser.add_mutually_exclusive_group()
        protocols.add_argument("--shadow-review", action="store_true")
        protocols.add_argument("--shadow-quote-v2", action="store_true")
        protocols.add_argument("--shadow-projection", action="store_true")
        protocols.add_argument("--shadow-single-target", action="store_true")
        protocols.add_argument("--experience", action="store_true")
        parser.add_argument("--helper-sha")
        parser.add_argument("--request-sha")
        parser.add_argument("--admission-file")
        args = parser.parse_args(argv)
        demand(not args.admission_file or args.phase == "paid", "ADMISSION_PHASE_INVALID")
        if args.phase == "inspect":
            demand(not args.helper_sha and not args.request_sha and not args.admission_file,
                   "REGISTRATION_ONLY")
            demand(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
                   and os.environ.get("GITHUB_REF") == f"refs/heads/{BRANCH}"
                   and os.environ.get("GITHUB_EVENT_NAME") == "push"
                   and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "TRIGGER_NOT_AUTHORIZED")
            result = inspect_metadata(GitHubReader(os.environ.get("GH_TOKEN", "")))
            print(json.dumps(result, sort_keys=True))
            return 0
        # Deliberately select metadata, rather than snapshotting the environment.
        keys = (HELPER_VAR, REQUEST_VAR, "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME",
                "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_SHA", "GITHUB_WORKFLOW_REF",
                "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_ID", "GITHUB_REPOSITORY_ID")
        env = {key: os.environ.get(key, "") for key in keys}
        if args.helper_sha is not None:
            env[HELPER_VAR] = args.helper_sha
        if args.request_sha is not None:
            env[REQUEST_VAR] = args.request_sha
        ctx = validate_context(env, shadow_review=args.shadow_review,
                               shadow_quote_v2=args.shadow_quote_v2, shadow_projection=args.shadow_projection, shadow_single_target=args.shadow_single_target, experience=args.experience)
        deadline = start_quote_window(shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
        check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
        result = preflight(phase=args.phase, env=env, event=event,
                           read=(GitHubReader(os.environ.get("GH_TOKEN", ""), experience=True)
                                 if ctx.experience else
                                 GitHubReader(os.environ.get("GH_TOKEN", ""), shadow_single_target=True)
                                 if ctx.shadow_single_target else
                                 GitHubReader(os.environ.get("GH_TOKEN", ""), shadow_projection=True)
                                 if ctx.shadow_projection else
                                 GitHubReader(os.environ.get("GH_TOKEN", ""), shadow_quote_v2=True)
                                 if ctx.shadow_quote_v2 else
                                 GitHubReader(os.environ.get("GH_TOKEN", ""), shadow_review=True)
                                 if ctx.shadow_review else GitHubReader(os.environ.get("GH_TOKEN", ""))),
                           quote_deadline=deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
        if args.admission_file:
            write_admission(args.admission_file, ctx, deadline)
        check_quote_window(deadline, shadow_review=ctx.shadow_review,
                         **({"experience": True} if ctx.experience else
                            {"shadow_single_target": True} if ctx.shadow_single_target else
                            {"shadow_projection": True} if ctx.shadow_projection else
                            {"shadow_quote_v2": True} if ctx.shadow_quote_v2 else {}))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, Blocked) else "VALIDATION_BLOCKED"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
