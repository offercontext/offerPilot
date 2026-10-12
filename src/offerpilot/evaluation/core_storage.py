from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json

FRAGMENTS = (
    "initial",
    "observed",
    "milestones",
    "facts",
    "http",
    "calls",
    "faults",
    "provider_attempts",
    "input_observations",
    "budget_events",
    "manual_review",
    "files",
)


def seal(private: Path, evidence: dict[str, Any]) -> str:
    fragments = {}
    for key in FRAGMENTS:
        value = evidence.get(
            key, {} if key in {"initial", "observed", "milestones", "facts", "files"} else []
        )
        path = private / f"fragment-{key}.json"
        atomic_json(path, {"value": value, "sha256": digest(value)})
        fragments[key] = {"name": path.name, "sha256": file_hash(path)}
    index = {"schema_version": 2, "trial_key": evidence["trial_key"], "fragments": fragments}
    atomic_json(private / "evidence-index.json", index)
    return digest(index)


def load(private: Path, expected_hash: str | None = None) -> dict[str, Any]:
    errors = {}
    try:
        index = json.loads((private / "evidence-index.json").read_bytes())
        if (
            set(index) != {"schema_version", "trial_key", "fragments"}
            or index["schema_version"] != 2
            or set(index["fragments"]) != set(FRAGMENTS)
            or (expected_hash and digest(index) != expected_hash)
        ):
            raise ValueError("invalid index")
    except (OSError, ValueError, KeyError, TypeError):
        index = {"trial_key": None, "fragments": {}}
        errors["index"] = "invalid"
    raw: dict[str, Any] = {"trial_key": index["trial_key"], "errors": errors}
    for key in FRAGMENTS:
        try:
            path = private / f"fragment-{key}.json"
            if not path.resolve().is_relative_to(private.resolve()) or path.is_symlink():
                raise ValueError("escaped fragment")
            envelope = json.loads(path.read_bytes())
            value = envelope["value"]
            if set(envelope) != {"value", "sha256"} or digest(value) != envelope["sha256"]:
                raise ValueError("corrupt fragment")
            if "index" not in errors and index["fragments"][key] != {
                "name": path.name,
                "sha256": file_hash(path),
            }:
                raise ValueError("unbound fragment")
            if not isinstance(
                value,
                dict if key in {"initial", "observed", "milestones", "facts", "files"} else list,
            ):
                raise ValueError("wrong fragment type")
            raw[key] = value
        except (OSError, ValueError, TypeError, KeyError):
            errors[key] = "invalid"
    for name, record in raw.get("files", {}).items():
        try:
            path = private / name
            if (
                path.name != name
                or path.is_symlink()
                or not path.resolve().is_relative_to(private.resolve())
                or file_hash(path) != record["sha256"]
            ):
                raise ValueError("private file corrupt")
        except (OSError, ValueError, KeyError, TypeError):
            errors["file:" + name] = "invalid"
    return raw
