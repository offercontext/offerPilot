"""Read-only Actions history admission: reserve four calls per attempted live job.

Unknown/cancelled outcomes remain charged four calls. No receipt resets the ledger.
This task permits twenty calls total, not twenty calls per workflow invocation.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

REPO = "offercontext/offerPilot"
WORKFLOW = "image-vision-real.yml"
PER_RUN = 4
TOTAL = 20


def reserved_calls(jobs: list[dict[str, Any]]) -> int:
    live = [j for j in jobs if j.get("name") == "real-vision"]
    if len(live) != 1:
        raise ValueError("ambiguous_live_job_history")
    job = live[0]
    if job.get("status") != "completed":
        raise ValueError("prior_run_not_terminal")
    if job.get("conclusion") == "skipped":
        return 0
    if job.get("conclusion") not in {
        "success", "failure", "cancelled", "timed_out", "action_required", "neutral",
    }:
        raise ValueError("unknown_job_outcome")
    return PER_RUN


def admit(prior_reserved: int) -> None:
    if prior_reserved < 0 or prior_reserved + PER_RUN > TOTAL:
        raise ValueError("task_call_limit_exhausted")


def main() -> int:
    try:
        if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
            raise ValueError("rerun_forbidden")
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise ValueError("repository_mismatch")
        if os.environ.get("GITHUB_REF") != "refs/heads/feat/20261013-pilot-image-messages":
            raise ValueError("branch_mismatch")
        run_id = int(os.environ["GITHUB_RUN_ID"])
        token = os.environ["GH_TOKEN"]

        def get(path: str) -> dict[str, Any]:
            request = urllib.request.Request(
                f"https://api.github.com/repos/{REPO}/{path}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)

        def pages(path: str, key: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for page in range(1, 101):
                value = get(f"{path}?per_page=100&page={page}")
                entries = value[key]
                if not isinstance(entries, list):
                    raise ValueError("history_invalid")
                result.extend(entries)
                if len(entries) < 100:
                    if len(result) != value["total_count"]:
                        raise ValueError("history_incomplete")
                    return result
            raise ValueError("history_too_large")

        runs = pages(f"actions/workflows/{WORKFLOW}/runs", "workflow_runs")
        if sum(r.get("id") == run_id for r in runs) != 1:
            raise ValueError("current_run_not_in_history")
        reserved = 0
        for run in runs:
            if run["id"] == run_id:
                continue
            # Every attempt-1 live job reserves four, even if cancelled before calling.
            jobs = pages(f"actions/runs/{int(run['id'])}/attempts/1/jobs", "jobs")
            reserved += reserved_calls(jobs)
        admit(reserved)
        print(json.dumps({"admitted": True, "prior_calls_reserved": reserved,
                          "this_run_limit": PER_RUN, "task_limit": TOTAL}))
        return 0
    except Exception:
        # Never print authentication, URLs with tokens, or upstream exception content.
        print('{"admitted":false,"reason":"history_or_call_limit_check_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
