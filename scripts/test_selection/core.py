from __future__ import annotations

from collections import Counter
from fnmatch import fnmatchcase
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any, cast
from uuid import uuid4


class SelectionError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise SelectionError("Git ref or diff collection failed") from exc


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, Any]:
    paths = set(git(root, "ls-files", "-z").split(b"\0") + git(
        root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")) - {b""}
    files = {}
    for raw in sorted(paths):
        name = os.fsdecode(raw).replace("\\", "/")
        p = root / name
        files[name] = {"hash": file_hash(p) if p.is_file() else "missing",
                       "mode": p.stat().st_mode if p.exists() else None,
                       "link": os.readlink(p) if p.is_symlink() else None}
    return {"head": git(root, "rev-parse", "HEAD").decode().strip(),
            "files": files, "index": git(root, "ls-files", "--stage", "-z").hex(),
            "status": git(root, "status", "--porcelain=v1", "-z").hex()}


def changed_paths(root: Path, base: str) -> tuple[str, str, list[dict[str, Any]]]:
    commit = git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
    merge = git(root, "merge-base", commit, "HEAD").decode().strip()
    changes = []
    for source, args in (
        ("committed", (merge, "HEAD")), ("staged", ("--cached", "HEAD")), ("worktree", ())
    ):
        raw = git(root, "diff", "--name-status", "-z", "-M", "-C", *args).split(b"\0")
        i = 0
        while i < len(raw) and raw[i]:
            status = raw[i].decode("ascii")
            count = 2 if status[0] in {"R", "C"} else 1
            paths = [os.fsdecode(x).replace("\\", "/") for x in raw[i + 1:i + count + 1]]
            if len(paths) != count:
                raise SelectionError("invalid NUL diff record")
            changes.append({"source": source, "status": status, "paths": paths})
            i += count + 1
    for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if p:
            changes.append({"source": "untracked", "status": "A", "paths": [os.fsdecode(p).replace("\\", "/")]})
    return commit, merge, changes


def load_mapping(root: Path) -> dict[str, Any]:
    mapping = json.loads((root / "tests/selection-map.json").read_bytes())
    if set(mapping) != {"schema_version", "modules", "risk_rules", "test_metadata"} or mapping["schema_version"] != 1:
        raise SelectionError("invalid selection mapping schema")
    modules = mapping["modules"]
    if not isinstance(modules, list) or not isinstance(mapping["test_metadata"], dict) or not isinstance(mapping["risk_rules"], list):
        raise SelectionError("invalid mapping container types")
    if any(not isinstance(m, dict) or not isinstance(m.get("id"), str) for m in modules):
        raise SelectionError("invalid module identity")
    if len({m["id"] for m in modules}) != len(modules):
        raise SelectionError("duplicate module")
    ids = {m["id"] for m in modules}
    for m in modules:
        if any(not isinstance(m.get(k), list) for k in ("production", "tests", "surfaces", "depends_on")):
            raise SelectionError("invalid module containers")
        if set(m) != {"id", "production", "tests", "surfaces", "depends_on"} or set(m["depends_on"]) - ids:
            raise SelectionError("invalid module or dependency")
        if (not m["surfaces"] or set(m["surfaces"]) - {"backend", "frontend", "integration"}
                or any(not isinstance(m[k], list) for k in ("production", "tests", "surfaces", "depends_on"))):
            raise SelectionError("invalid module surface or paths")
        for path in m["production"] + m["tests"]:
            if not isinstance(path, str):
                raise SelectionError("invalid module path type")
            if path.startswith("/") or ".." in Path(path).parts or "\\" in path:
                raise SelectionError("noncanonical module path")
            if not any(c in path for c in "*?[") and not (root / path).exists():
                raise SelectionError(f"stale exact mapping path: {path}")
    for path, entry in mapping["test_metadata"].items():
        if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts or "\\" in path:
            raise SelectionError("noncanonical test metadata path")
        if not isinstance(entry, dict) or not {"modules", "priority", "surface", "requires"} <= set(entry):
            raise SelectionError("incomplete test metadata")
        expected_surface = "frontend" if path.startswith("web/") else "backend"
        if (entry["surface"] != expected_surface or not isinstance(entry["modules"], list)
                or not isinstance(entry["requires"], list)
                or set(entry["requires"]) - {"real_model", "docker", "credentials"}):
            raise SelectionError("invalid test surface or requirement")
        if not (root / path).is_file() or set(entry.get("modules", [])) - ids:
            raise SelectionError(f"stale test or invalid test module: {path}")
        if entry.get("priority") not in {"p0", "p1", "p2", None}:
            raise SelectionError("invalid test priority")
    risk_ids = set()
    repository_files = [os.fsdecode(p).replace("\\", "/") for p in (git(root, "ls-files", "-z")
        + git(root, "ls-files", "--others", "--exclude-standard", "-z")).split(b"\0") if p]
    for rule in mapping["risk_rules"]:
        if (not isinstance(rule, dict) or set(rule) != {"id", "paths", "expand"}
                or not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in risk_ids
                or not isinstance(rule["paths"], list) or not rule["paths"] or rule["expand"] != "full"):
            raise SelectionError("invalid risk expansion rule")
        risk_ids.add(rule["id"])
        for pattern in rule["paths"]:
            if (not isinstance(pattern, str) or not pattern or Path(pattern).is_absolute()
                    or ".." in Path(pattern).parts or "\\" in pattern
                    or not any(fnmatchcase(path, pattern) for path in repository_files)
                    and not any(p.is_file() for p in root.glob(pattern))):
                raise SelectionError("invalid or stale risk path")
    return cast(dict[str, Any], mapping)


def runtime_environment(root: Path) -> dict[str, Any]:
    env: dict[str, Any] = {"python": sys.version, "executable": sys.executable,
                          "platform": platform.platform(), "dependencies": sorted(
        [d.metadata["Name"], d.version] for d in importlib.metadata.distributions())}
    for name, argv in (("node", ["node", "--version"]), ("npm", ["npm.cmd" if os.name == "nt" else "npm", "--version"])):
        try:
            env[name] = subprocess.check_output(argv, cwd=root, stderr=subprocess.PIPE, timeout=15).decode().strip()
        except (OSError, subprocess.SubprocessError):
            env[name] = None
    pkg = root / "web/node_modules/vitest/package.json"
    env["vitest"] = json.loads(pkg.read_bytes())["version"] if pkg.exists() else None
    return env


def collect(root: Path, mapping: dict[str, Any]) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="offerpilot-catalog-") as tmp:
        output = Path(tmp) / "backend.json"
        result = subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
            "--selection-catalog", str(output)], cwd=root, capture_output=True, timeout=300)
        if result.returncode or not output.exists():
            raise SelectionError("pytest collection failed (no empty fallback)")
        catalog = json.loads(output.read_bytes())
    if not catalog:
        raise SelectionError("unexpected empty backend catalog")
    front_files = []
    if (root / "web").is_dir():
        vitest = root / "web/node_modules/vitest/vitest.mjs"
        if not vitest.is_file():
            raise SelectionError("Vitest collector unavailable; install frontend dependencies")
        listing = subprocess.run(["node", str(vitest), "list", "--filesOnly", "--json"],
                                 cwd=root / "web", capture_output=True, timeout=300)
        if listing.returncode:
            raise SelectionError("Vitest collection failed (no path-guess fallback)")
        front_files = sorted(Path(row["file"]).resolve().relative_to(root.resolve()).as_posix()
                             for row in json.loads(listing.stdout))
        if not front_files or len(set(front_files)) != len(front_files):
            raise SelectionError("empty or duplicate Vitest collection")
    metadata = mapping["test_metadata"]
    actual = {r["file"] for r in catalog} | set(front_files)
    missing = actual - set(metadata)
    if missing:
        raise SelectionError("new tests require metadata: " + ", ".join(sorted(missing)))
    for p in front_files:
        entry = metadata[p]
        catalog.append({"id": p, "file": p, "surface": "frontend", **entry})
    ids = [r["id"] for r in catalog]
    if len(ids) != len(set(ids)):
        raise SelectionError("duplicate catalog ID")
    return cast(list[dict[str, Any]], catalog)


