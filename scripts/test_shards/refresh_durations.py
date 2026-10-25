"""Offline, reproducible scheduling refresh; never imports pass/skip gate evidence.

Usage: python scripts/test_shards/refresh_durations.py ROUND1_DIR ROUND2_DIR OUTPUT
Directories contain the reviewed aggregate artifacts' manifest/shard JSON files.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

import gate

BASELINE_SHA256 = "ced3340e064ca8a5fffb1621d2b4a07593f9dd1095c2595d02873c78ff62fe77"
SOURCES = (
    {"run_id": "40059938410", "artifact_id": 12556478824,
     "artifact_sha256": "9b2007beee66f412dc3da75540e4f34b29503631ed72c7f235d25bb3d9e6fb13"},
    {"run_id": "40085762532", "artifact_id": 12567269333,
     "artifact_sha256": "1c707cab7b97dd2660ed682e9ff16f5a60feb2ce64bb81b6c1963f5f668466bb"},
)


def file_digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def complete_passed_ms(phases):
    if (not isinstance(phases, list) or len(phases) != 3
            or Counter(p.get("when") for p in phases) != Counter(("setup", "call", "teardown"))
            or any(p.get("outcome") != "passed" or p.get("xfail") is not False for p in phases)):
        return None
    durations = [p.get("duration_seconds") for p in phases]
    if any(type(d) not in (int, float) or not math.isfinite(d) or d < 0 for d in durations):
        return None
    return max(1, math.ceil(sum(durations) * 1000))


def refresh(baseline, directories, sources=SOURCES):
    if len(directories) != len(sources):
        raise ValueError("Each reviewed source requires exactly one evidence directory")
    costs = dict(baseline["duration_ms"])
    observations = {}
    provenance = []
    for directory, source in zip(directories, sources, strict=True):
        directory = Path(directory)
        manifest_path = directory / "manifest.json"
        manifest = gate.read(manifest_path)
        identity = manifest["identity"]
        nodes, shards = manifest["nodes"], manifest["shards"]
        if (identity["platform"] != "Windows" or identity["run"] != source["run_id"]
                or manifest["version"] != 4 or len(shards) != 20
                or len(nodes) != len(set(nodes)) or sorted(sum(shards, [])) != sorted(nodes)
                or manifest["partition"]["history_sha256"] != BASELINE_SHA256
                or shards != gate._partition(nodes, 20, baseline)):
            raise ValueError("Unreviewed or inconsistent Windows source manifest")
        paths = sorted(directory.glob("shard-*.json"), key=lambda p: int(p.stem.split("-")[-1]))
        if {p.name for p in paths} != {f"shard-{i}.json" for i in range(20)}:
            raise ValueError("Missing source shard artifact")
        run = {**source, "identity": identity, "manifest_sha256": gate.digest(manifest),
               "manifest_file_sha256": file_digest(manifest_path), "manifest_nodes": len(nodes),
               "nodes_sha256": gate.digest(sorted(nodes)), "shards": []}
        for index, path in enumerate(paths):
            result = gate.read(path)
            if (result["identity"] != identity or result["manifest"] != gate.digest(manifest)
                    or result["index"] != index or result["selected"] != shards[index]
                    or set(result["reports"]) - set(shards[index])):
                raise ValueError("Source shard identity, selection or report mismatch")
            accepted = 0
            for node, phases in sorted(result["reports"].items()):
                milliseconds = complete_passed_ms(phases)
                if milliseconds is None:
                    continue
                if node in baseline["historical_skips"]:
                    raise ValueError("Historical skip changed; separate explicit review required")
                accepted += 1
                observations.setdefault(node, []).append({
                    "run_id": source["run_id"], "shard": index, "observed_ms": milliseconds,
                })
                costs[node] = max(costs.get(node, 0), milliseconds)
            run["shards"].append({"shard": index, "result_sha256": file_digest(path),
                                  "exit_code": result["exit_code"], "selected_nodes": len(shards[index]),
                                  "complete_passed_nodes": accepted,
                                  "excluded_or_missing_nodes": len(shards[index]) - accepted})
        provenance.append(run)
    audits = {node: {"previous_ms": baseline["duration_ms"].get(node),
                    "observations": values, "effective_ms": costs[node]}
              for node, values in sorted(observations.items())}
    skipped = copy.deepcopy(baseline["historical_skips"])
    return {"version": 2, "source": {
        "previous_history_sha256": BASELINE_SHA256,
        "policy": "max-old-and-complete-passed-total-phases-v1", "runs": provenance,
        "node_observations": audits, "nodes_sha256": gate.digest(sorted(costs | skipped)),
        "added_nodes": len(set(costs) - set(baseline["duration_ms"])),
        "raised_nodes": sum(costs[node] > value for node, value in baseline["duration_ms"].items()),
        "scope": "scheduling-only; not completed execution evidence",
    }, "unit": baseline["unit"], "fallback_ms": max(costs.values()),
        "historical_skips": skipped, "duration_ms": dict(sorted(costs.items()))}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    baseline_path = Path(__file__).with_name("durations-v1.json")
    if file_digest(baseline_path) != BASELINE_SHA256:
        sys.exit("Frozen v1 history changed")
    result = refresh(gate.read(baseline_path), sys.argv[1:3])
    gate.write(sys.argv[3], result)
    print(json.dumps({"sha256": file_digest(Path(sys.argv[3])),
                      "added": result["source"]["added_nodes"],
                      "raised": result["source"]["raised_nodes"],
                      "fallback_ms": result["fallback_ms"]}))
