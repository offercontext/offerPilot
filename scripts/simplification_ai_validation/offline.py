"""Exact-node Linux safety gate. A skipped, missing or repeated phase is failure."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from scripts.simplification_ai_validation.broker import require_linux

ROOT = Path(__file__).resolve().parents[2]
SUITE = "scripts/simplification_ai_validation/tests"
MANIFEST = Path(__file__).with_name("offline-nodes.json")
EXPECTED_COUNT = 287


def source_digest() -> str:
    paths = [path for path in Path(__file__).parent.rglob("*")
             if path.suffix in {".py", ".json", ".md"}]
    paths += [ROOT / name for name in (
        ".github/workflows/simplification-real-ai.yml", ".github/workflows/desktop-windows.yml",
        "tests/test_simplification_ai_platform.py", "tests/test_complete_pytest_shards.py",
        "tests/_offline_network_guard.py", "pyproject.toml", "uv.lock",
    )]
    content = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in paths}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def expected_nodes() -> list[str]:
    nodes = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (not isinstance(nodes, list) or len(nodes) != EXPECTED_COUNT
            or any(not isinstance(n, str) or not n.startswith(SUITE + "/test_")
                   or "::test_" not in n for n in nodes)
            or nodes != sorted(set(nodes))):
        raise ValueError("invalid_offline_manifest")
    return nodes


def aggregate(expected: list[str], collected: list[str],
              reports: dict[str, list[dict[str, Any]]], exit_code: int,
              collection_errors: bool, deselected: bool) -> bool:
    if (exit_code != 0 or collection_errors or deselected
            or len(expected) != len(set(expected)) or not expected
            or sorted(collected) != expected or set(reports) != set(expected)):
        return False
    return all(
        sorted((phase["when"], phase["outcome"], phase["xfail"]) for phase in reports[node])
        == [("call", "passed", False), ("setup", "passed", False),
            ("teardown", "passed", False)]
        for node in expected
    )


class Evidence:
    def __init__(self, expected: list[str]):
        self.expected = expected
        self.collected: list[str] = []
        self.reports: dict[str, list[dict[str, Any]]] = {}
        self.collection_errors = False
        self.deselected = False

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed or report.skipped:
            self.collection_errors = True

    def pytest_deselected(self, items: Any) -> None:
        self.deselected = self.deselected or bool(items)

    def pytest_collection_finish(self, session: Any) -> None:
        import pytest
        self.collected = sorted(item.nodeid for item in session.items)
        if self.collected != self.expected or self.collection_errors or self.deselected:
            raise pytest.UsageError(f"offline_collection_not_exact_{EXPECTED_COUNT}")

    def pytest_runtest_logreport(self, report: Any) -> None:
        self.reports.setdefault(report.nodeid, []).append({
            "when": report.when, "outcome": report.outcome,
            "xfail": hasattr(report, "wasxfail"),
        })


def main() -> int:
    require_linux()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT:
        raise ValueError("offline_requires_repository_root")
    nodes = expected_nodes()
    git_env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], env=git_env).decode().strip()
    if os.environ.get("GITHUB_SHA", sha) != sha:
        raise ValueError("offline_checkout_identity")
    source_before = source_digest()
    # Explicit path, no wildcard and no selection options. The final collection
    # and every setup/call/teardown report must match the checked-in node list.
    import pytest
    evidence = Evidence(nodes)
    code = int(pytest.main(["-q", SUITE, "--durations=10"], plugins=[evidence]))
    passed = aggregate(nodes, evidence.collected, evidence.reports, code,
                       evidence.collection_errors, evidence.deselected)
    unchanged = source_before == source_digest()
    passed = passed and unchanged
    report = {
        "schema": 1, "source_sha": sha, "platform": "linux", "passed": passed,
        "sources_sha256": source_before, "sources_unchanged": unchanged,
        "expected_count": EXPECTED_COUNT, "collected_count": len(evidence.collected),
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "exit_code": code, "collection_errors": evidence.collection_errors,
        "deselected": evidence.deselected, "nodes": evidence.collected,
        "reports": evidence.reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "expected": EXPECTED_COUNT,
                      "collected": len(evidence.collected)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