def impact(paths: list[str], mappings: list[dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]], set[str]]:
    affected: set[str] = set()
    reasons = []
    surfaces: set[str] = set()
    for path in paths:
        matched = [m for mapping in mappings for m in mapping["modules"]
                   if any(fnmatchcase(path, p) for p in m["production"] + m["tests"])]
        for m in matched:
            affected.add(m["id"])
            surfaces.update(m["surfaces"])
            reasons.append({"kind": "direct", "path": path, "chain": [m["id"]]})
        if not matched and not content_only(path):
            guessed = "frontend" if path.startswith("web/") else "backend" if path.startswith(("src/", "tests/")) else None
            surfaces.update({guessed} if guessed else {"frontend", "backend"})
            reasons.append({"kind": "fallback", "path": path, "surface": guessed or "all"})
    pending: list[tuple[str, list[str]]] = [(str(r["chain"][-1]), list(r["chain"])) for r in reasons if r["kind"] == "direct"]
    visited = set(affected)
    while pending:
        provider, chain = pending.pop(0)
        for mapping in mappings:
            for m in mapping["modules"]:
                if provider in m["depends_on"] and m["id"] not in visited:
                    visited.add(m["id"])
                    affected.add(m["id"])
                    surfaces.update(m["surfaces"])
                    next_chain = chain + [m["id"]]
                    reasons.append({"kind": "dependency", "chain": next_chain})
                    pending.append((m["id"], next_chain))
    return affected, reasons, surfaces


