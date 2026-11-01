"""Route an existing SelectionPlan to draft feedback or the native full gate.

This adapter never selects tests or changes the selector's dev/merge/full rules.
Planning failure requests the native full gate; execution failure is NOT routed
here and must remain a failure. No release eligibility is granted by this file.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from uuid import uuid4


def draft_event(event_name: str, event: dict[str, Any]) -> bool:
    pr = event.get("pull_request")
    return (event_name == "pull_request" and isinstance(pr, dict)
            and pr.get("draft") is True
            and event.get("action") in {"opened", "synchronize", "reopened", "converted_to_draft"})


def decision(plan: dict[str, Any]) -> tuple[str, str]:
    """Allow only the already verified selector's bounded presentation scope."""
    if plan.get("needs_full") is True or plan.get("unsupported_checks"):
        return "full", "selector requires unavailable/broader checks; native full does not certify unported Harness"
    if (plan.get("schema_version") != 1 or plan.get("profile") != "dev"
            or plan.get("release_eligible") is not False
            or plan.get("needs_full") is not False
            or plan.get("unsupported_checks") != []):
        return "full", "unsupported or unverified selector contract"
    if (plan.get("selection_level") != "incremental"
            or any(plan.get(key) != [] for key in
                   ("risk_expansion", "fallbacks", "deleted_tests", "unclassified"))):
        return "full", "risk, fallback, deletion or unclassified scope requires full validation"
    checks, tests = plan.get("required_checks"), plan.get("selected_tests")
    if (not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)
            or not isinstance(tests, list)):
        return "full", "invalid selector plan"
    if plan.get("selection_status") == "checks_only":
        if (tests == [] and plan.get("content_basis") == "audited_non_runtime_documentation_paths"
                and "content_diff" in checks
                and set(checks) <= {"content_diff", "documentation_links"}):
            return "selected", "audited documentation checks only; no release validation"
        return "full", "unverified checks-only scope"
    if (plan.get("selection_status") == "ready" and tests
            and all(isinstance(t, dict) and t.get("surface") == "frontend"
                    and t.get("requires") == [] for t in tests)
            and {"content_diff", "frontend_build"} <= set(checks)
            and set(checks) <= {"content_diff", "documentation_links", "frontend_build"}):
        return "selected", "existing dev frontend selection; no release validation"
    return "full", "backend, broad or incomplete selection requires full validation"


def run_planner(root: Path, command: list[str], log: Path, *, timeout: float = 600) -> int:
    # Planning collects pytest/Vitest in child processes too. Reuse the ported
    # process owner rather than abandoning grandchildren on an outer timeout.
    sys.path.insert(0, str(root))
    from scripts.evaluation_subject.process_tree import ProcessTree
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        tree = ProcessTree(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
        try:
            try:
                return int(tree.wait(timeout))
            except (subprocess.TimeoutExpired, TimeoutError):
                try:
                    tree.kill()
                    tree.wait(10)
                except Exception as exc:
                    # Do NOT start the expensive full gate beside an unowned
                    # collector. This uncaught error fails the routing job.
                    raise RuntimeError("planner cleanup unproven; full fallback blocked") from exc
                raise
        finally:
            try:
                tree.close()
            except Exception as exc:
                raise RuntimeError("planner ownership closure unproven") from exc


def route(root: Path, event_name: str, event: dict[str, Any], head: str,
          output: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"mode": "full", "reason": "stable candidate or non-PR event",
                              "release_eligible": False, "plan": None, "frontend": False}
    if not draft_event(event_name, event):
        return result
    try:
        base = event["pull_request"]["base"]["sha"]
        if (not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base)
                or not re.fullmatch(r"[0-9a-f]{40}", head)
                or base == "0" * 40 or head == "0" * 40):
            raise ValueError("missing event identity")
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                           text=True).strip()
        if checkout != head:
            raise ValueError("checkout differs from the tested merge SHA")
        command = [sys.executable, "scripts/select_tests.py", "plan", "--base", base,
                   "--profile", "dev", "--output", str(output)]
        # This log is outside the selector's immutable source snapshot.
        code = run_planner(root, command, output.parent / "planning.log")
        if code:
            raise ValueError(f"selector planning exited {code}")
        plan_path = output / "selection-plan.json"
        value = json.loads(plan_path.read_text(encoding="utf-8"))
        # Import only after the existing CLI generated the plan successfully.
        sys.path.insert(0, str(root))
        from scripts.test_selection.execution import check_plan
        check_plan(root, value)
        mode, reason = decision(value)
        result.update(mode=mode, reason=reason, plan=str(plan_path),
                      frontend=mode == "selected" and bool(value["selected_tests"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError,
            subprocess.SubprocessError) as exc:
        result["reason"] = f"planning unavailable ({type(exc).__name__}); native full gate required"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        event = json.loads(Path(os.environ.get("GITHUB_EVENT_PATH", "missing-event.json"))
                           .read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            event = {}
    except (OSError, ValueError):
        event = {}
    output = args.root / "artifacts/test-plans" / ("ci-" + uuid4().hex)
    result = route(args.root, os.getenv("GITHUB_EVENT_NAME", ""), event,
                   os.getenv("GITHUB_SHA", ""), output)
    # All generated evidence stays in the selector's ignored output namespace.
    evidence = args.root / "artifacts/test-plans/ci-routing.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"mode={result['mode']}\n")
            stream.write(f"frontend={str(result['frontend']).lower()}\n")
            # Plan paths are generated locally, not supplied by PR text.
            stream.write(f"plan={result['plan'] or ''}\n")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"### Verification route: {result['mode']}\n\n{result['reason']}\n\n"
                         "Selected feedback never certifies the full release gate. "
                         "Native full fallback does not certify unported Harness evaluations.\n")
    return 0  # Routing is not validation; native full remains mandatory on fallback.


if __name__ == "__main__":
    raise SystemExit(main())
