"""Offline, reproducible scheduling refresh; never imports pass/skip gate evidence.

Usage: python scripts/test_shards/refresh_durations.py ROUND1_DIR ROUND2_DIR OUTPUT
V3: python scripts/test_shards/refresh_durations.py --v3 VERIFIED_ARTIFACT_ZIP OUTPUT
Directories contain the reviewed aggregate artifacts' manifest/shard JSON files.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import zipfile
import struct
import zlib

import gate

BASELINE_SHA256 = "ced3340e064ca8a5fffb1621d2b4a07593f9dd1095c2595d02873c78ff62fe77"
SOURCES = (
    {"run_id": "40059938410", "artifact_id": 12556478824,
     "artifact_sha256": "9b2007beee66f412dc3da75540e4f34b29503631ed72c7f235d25bb3d9e6fb13"},
    {"run_id": "40085762532", "artifact_id": 12567269333,
     "artifact_sha256": "1c707cab7b97dd2660ed682e9ff16f5a60feb2ce64bb81b6c1963f5f668466bb"},
)


V3_ENCODER_ZLIB_VERSION = "1.3.2"
V3_BASELINE_SHA256 = "8d8ae33673e05619c70e69846c3bdb4014e5fe38e4ed6d774fe4b110744324d9"
V3_SOURCE = {
    "run_id": "41788302769", "artifact_id": 13315112535,
    "artifact_sha256": "0abdac6c2b32d9b239dac16b232424b85cf927b700f809f20b910dced1f672f2",
    "manifest_sha256": "319240642bb08091e77ee779334ce9a040b0e0a65e52775c51d52fe4afa8a2ef",
    "manifest_file_sha256": "c7db3b25d2174576536ea04f57d408fe7d9f763b543f28b38781f0ee3aa3d2b1",
}


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


def refresh(baseline, directories, sources=SOURCES, *,
            baseline_sha256=BASELINE_SHA256, history_version=2):
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
                or manifest["partition"]["history_sha256"] != baseline_sha256
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
    return {"version": history_version, "source": {
        "previous_history_sha256": baseline_sha256,
        "policy": "max-old-and-complete-passed-total-phases-v1", "runs": provenance,
        "node_observations": audits, "nodes_sha256": gate.digest(sorted(costs | skipped)),
        "added_nodes": len(set(costs) - set(baseline["duration_ms"])),
        "raised_nodes": sum(costs[node] > value for node, value in baseline["duration_ms"].items()),
        "scope": "scheduling-only; not completed execution evidence",
    }, "unit": baseline["unit"], "fallback_ms": max(costs.values()),
        "historical_skips": skipped, "duration_ms": dict(sorted(costs.items()))}


def refresh_v3(archive_path):
    """Reproduce v3 from one pinned, complete successful Windows artifact.

    Historical timings affect scheduling only. A new partition never inherits
    this run's passing evidence. Keep the legacy v2 refresh reproducible above.
    """
    baseline_path = Path(__file__).with_name("durations-v2.json")
    if file_digest(baseline_path) != V3_BASELINE_SHA256:
        raise ValueError("Frozen v2 history changed")
    raw = Path(archive_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != V3_SOURCE["artifact_sha256"]:
        raise ValueError("Unreviewed v3 artifact digest")
    baseline = gate.read(baseline_path)
    expected = {"manifest.json"} | {f"shard-{i}.json" for i in range(20)}
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            evidence_names = [n for n in names if n.startswith("pytest-evidence/") and not n.endswith("/")]
            if (len(evidence_names) != len(expected)
                    or set(evidence_names) != {f"pytest-evidence/{n}" for n in expected}):
                raise ValueError("Missing, duplicate or unexpected v3 evidence files")
            # Never extract arbitrary archive paths or trust symlinks.
            for name in sorted(expected):
                (directory / name).write_bytes(archive.read(f"pytest-evidence/{name}"))
        manifest = gate.read(directory / "manifest.json")
        if (gate.digest(manifest) != V3_SOURCE["manifest_sha256"]
                or file_digest(directory / "manifest.json") != V3_SOURCE["manifest_file_sha256"]):
            raise ValueError("Unreviewed v3 manifest identity or policy")
        executed, skipped = [], []
        for index in range(20):
            result = gate.read(directory / f"shard-{index}.json")
            gate.validate_result(manifest, result, index)
            executed.extend(result["reports"])
            for node, phases in result["reports"].items():
                has_skip = any(p["outcome"] == "skipped" for p in phases)
                if not has_skip and complete_passed_ms(phases) is None:
                    raise ValueError("Incomplete v3 passed duration measurement")
                skipped.extend({"node": node, "reason": p["reason"]}
                               for p in phases if p["outcome"] == "skipped")
        if Counter(executed) != Counter(manifest["nodes"]):
            raise ValueError("Incomplete or duplicate v3 full execution")
        history = refresh(baseline, [directory], [V3_SOURCE],
                          baseline_sha256=V3_BASELINE_SHA256, history_version=3)
        history["source"]["execution_audit"] = {
            "passed": len(executed) - len(skipped), "skipped": sorted(skipped, key=lambda p: p["node"]),
            "allowed_skips": manifest["allowed_skips"],
            "collection_skips": manifest.get("collection_skips", []),
        }
        history["source"]["estimate_scope"] = (
            "Retrospective same-run scheduling observations; no guaranteed future speedup."
        )
        return history


def canonical_history_gzip(raw):
    """Fixed gzip envelope; DEFLATE bytes still depend on the zlib encoder."""
    compressor = zlib.compressobj(level=9, wbits=-15)
    payload = compressor.compress(raw) + compressor.flush()
    return (gate.DURATION_HISTORY_GZIP_HEADER + payload
            + struct.pack("<II", zlib.crc32(raw), len(raw) & 0xffffffff))


def write_v3_compressed(path, history):
    """Pinned encoder and dual digests: fail rather than emit a new encoding.

    Readers do not require this encoder version. Regeneration does; fixed OS
    and mtime fields alone cannot guarantee identical DEFLATE across versions.
    """
    if zlib.ZLIB_RUNTIME_VERSION != V3_ENCODER_ZLIB_VERSION:
        raise ValueError(f"V3 regeneration requires zlib {V3_ENCODER_ZLIB_VERSION}")
    raw = json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")
    if (len(raw) > gate.DURATION_HISTORY_MAX_RAW_BYTES
            or hashlib.sha256(raw).hexdigest() != gate.DURATION_HISTORY_SHA256):
        raise ValueError("Regenerated v3 raw history size or digest mismatch")
    packed = canonical_history_gzip(raw)
    if (len(packed) > gate.DURATION_HISTORY_MAX_COMPRESSED_BYTES
            or hashlib.sha256(packed).hexdigest() != gate.DURATION_HISTORY_COMPRESSED_SHA256):
        raise ValueError("Regenerated v3 compressed history size or digest mismatch")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(packed)
    temporary.replace(path)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--v3":
        result = refresh_v3(sys.argv[2])
        write_v3_compressed(sys.argv[3], result)
        sys.exit(0)
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
