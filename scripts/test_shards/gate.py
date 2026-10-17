"""Exact-node full pytest partitioning, isolated execution, and fail-closed evidence.

Run with the repository's Python: gate.py collect/run/aggregate --help.
No plugin or pytest options are exported to nested pytest subprocesses.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

# Reviewed after Windows shard 0 exceeded 90 minutes: 390 chat cases have
# function-local state and no shared custom fixtures. All shards already import
# the whole suite, so this exception adds no collection/catalog construction.
NODE_SPLIT_FILES = frozenset({"tests/test_chat_api.py"})
MANIFEST_VERSION = 4
PARTITION_STRATEGY = "duration-lpt-v1"
DURATION_HISTORY_PATH = Path(__file__).with_name("durations-v1.json")
# SHA256 of the checked-in history with checkout CRLF normalized to LF.
DURATION_HISTORY_SHA256 = "ced3340e064ca8a5fffb1621d2b4a07593f9dd1095c2595d02873c78ff62fe77"


def validate_split_fixture(item, name, definition, scope):
    if item.nodeid.split("::", 1)[0] not in NODE_SPLIT_FILES:
        return
    builtin = definition.func.__module__.startswith("_pytest.")
    if scope != "function" and (not builtin or name.startswith("_xunit_")):
        raise ValueError(f"{item.nodeid}: shared fixture {name} forbids node splitting")


def validate_split_fixture_scope(item):
    if item.nodeid.split("::", 1)[0] not in NODE_SPLIT_FILES:
        return
    callspec = getattr(item, "callspec", None)
    overrides = callspec._arg2scope if callspec is not None else {}
    for name, scope in overrides.items():
        if getattr(scope, "value", scope) != "function":
            raise ValueError(f"{item.nodeid}: shared parameter {name} forbids node splitting")
    # Include dynamically acquired repository fixtures, even if another module
    # could have populated their cache. Unused third-party plugin fixtures are
    # outside this reviewed file's closure and must not block collection.
    # These pytest internals are pinned by uv.lock; absence fails the gate.
    manager = item.session._fixturemanager
    for name in manager._arg2fixturedefs:
        definitions = manager.getfixturedefs(name, item) or ()
        for definition in definitions:
            if name not in item._fixtureinfo.name2fixturedefs:
                source = inspect.getsourcefile(definition.func)
                try:
                    relative = Path(source).resolve().relative_to(Path.cwd().resolve()) if source else None
                except ValueError:
                    relative = None
                if relative is None or set(relative.parts) & {".venv", "site-packages", "dist-packages"}:
                    continue
            scope = overrides.get(name, definition.scope)
            validate_split_fixture(item, name, definition, getattr(scope, "value", scope))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def identity():
    def git(*args):
        return subprocess.check_output(["git", *args]).decode().strip()
    # A local source snapshot must include new files, not just tracked diffs.
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"]
    ).decode().split("\0")
    source_roots = {"src", "tests", "scripts", "web", "desktop"}
    sources = {}
    for name in untracked:
        path = Path(name)
        if not name or name.startswith("desktop/ci-evidence/"):
            continue
        if (path.parts[0] in source_roots or
                name in {"pyproject.toml", "pytest.ini", "conftest.py", "uv.lock"}):
            sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "untracked_sources": digest(sources),
        "commit": git("rev-parse", "HEAD"),
        "diff": digest(git("diff", "--binary", "HEAD")),
        "lock": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "run": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
    }


def load_duration_history():
    """Read a reviewed scheduling input, never prior pass/fail evidence."""
    raw = DURATION_HISTORY_PATH.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != DURATION_HISTORY_SHA256:
        raise ValueError("Frozen duration history digest mismatch")
    history = json.loads(raw)
    if (set(history) != {"version", "source", "unit", "fallback_ms", "duration_ms",
                         "historical_skips"}
            or type(history["version"]) is not int or history["version"] != 1
            or history["unit"] != "ceil_total_phase_milliseconds"):
        raise ValueError("Unsupported duration history")
    costs, skipped = history["duration_ms"], history["historical_skips"]
    if (not isinstance(costs, dict) or not costs or not isinstance(skipped, dict)
            or any(not isinstance(node, str) or "::" not in node for node in costs | skipped)
            or any(type(cost) is not int or cost < 1 for cost in costs.values())
            or any(not isinstance(reason, str) or not reason for reason in skipped.values())
            or set(costs) & set(skipped)
            or type(history["fallback_ms"]) is not int
            or history["fallback_ms"] != max(costs.values())
            or digest(sorted(costs | skipped)) != history["source"]["nodes_sha256"]):
        raise ValueError("Invalid duration history costs or coverage")
    return history


def partition_policy(history=None):
    history = load_duration_history() if history is None else history
    return {"strategy": PARTITION_STRATEGY, "history_version": history["version"],
            "history_sha256": DURATION_HISTORY_SHA256, "fallback_ms": history["fallback_ms"],
            "fallback_strategy": "file-passed-max-then-global-max-v1"}


def estimated_costs(nodes, history):
    """Scheduling estimates only; historical skips never count as passed bodies."""
    by_file = {}
    for node, cost in history["duration_ms"].items():
        filename = node.split("::", 1)[0]
        by_file[filename] = max(by_file.get(filename, 0), cost)
    return {node: history["duration_ms"].get(
        node, by_file.get(node.split("::", 1)[0], history["fallback_ms"])) for node in nodes}


def partition(nodes, count):
    return _partition(nodes, count, load_duration_history())


def _partition(nodes, count, history):
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("Empty or duplicate full collection")
    if not 1 <= count <= len(nodes):
        raise ValueError("Shard count must be between one and the test count")
    # Preserve module fixture lifecycles except the explicitly reviewed file.
    # Missing/skipped nodes use the file's maximum complete passed history, or
    # the global maximum for new/all-skipped files. These are not pass evidence.
    units = {}
    for node in sorted(nodes):
        filename = node.split("::", 1)[0]
        unit = node if filename in NODE_SPLIT_FILES else filename
        units.setdefault(unit, []).append(node)
    if count > len(units):
        raise ValueError("Shard count exceeds test files; reduce count to preserve fixture scope")
    estimates = estimated_costs(nodes, history)
    costs = {unit: sum(estimates[node] for node in members) for unit, members in units.items()}
    shards = [[] for _ in range(count)]
    loads = [0] * count
    # LPT and all ties are deterministic; input collection order is irrelevant.
    for unit in sorted(units, key=lambda name: (-costs[name], name)):
        index = min(range(count), key=lambda i: (loads[i], len(shards[i]), i))
        shards[index].extend(units[unit])
        loads[index] += costs[unit]
    return [sorted(shard) for shard in shards]


def validate_manifest(manifest):
    if manifest["version"] != MANIFEST_VERSION:
        raise ValueError("Unsupported manifest")
    history = load_duration_history()
    if manifest.get("partition") != partition_policy(history):
        raise ValueError("Manifest duration policy differs from the frozen input")
    if manifest["shards"] != _partition(manifest["nodes"], len(manifest["shards"]), history):
        raise ValueError("Manifest partition is incomplete or not deterministic")
    if set(manifest["allowed_skips"] or {}) - set(manifest["nodes"]):
        raise ValueError("Skip policy contains unknown node IDs")


def validate_result(manifest, result, index):
    expected = manifest["shards"][index]
    if (result["manifest"] != digest(manifest) or result["index"] != index
            or result["identity"] != manifest["identity"] or result["exit_code"] != 0):
        raise ValueError(f"Shard {index}: unsuccessful or mismatched evidence")
    if result["selected"] != expected:
        raise ValueError(f"Shard {index}: selection differs from manifest")
    reports = result["reports"]
    if set(reports) != set(expected):
        raise ValueError(f"Shard {index}: missing or unexpected executed tests")
    for node, phases in reports.items():
        counts = Counter(phase["when"] for phase in phases)
        if (counts["setup"] != 1 or counts["teardown"] != 1 or counts["call"] > 1
                or set(counts) - {"setup", "call", "teardown"}):
            raise ValueError(f"{node}: missing or duplicate test phases")
        skips = [phase for phase in phases if phase["outcome"] == "skipped"]
        if skips:
            if (len(skips) != 1 or not skips[0]["reason"]
                    or (manifest["allowed_skips"] is not None
                        and skips[0]["reason"] != manifest["allowed_skips"].get(node))
                    or skips[0]["when"] not in {"setup", "call"}):
                raise ValueError(f"{node}: unexpected skip")
            if counts["call"] != (0 if skips[0]["when"] == "setup" else 1):
                raise ValueError(f"{node}: invalid skipped execution")
        elif counts["call"] != 1:
            raise ValueError(f"{node}: test body did not execute")
        if any(p["outcome"] not in {"passed", "skipped"} or p["xfail"] for p in phases):
            raise ValueError(f"{node}: failure, error, or xfail is not a pass")


def aggregate(manifest, directory):
    validate_manifest(manifest)
    paths = sorted(Path(directory).glob("shard-*.json"))
    expected = {f"shard-{i}.json" for i in range(len(manifest["shards"]))}
    if {path.name for path in paths} != expected:
        raise ValueError("Missing or unexpected shard evidence files")
    executed = []
    skipped = []
    for index in range(len(manifest["shards"])):
        result = read(Path(directory) / f"shard-{index}.json")
        validate_result(manifest, result, index)
        executed.extend(result["reports"])
        skipped.extend({"node": node, "reason": phase["reason"]}
                       for node, phases in result["reports"].items()
                       for phase in phases if phase["outcome"] == "skipped")
    if Counter(executed) != Counter(manifest["nodes"]):
        raise ValueError("Full execution has missing or duplicate node IDs")
    return {"tests": len(executed), "passed": len(executed) - len(skipped),
            "skipped": skipped, "collection_skips": manifest.get("collection_skips", []),
            "manifest": digest(manifest)}


def worker(args):
    import pytest

    manifest = read(args.manifest) if args.command == "_run" else None
    if manifest:
        validate_manifest(manifest)
        if identity() != manifest["identity"]:
            raise ValueError("Manifest belongs to another source/environment/run")
    nodes, selected, reports, collection_skips = [], [], {}, []

    class Evidence:
        def pytest_fixture_setup(self, fixturedef, request):
            # getfixturevalue() can acquire fixtures absent from static closure.
            # Runtime effective scope also covers indirect parametrization.
            validate_split_fixture(request._pyfuncitem, fixturedef.argname,
                                   fixturedef, request.scope)

        def pytest_collectreport(self, report):
            if report.skipped:
                collection_skips.append({"node": report.nodeid, "reason": str(report.longrepr)})

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, session, config, items):
            for item in items:
                try:
                    validate_split_fixture_scope(item)
                except ValueError as exc:
                    raise pytest.UsageError(str(exc)) from exc
            nodes.extend(item.nodeid for item in items)
            if manifest:
                if sorted(nodes) != manifest["nodes"]:
                    raise pytest.UsageError("Full collection differs from manifest")
                wanted = set(manifest["shards"][args.index])
                removed = [item for item in items if item.nodeid not in wanted]
                items[:] = [item for item in items if item.nodeid in wanted]
                config.hook.pytest_deselected(items=removed)
                selected.extend(sorted(item.nodeid for item in items))

        def pytest_runtest_logreport(self, report):
            reason = ""
            if report.skipped and isinstance(report.longrepr, tuple):
                reason = str(report.longrepr[2]).removeprefix("Skipped: ")
            reports.setdefault(report.nodeid, []).append({
                "when": report.when, "outcome": report.outcome, "reason": reason,
                "xfail": hasattr(report, "wasxfail"),
                # Diagnostic only: preserve real phase costs for later balancing.
                # Timing never changes pass/skip/failure or exact-node validation.
                "duration_seconds": report.duration,
            })

    options = ["-q", "--durations=20"]
    if manifest is None:
        options += ["--collect-only"]
    code = int(pytest.main(options, plugins=[Evidence()]))
    if manifest is None:
        if code:
            return code
        policy = read(args.allowed_skips) if args.allowed_skips else None
        manifest = {"version": MANIFEST_VERSION, "partition": partition_policy(),
                    "identity": identity(), "nodes": sorted(nodes),
                    "shards": partition(nodes, args.count), "allowed_skips": policy,
                    "collection_skips": collection_skips}
        validate_manifest(manifest)
        write(args.manifest, manifest)
    else:
        if collection_skips != manifest.get("collection_skips", []):
            raise ValueError("Collection skip scope/reasons differ from manifest")
        result = {"identity": identity(), "manifest": digest(manifest), "index": args.index,
                  "exit_code": code, "selected": selected, "reports": reports}
        write(Path(args.output) / f"shard-{args.index}.json", result)
        validate_result(manifest, result, args.index)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "run", "aggregate", "_collect", "_run"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--output", default="pytest-evidence")
    parser.add_argument("--allowed-skips", help="Reviewed JSON mapping exact node ID to skip reason")
    args = parser.parse_args()
    if args.command in {"collect", "run"}:
        # A fresh process per shard; no PYTEST_ADDOPTS/PYTEST_PLUGINS mutation.
        if args.command == "collect":
            Path(args.manifest).unlink(missing_ok=True)
        else:
            (Path(args.output) / f"shard-{args.index}.json").unlink(missing_ok=True)
        return subprocess.call([sys.executable, str(Path(__file__).resolve()),
                                "_" + args.command, *sys.argv[2:]])
    if args.command == "aggregate":
        manifest = read(args.manifest)
        if manifest["identity"] != identity():
            raise ValueError("Evidence belongs to another source/environment/run")
        print(json.dumps(aggregate(manifest, args.output), indent=2))
        return 0
    return worker(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, IndexError) as exc:
        print(f"Full pytest gate failed: {exc}", file=sys.stderr)
        sys.exit(1)
