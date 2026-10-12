from __future__ import annotations

from collections import Counter
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import unquote
from scripts.evaluation_subject.process_tree import ProcessTree

from scripts.test_selection.core import (
    SelectionError, digest, file_hash, reconcile_backend, runtime_environment, snapshot,
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
            argv = ["npm.cmd" if os.name == "nt" else "npm", "test", "--", "--reporter=json",
                    "--pool=forks", "--maxWorkers=1", "--minWorkers=1",
                    "--outputFile=" + str(report_path.resolve()), *[Path(p).relative_to("web").as_posix() for p in frontend]]
            command("frontend", argv, root / "web")
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
            if not value.get("harness_variants"):
                rows.append({"check": check, "code": 3, "status": "blocked",
                             "reason": "baseline_and_candidate_required_for_six_trials"})
                continue
            variants_path = output / "harness-variants.json"
            variants_path.write_text(json.dumps(value["harness_variants"]), encoding="utf-8")
            command(check, cli_command("eval", "run", "--suite",
                str(root / "evals/suites/harness-p0-v1.json"), "--variants", str(variants_path.resolve()),
                "--output", str((output / "harness-p0").resolve())), root)
            try:
                from offerpilot.evaluation.compare import read_run
                from offerpilot.evaluation.reporting import aggregate
                manifest, trials = read_run(output / "harness-p0")
                checked = aggregate(manifest, trials)
                code = checked["exit_code"]
                if not code and (manifest.plan_kind != "p0" or checked["planned"] != 6
                                 or set(manifest.variants) != {"baseline", "candidate"}):
                    code = 2
                rows.append({"check": "harness_reconciliation", "code": code,
                             "status": "passed" if code == 0 else "failed" if code == 1 else "error",
                             "planned": checked["planned"], "counts": checked["counts"]})
            except OSError:
                rows.append({"check": "harness_reconciliation", "code": 3, "status": "blocked",
                             "reason": "missing_harness_report"})
            except (ValueError, KeyError, TypeError):
                rows.append({"check": "harness_reconciliation", "code": 2, "status": "error",
                             "reason": "invalid_harness_report"})
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
