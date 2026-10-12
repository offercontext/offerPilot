from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from offerpilot.evaluation.contracts import EvidenceBundle
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

FRAGMENTS = ("initial", "milestones", "final", "deltas", "markers", "approval", "facts",
             "http", "calls", "faults", "coverage")


def bind_fragments(evidence: EvidenceBundle) -> EvidenceBundle:
    raw = evidence.model_dump(mode="json")
    return evidence.model_copy(update={"fragment_hashes": {k: digest(raw[k]) for k in FRAGMENTS}})


def seal(private: Path, evidence: EvidenceBundle) -> EvidenceBundle:
    evidence = bind_fragments(evidence)
    raw = evidence.model_dump(mode="json")
    index: dict[str, Any] = {"fragment_hashes": raw["fragment_hashes"],
                            "fragment_errors": raw["fragment_errors"], "files": {}}
    for key in FRAGMENTS:
        path = private / f"fragment-{key}.json"
        atomic_json(path, {"sha256": digest(raw[key]), "value": raw[key]})
        index["files"][key] = {"name": path.name, "sha256": file_hash(path),
                                "bytes": path.stat().st_size}
    atomic_json(private / "evidence-index.json", index)
    atomic_json(private / "evidence.json", evidence.model_dump(mode="json"))
    return evidence


def load(private: Path) -> EvidenceBundle:
    index_error = None
    try:
        index = json.loads((private / "evidence-index.json").read_bytes())
        if set(index) != {"fragment_hashes", "fragment_errors", "files"}:
            raise ValueError("invalid evidence index")
    except (OSError, ValueError, TypeError) as exc:
        index_error = type(exc).__name__
        index = {"fragment_hashes": {}, "fragment_errors": {"index": index_error}, "files": {}}
    raw: dict[str, Any] = {"fragment_hashes": index["fragment_hashes"],
                           "fragment_errors": dict(index["fragment_errors"]), "files": index["files"]}
    for key in FRAGMENTS:
        try:
            entry = index["files"].get(key, {"name": f"fragment-{key}.json"})
            path = private / entry["name"]
            if path.name != f"fragment-{key}.json" or path.resolve().parent != private.resolve():
                raise ValueError("fragment path escaped private directory")
            if not index_error and (file_hash(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]):
                raise ValueError("fragment checksum mismatch")
            envelope = json.loads(path.read_bytes())
            value = envelope["value"]
            if digest(value) != envelope["sha256"] or (not index_error and digest(value) != index["fragment_hashes"][key]):
                raise ValueError("fragment binding mismatch")
            if index_error:
                raw["fragment_hashes"][key] = envelope["sha256"]
            # Validate fragments separately so a broken control log never hides
            # intact, independently sealed business deltas.
            EvidenceBundle.model_validate({key: value})
            raw[key] = value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raw["fragment_errors"][key] = type(exc).__name__
    return EvidenceBundle.model_validate(raw)
