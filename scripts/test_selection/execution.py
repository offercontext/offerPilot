from __future__ import annotations

from collections import Counter
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any
from urllib.parse import unquote
from scripts.evaluation_subject.process_tree import ProcessTree

from scripts.test_selection.core import (
    SelectionError, digest, file_hash, reconcile_backend, runtime_environment, snapshot, unsupported_checks,
)


def cli_command(*args: str) -> list[str]:
    # offerpilot.cli defines the console app but has no __main__ invocation.
    return [sys.executable, "-c", "from offerpilot.cli import app; app()", *args]


def check_plan(root: Path, value: dict[str, Any]) -> None:
    hashed = {k: v for k, v in value.items() if k != "plan_hash"}
    if digest(hashed) != value.get("plan_hash"):
        raise SelectionError("plan hash mismatch")
    if snapshot(root) != value["snapshot"] or runtime_environment(root) != value["environment"]:
        raise SelectionError("source/index/dependencies/environment changed; regenerate plan")
    if digest({p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")}) != value["collector_hash"]:
        raise SelectionError("collector changed; regenerate plan")
    for variant in value.get("harness_variants") or []:
        if snapshot(Path(variant["checkout"])) != value["harness_snapshots"][variant["variant_id"]]:
            raise SelectionError("Harness target snapshot changed")


def reconcile_frontend(report: dict[str, Any], selected: list[str], root: Path) -> tuple[int, dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("testResults"), list):
        raise SelectionError("invalid frontend report schema")
    suites = report.get("testResults", [])
    files, ids, failed, skipped, empty = [], [], [], [], []
    for suite in suites:
        file = Path(suite["name"]).resolve().relative_to(root.resolve()).as_posix()
        files.append(file)
        assertions = suite.get("assertionResults", [])
        if not assertions:
            empty.append(file)
        for assertion in assertions:
            identity = file + "::" + assertion["fullName"]
            ids.append(identity)
            status = assertion["status"]
            if status == "failed":
                failed.append(identity)
            elif status != "passed":
                skipped.append(identity)
    problems = {"missing": sorted(set(selected) - set(files)), "extra": sorted(set(files) - set(selected)),
                "duplicate_files": [k for k, n in Counter(files).items() if n > 1],
                "duplicate_assertions": [k for k, n in Counter(ids).items() if n > 1],
                "empty_files": empty, "skipped": skipped}
    return 1 if failed else 2 if any(problems.values()) else 0, {
        **problems, "failed": failed, "assertion_ids": ids, "executed": len(ids),
        "passed": len(ids) - len(failed) - len(skipped), "skipped_count": len(skipped)}


# cmd.exe has an 8191-character batch limit. Reserve space for npm.cmd/node
# expansion rather than treating that hard ceiling as usable argument capacity.
WINDOWS_NPM_COMMAND_BUDGET = 6000


def frontend_command(value: dict[str, Any], report_path: Path, *,
                     windows: bool | None = None) -> list[str]:
    """Keep npm lifecycle semantics; omit filters only for the exact full catalog."""
    def ids(rows: Any) -> list[str]:
        if not isinstance(rows, list):
            raise SelectionError("invalid frontend catalog")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise SelectionError("invalid frontend identity")
            if row.get("surface") != "frontend":
                continue
            name = row.get("id")
            if (not isinstance(name, str) or row.get("file") != name
                    or not name.startswith("web/") or name[4:].startswith("-")
                    or "\\" in name or "\0" in name
                    or PurePosixPath(name).as_posix() != name
                    or any(part in {"", ".", ".."} for part in name.split("/"))):
                raise SelectionError("noncanonical frontend identity")
            result.append(name)
        if not result or len(result) != len(set(result)):
            raise SelectionError("empty or duplicate frontend identities")
        return result

    selected, catalog = ids(value.get("selected_tests")), ids(value.get("catalog"))
    if not set(selected) <= set(catalog):
        raise SelectionError("selected frontend identities absent from frozen catalog")
    is_windows = os.name == "nt" if windows is None else windows
    # The plan output parent is already validated by safe_output. Do not call
    # filesystem resolution before checking an overlong Windows argument.
    argv = ["npm.cmd" if is_windows else "npm", "test", "--", "--reporter=json",
            "--pool=forks", "--maxWorkers=1", "--minWorkers=1",
            "--outputFile=" + str(report_path.absolute())]
    # With no file filters Vitest uses the same locked config as catalog collection.
    # Reconciliation still refuses missing/extra/duplicate/skipped executions.
    if set(selected) != set(catalog):
        argv.extend(str(PurePosixPath(name).relative_to("web")) for name in selected)
    if is_windows:
        if any(any(c in arg for c in '&|<>^%!()\r\n"') for arg in argv):
            raise SelectionError("unsafe Windows npm command argument")
        if len(subprocess.list2cmdline(argv).encode("utf-16-le")) // 2 > WINDOWS_NPM_COMMAND_BUDGET:
            raise SelectionError("Windows npm command exceeds safe budget")
    return argv


def documentation_links(root: Path, paths: list[str]) -> list[str]:
    broken = []
    for name in paths:
        path = root / name
        if path.suffix != ".md" or not path.is_file():
            continue
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            target = target.strip().strip("<>").split(' "')[0].split("#")[0]
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("/"):
                continue
            if not (path.parent / unquote(target)).exists():
                broken.append(name + ": " + target)
    return broken


def execute(root: Path, plan_path: Path, review_evidence: Path | None = None) -> dict[str, Any]:
    value = json.loads(plan_path.read_bytes())
    check_plan(root, value)
    output = plan_path.parent / "execution"
    output.mkdir(exist_ok=False)
    rows: list[dict[str, Any]] = []
    unsupported_capabilities = unsupported_checks(value["required_checks"])
    if unsupported_capabilities:
        # Preflight the entire plan, before any selected test or command starts.
        summary = {"plan_id": value["plan_id"], "planned": len(value["selected_tests"]),
                   "checks": [{"check": check, "status": "blocked", "code": 3,
                               "reason": "harness_evaluation_not_migrated"}
                              for check in unsupported_capabilities],
                   "exit_code": 3, "release_eligible": False, "selection_status": "blocked",
                   "needs_full": True}
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    frontend = [t["id"] for t in value["selected_tests"] if t.get("surface") == "frontend"]
    frontend_argv = None
    if frontend:
        try:
            frontend_argv = frontend_command(value, output / "frontend-report.json")
        except SelectionError as exc:
            summary = {"plan_id": value["plan_id"], "planned": len(value["selected_tests"]),
                       "checks": [{"check": "frontend_command", "status": "blocked", "code": 3,
                                   "reason": str(exc)}], "exit_code": 3, "release_eligible": False,
                       "selection_status": "blocked", "needs_full": True}
            (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary
    if value["selection_status"] == "blocked":
        rows.append({"check": "deleted_test_coverage", "status": "blocked", "code": 3})

    def command(name: str, argv: list[str], cwd: Path, *, timeout: int = 3600) -> int:
        log = output / f"{name}.log"
        code = 3
        diagnostics = []
        tree = None
        try:
            with log.open("wb") as stream:
                tree = ProcessTree(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
                try:
                    returncode = tree.wait(timeout)
                    code = 0 if returncode == 0 else 1 if returncode == 1 or (
                        name in {"content_diff", "committed_content_diff"} and returncode == 2) else 2
                    if name == "harness_p0" and returncode == 3:
                        code = 3
                except (subprocess.TimeoutExpired, TimeoutError):
                    diagnostics.append("timeout")
                    if getattr(getattr(tree, "process", None), "returncode", None) == 1:
                        code = 1
                    try:
                        tree.kill()
                        tree.wait(10)
                    except Exception as exc:
                        diagnostics.append("cleanup:" + type(exc).__name__)
        except Exception as exc:
            diagnostics.append("process:" + type(exc).__name__)
        finally:
            if tree:
                try:
                    tree.close()
                except Exception as exc:
                    diagnostics.append("close:" + type(exc).__name__)
            if diagnostics and code == 0:
                code = 3
        rows.append({"check": name, "argv": argv, "cwd": str(cwd), "status": "passed" if code == 0 else
                     "failed" if code == 1 else "error" if code == 2 else "blocked", "code": code,
                     "diagnostics": diagnostics})
        return code

    def reconcile(name: str, report_path: Path, expected: list[str]) -> None:
        try:
            report = json.loads(report_path.read_bytes())
            code, details = (reconcile_backend(report, expected) if name == "backend" else
                             reconcile_frontend(report, expected, root))
            rows.append({"check": name + "_reconciliation", "code": code,
                         "status": "passed" if code == 0 else "failed" if code == 1 else "error", **details})
        except OSError as exc:
            rows.append({"check": name + "_reconciliation", "code": 3, "status": "blocked",
                         "reason": type(exc).__name__})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            rows.append({"check": name + "_reconciliation", "code": 2, "status": "error",
                         "reason": type(exc).__name__})

    selected = value["selected_tests"]
    if len({t["id"] for t in selected}) != len(selected):
        raise SelectionError("duplicate selected test IDs")
    registered_checks = {"content_diff", "ruff", "mypy", "frontend_build", "local_smoke", "harness_p0", "independent_review", "group_catalog", "documentation_links"}
    if set(value["required_checks"]) - registered_checks:
        raise SelectionError("unregistered required check")
    if any(t.get("surface") not in {"backend", "frontend"} for t in selected):
        raise SelectionError("selected test has no unique registered runner")
    unsupported = [t["id"] for t in selected if t.get("requires")]
    if unsupported:
        rows.append({"check": "required_environment_or_authorization", "status": "blocked", "code": 3,
                     "tests": unsupported})
    else:
        backend = [t["id"] for t in selected if t["surface"] == "backend"]
        if backend:
            ids_path = output / "backend-ids.json"
            ids_path.write_text(json.dumps(backend), encoding="utf-8")
            report_path = output / "backend-report.json"
            command("backend", [sys.executable, "-m", "pytest", "tests", "-q", "--selection-ids",
                str(ids_path.resolve()), "--selection-report", str(report_path.resolve())], root)
            reconcile("backend", report_path, backend)
        frontend = [t["id"] for t in selected if t["surface"] == "frontend"]
        if frontend:
            report_path = output / "frontend-report.json"
            assert frontend_argv is not None
            command("frontend", frontend_argv, root / "web")
            reconcile("frontend", report_path, frontend)
    for check in value["required_checks"]:
        if check == "content_diff":
            command("committed_content_diff", ["git", "diff", "--check", value["merge_base"], "HEAD"], root, timeout=30)
            command(check, ["git", "diff", "--check", "HEAD"], root, timeout=30)
        elif check == "documentation_links":
            broken = documentation_links(root, sorted({p for c in value["changes"] for p in c["paths"]}))
            rows.append({"check": check, "code": 1 if broken else 0,
                         "status": "failed" if broken else "passed", "broken": broken})
        elif check == "ruff":
            targets = [p for p in ("src", "tests", "scripts/test_selection", "scripts/evaluation_subject", "scripts/select_tests.py") if (root / p).exists()]
            command(check, [sys.executable, "-m", "ruff", "check", *targets], root)
        elif check == "mypy":
            targets = [p for p in ("src", "scripts/test_selection", "scripts/evaluation_subject", "scripts/select_tests.py") if (root / p).exists()]
            command(check, [sys.executable, "-m", "mypy", *targets], root)
        elif check == "frontend_build":
            command(check, ["npm.cmd" if os.name == "nt" else "npm", "run", "build"], root / "web")
        elif check == "local_smoke":
            if not (root / "web/dist/index.html").is_file():
                rows.append({"check": check, "code": 3, "status": "blocked", "reason": "static_assets_missing"})
            else:
                code = command(check, cli_command("verify", "--profile", "local",
                               "--static-dir", str(root / "web/dist")), root)
                if code == 0 and "Verify local passed" not in (output / "local_smoke.log").read_text(errors="replace"):
                    rows.append({"check": "local_smoke_completion", "code": 3, "status": "blocked",
                                 "reason": "smoke_completion_missing"})
        elif check == "harness_p0":
            raise SelectionError("Harness evaluation is not migrated")
        elif check == "independent_review":
            try:
                review = json.loads(review_evidence.read_bytes()) if review_evidence else {}
                if not isinstance(review, dict):
                    raise ValueError("invalid review report")
            except (OSError, ValueError):
                rows.append({"check": check, "code": 2, "status": "error", "reason": "invalid_review_report"})
                continue
            valid = (review.get("worktree_fingerprint") == value["worktree_fingerprint"]
                     and review.get("approved") is True and bool(review.get("reviewer_id"))
                     and review.get("reviewer_id") != "/root" and bool(review.get("evidence_ref")))
            rows.append({"check": check, "code": 0 if valid else 3,
                         "status": "passed" if valid else "blocked", "reason": "independent_review_required",
                         "evidence_type": "caller_supplied_review_attestation",
                         "reviewer_id": review.get("reviewer_id"), "evidence_ref": review.get("evidence_ref")})
        elif check == "group_catalog":
            if os.name != "nt":
                rows.append({"check": check, "code": 3, "status": "blocked",
                             "reason": "windows_group_scripts_require_powershell"})
            else:
                catalog_path = output / "full-catalog.json"
                catalog_path.write_text(json.dumps(value["catalog"]), encoding="utf-8")
                command(check, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(root / "scripts/test_selection/check_groups.ps1"), "-RepositoryRoot", str(root),
                    "-Catalog", str(catalog_path), "-Output", str(output / "group-catalog.json")], root)
        else:
            raise SelectionError("unregistered required check")
    try:
        check_plan(root, value)
    except SelectionError:
        rows.append({"check": "final_snapshot", "code": 2, "status": "error"})
    codes = {r["code"] for r in rows}
    summary = {"plan_id": value["plan_id"], "planned": len(selected),
               "checks": rows, "exit_code": 1 if 1 in codes else 2 if 2 in codes else 3 if 3 in codes else 0,
               "release_eligible": False, "selection_status": value["selection_status"]}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
