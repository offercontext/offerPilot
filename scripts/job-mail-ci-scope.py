"""Route mail-only CI work without suppressing either required check entry.

Uses the complete event range and treats unknown inputs as requiring all work.
No repository dependencies are needed on the lightweight routing runner.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


def classify(paths: list[str]) -> tuple[bool, bool]:
    if not paths:
        return True, True
    browser = native = False
    for path in paths:
        if path in {"scripts/job-mail-ci-scope.py", "tests/test_job_mail_ci_scope.py"}:
            browser = native = True
        elif path == "tests/test_job_mail_process_lock.py":
            native = True
        elif path.startswith(("web/", "scripts/job-mail-", "tests/fixtures/job_mail")):
            browser = True
        elif path.startswith(("src/", "tests/", ".github/")):
            # Shared app imports, auth/schema, test guards and CI can affect mail.
            browser = native = True
        elif path.startswith(("docs/", "plugins/dsh-offerpilot/", "scripts/dsh-")):
            continue
        elif path.endswith(".md") or path in {"LICENSE", ".gitignore", ".gitattributes"}:
            continue
        else:
            # Python dependencies, unknown/new runtime inputs: never silently skip.
            browser = native = True
    return browser, native


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def route(repo: Path, event_name: str, event: dict[str, Any], head: str) -> dict[str, Any]:
    full = {"browser": True, "native_lock": True, "paths": [],
            "reason": "unknown range; run all mail checks", "base": None, "head": head}
    if event_name == "workflow_dispatch":
        return {**full, "reason": "manual dispatch; run all mail checks"}
    try:
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
        elif event_name == "push":
            base = event["before"]
            if event.get("after") != head:
                return full
        else:
            return full
        if any(not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
               or sha == "0" * 40 for sha in (base, head)):
            return full
        if _git(repo, "rev-parse", "HEAD").decode().strip() != head:
            return full
        # --no-renames retains both old and new paths. NUL separation handles
        # spaces, Unicode and line breaks without Git's quoted-path encoding.
        paths = [path.decode("utf-8", errors="strict") for path in
                 _git(repo, "diff", "--no-renames", "--name-only", "-z", base, head, "--")
                 .split(b"\0") if path]
    except (KeyError, TypeError, UnicodeError, OSError, subprocess.CalledProcessError):
        return full
    browser, native = classify(paths)
    return {"browser": browser, "native_lock": native, "paths": paths,
            "base": base, "head": head, "reason": "complete event diff"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", type=Path,
                        default=Path(os.environ.get("GITHUB_EVENT_PATH", "missing-event.json")))
    parser.add_argument("--head", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--output", type=Path,
                        default=Path(os.environ.get("GITHUB_OUTPUT", "mail-scope-output.txt")))
    parser.add_argument("--summary", type=Path,
                        default=Path(os.environ.get("GITHUB_STEP_SUMMARY", "mail-scope-summary.md")))
    args = parser.parse_args()
    try:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            event = {}
    except (OSError, ValueError):
        event = {}
    result = route(args.repo, args.event_name, event, args.head)
    with args.output.open("a", encoding="utf-8") as stream:
        for key in ("browser", "native_lock"):
            stream.write(f"{key}={str(result[key]).lower()}\n")
    with args.summary.open("a", encoding="utf-8") as stream:
        stream.write("### Mail CI scope\n\n")
        stream.write(f"Decision: {result['reason']}; {len(result['paths'])} changed paths.\n\n")
        for key in ("browser", "native_lock"):
            stream.write(f"- {key}: {'required' if result[key] else 'not required'}\n")
    # Log only routing facts; do not render arbitrary filenames into workflow commands.
    print(json.dumps({key: value for key, value in result.items() if key != "paths"}))


if __name__ == "__main__":
    main()
