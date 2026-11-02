"""Developer feedback CLI; capability and baseline contract: docs/architecture/test-selection.md."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_selection.core import SelectionError, collect, load_mapping, plan  # noqa: E402
from scripts.test_selection.execution import execute  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot-bound test selection; never a release gate")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    planning = commands.add_parser("plan")
    planning.add_argument("--base", required=True)
    planning.add_argument("--profile", choices=("dev", "merge", "full"), default="dev")
    planning.add_argument("--output", type=Path, required=True)
    planning.add_argument("--harness-variants", type=Path)
    executing = commands.add_parser("execute")
    executing.add_argument("--plan", type=Path, required=True)
    executing.add_argument("--review-evidence", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            mapping = load_mapping(args.root)
            catalog = collect(args.root, mapping)
            print(json.dumps({"collected": len(catalog), "unclassified": sum(
                t.get("priority") in {None, "unknown"} for t in catalog), "release_eligible": False}))
            return 0
        if args.command == "plan":
            value = plan(args.root, args.base, args.profile, args.output, args.harness_variants)
            print(json.dumps({k: value[k] for k in ("plan_id", "selection_status", "selection_level", "release_eligible")}))
            return 0
        summary = execute(args.root, args.plan, args.review_evidence)
        print(json.dumps({"plan_id": summary["plan_id"], "exit_code": summary["exit_code"], "release_eligible": False}))
        return int(summary["exit_code"])
    except (SelectionError, ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"selection error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


import subprocess  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