def content_only(path: str) -> bool:
    # These namespaces were audited: product code does not load them at runtime.
    # Unknown docs locations and prompt/template names remain conservative.
    documented = path.startswith(("docs/architecture/", "docs/superpowers/", "docs/archive/",
                                  "docs/learn/", "docs/product-manual/"))
    return (documented and path.endswith(".md") and not any(
        token in Path(path).name.lower() for token in ("prompt", "template"))) or path in {
        "README.md", "README.zh-CN.md", "LICENSE", "CONTRIBUTING.md", "docs/BUGS.md",
        "docs/python-rewrite-contract.md", "docs/p0-release-checklist.md"}


def select(catalog: list[dict[str, Any]], affected: set[str], surfaces: set[str],
           reasons: list[dict[str, Any]], paths: list[str], profile: str,
           *, full: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fallbacks = [r for r in reasons if r["kind"] == "fallback"]
    selected = []
    unclassified = []
    for test in catalog:
        why = []
        unknown = test.get("priority") in {None, "unknown"} or not test.get("modules")
        if unknown:
            unclassified.append({"id": test["id"], "reason": "unknown_priority_or_module"})
        if full or profile == "full":
            why.append("full")
        if test["file"] in paths:
            why.append("direct")
        if set(test.get("modules", [])) & affected:
            why.append("dependency")
        if (test.get("priority") in {"p0", None, "unknown"}
                and (profile == "merge" or test["surface"] in surfaces)):
            why.append("core_p0")
        if any(r["surface"] in {"all", test["surface"]} for r in fallbacks):
            why.append("fallback")
        if not test.get("modules") and (profile == "merge" or test["surface"] in surfaces):
            why.append("unknown_module_surface_full")
        if why:
            selected.append({**test, "reasons": why})
    if not selected:
        raise SelectionError("code change/profile baseline selected zero tests")
    return selected, unclassified


def safe_output(root: Path, output: Path) -> Path:
    output = output.resolve()
    if output.is_relative_to(root.resolve()):
        relative = output.relative_to(root.resolve()).as_posix()
        ignored = subprocess.run(["git", "check-ignore", "--no-index", relative], cwd=root, capture_output=True)
        if ignored.returncode or not relative.startswith("artifacts/test-plans/"):
            raise SelectionError("output must be dedicated ignored artifacts/test-plans or outside repository")
    return output


def plan(root: Path, base: str, profile: str, output: Path,
         harness_variants: Path | None = None) -> dict[str, Any]:
    if profile not in {"dev", "merge", "full"}:
        raise SelectionError("unknown profile")
    output = safe_output(root, output)
    if output.exists():
        raise SelectionError("output cannot be overwritten")
    mapping = load_mapping(root)
    base_commit, merge, changes = changed_paths(root, base)
    before = snapshot(root)
    paths = sorted({p for c in changes for p in c["paths"]})
    simple = profile == "dev" and (not paths or all(content_only(p) for p in paths))
    old_map = None
    try:
        old_map = json.loads(git(root, "show", f"{merge}:tests/selection-map.json"))
    except SelectionError:
        pass  # First rollout has no historical map; mapping change forces full.
    affected, reasons, surfaces = impact(paths, [mapping, *([old_map] if old_map else [])])
    risk_rules = [r for m in [mapping, *([old_map] if old_map else [])] for r in m["risk_rules"]]
    risks = [r for r in risk_rules if any(fnmatchcase(p, pattern) for p in paths for pattern in r["paths"])]
    selection_controls = {"tests/selection-map.json", "tests/conftest.py", "scripts/select_tests.py",
                          "scripts/windows-pytest-groups.ps1", "scripts/windows-vitest-groups.ps1"}
    force_full = any(r["expand"] == "full" for r in risks) or any(
        p in selection_controls or p.startswith("scripts/test_selection/") for p in paths)
    if force_full:
        surfaces = {"backend", "frontend", "integration"}
    catalog = [] if simple else collect(root, mapping)
    selected, unclassified = ([], []) if simple else select(catalog, affected, surfaces, reasons, paths, profile, full=force_full)
    current_test_files = {r["file"] for r in catalog}
    removed = [p for c in changes if c["status"][0] == "D" or (
        c["status"][0] == "R" and c["paths"][1] not in current_test_files)
        for p in c["paths"][:1]
               if p.startswith(("tests/", "web/src/")) and ("test" in p or ".spec." in p)]
    checks = ["content_diff"]
    if any(p.endswith(".md") and content_only(p) for p in paths):
        checks += ["documentation_links"]
    if not simple:
        if "backend" in surfaces or profile in {"merge", "full"} or force_full:
            checks += ["ruff", "mypy"]
        if "frontend" in surfaces or profile == "full" or force_full:
            checks += ["frontend_build"]
        if risks:
            checks += ["independent_review", "local_smoke"]
        if "evaluation" in affected or force_full or profile == "full":
            checks += ["harness_p0"]
        if force_full or profile == "full":
            checks += ["group_catalog"]
    after = snapshot(root)
    if before != after:
        raise SelectionError("snapshot changed during catalog collection")
    harness = json.loads(harness_variants.read_bytes()) if harness_variants else None
    if harness is not None and (not isinstance(harness, list) or len(harness) != 2
            or {v.get("variant_id") for v in harness} != {"baseline", "candidate"}
            or any(set(v) - {"variant_id", "checkout", "interpreter"} for v in harness)):
        raise SelectionError("Harness acceptance requires two non-faulted checkout variants")
    if harness is not None:
        candidate = next(v for v in harness if v["variant_id"] == "candidate")
        if (Path(candidate["checkout"]).resolve() != root.resolve()
                or len({Path(v["checkout"]).resolve() for v in harness}) != 2):
            raise SelectionError("Harness candidate must be current root and baseline a distinct checkout")
    value = {"schema_version": 1, "plan_id": str(uuid4()), "profile": profile,
             "base_ref": base, "base_commit": base_commit, "merge_base": merge,
             "head": before["head"], "snapshot": before, "worktree_fingerprint": digest(before),
             "mapping_hash": file_hash(root / "tests/selection-map.json"),
             "collector_hash": digest({p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")}),
             "catalog_hash": digest(catalog), "catalog": catalog, "environment": runtime_environment(root),
             "harness_variants": harness,
             "harness_snapshots": {v["variant_id"]: snapshot(Path(v["checkout"])) for v in harness} if harness else None,
             "changes": changes, "affected_modules": sorted(affected), "impact_chains": reasons,
             "risk_expansion": risks, "selected_tests": selected, "required_checks": sorted(set(checks)),
             "unclassified": unclassified, "fallbacks": [r for r in reasons if r["kind"] == "fallback"],
             "unselected_summary": {"count": len(catalog) - len(selected),
                 "hash": digest([t["id"] for t in catalog if t["id"] not in {s["id"] for s in selected}])},
             "deleted_tests": removed, "selection_status": "blocked" if removed and profile == "merge" else
                 "no_changes" if simple and not paths else "checks_only" if simple else "ready",
             "selection_level": "full" if profile == "full" or force_full else "surface_full" if any(
                 r["kind"] == "fallback" for r in reasons) else "incremental", "release_eligible": False}
    value["content_basis"] = "audited_non_runtime_documentation_paths" if simple and paths else None
    value["plan_hash"] = digest(value)
    output.mkdir(parents=True, exist_ok=False)
    (output / "selection-plan.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    explanation = [f"Selection: {value['selection_status']} / {value['selection_level']}",
                   "release_eligible=false", "", *[json.dumps(r, ensure_ascii=False) for r in reasons],
                   "", *[f"{t['id']}: {', '.join(t['reasons'])}" for t in selected],
                   "", "Required checks: " + ", ".join(value["required_checks"])]
    (output / "selection-reasons.md").write_text("\n".join(explanation) + "\n", encoding="utf-8")
    return value


def reconcile_backend(report: dict[str, Any], expected: list[str]) -> tuple[int, dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("reports"), list) or any(
        not isinstance(r, dict) or not isinstance(r.get("id"), str)
        or r.get("when") not in {"call", "setup", "teardown"}
        or r.get("outcome") not in {"passed", "failed", "skipped"} for r in report["reports"]
    ):
        raise SelectionError("invalid backend report schema")
    calls = [r for r in report["reports"] if r["when"] == "call"]
    failures = [r for r in report["reports"] if r["outcome"] == "failed"]
    ids = [r["id"] for r in calls]
    errors = {"duplicates": [k for k, n in Counter(ids).items() if n > 1],
              "missing": sorted(set(expected) - set(ids)), "extra": sorted(set(ids) - set(expected)),
              "skips": [r["id"] for r in report["reports"] if r["outcome"] == "skipped"]}
    return (1 if failures else 2 if any(errors.values()) else 0), {
        **errors, "failures": failures, "executed": len(calls),
        "passed": sum(r["outcome"] == "passed" for r in calls),
        "failed": len({r["id"] for r in failures}), "skipped_count": len(errors["skips"])}
