"""Frozen rehearsal policy. No production registration or execution authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

SOURCE_SHA = "8848ac33623167cb2c2a58b1e0122abe825810dc"
SOURCE_TREE = "f3a61ed62f7b148e3eea2daead790b260eae6dfe"
REPOSITORY_ID = 241031113
CONTROL_REF = "refs/heads/ai-validation-control"
OWNER_ID = 112178718
CAMPAIGN = "one-shot-20261021"
ORDINAL = 4
PRIOR_CALLS = 48
PRIOR_SPEND = 411934
CAP = 20000000
RESERVATION = 3000000
DEADLINE = 1793145600
WINDOW = 1200
CONCURRENCY = "offerpilot-companion-ai-20261021-one-shot"
REQUEST_HASHES = (
    "c41fcc6094892a4fc0531ac99e43bcd5845cb79c51424f90a87af1e10e4100e0",
    "8a1cb62a6b0eeb71800a7301ebf38ba95f6a63e3b085cb6cc489b29fb32f74ec",
    "bdf3610e090a9defa725570a9528b849139c49d6b1a436fdf25896452d0e78b2",
    "25d7c2475fd86d2f8175d637eb5e82085520c785a9f5a92104ccc02224d84f42",
    "df2660209f2e54138a27a467d44ac8d0826798c8963a47c663914955328b0ec8",
    "8dadb05cb62fd5e14f766c074cdca7ec63be641a49794ffb153bc67840cf02d4",
)
OPERATIONS = tuple(f"s{i:02d}" for i in range(1, 7))


class Blocked(Exception):
    """Only fixed diagnostic codes, never untrusted values."""


def demand(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


MANIFEST = {"source_sha": SOURCE_SHA, "source_tree": SOURCE_TREE,
            "operations": [{"id": op, "sha256": sha}
                           for op, sha in zip(OPERATIONS, REQUEST_HASHES, strict=True)]}
MANIFEST_SHA256 = hashlib.sha256(canonical(MANIFEST)).hexdigest()
BATCH_KEY = hashlib.sha256(canonical([CAMPAIGN, ORDINAL, list(REQUEST_HASHES)])).hexdigest()


@dataclass(frozen=True)
class Registration:
    controller_sha: str
    workflow_id: int
    dry_run_workflow_id: int
    environment_id: int
    rehearsal: bool = True


PRODUCTION_REGISTRATION: Registration | None = None


def fixture_registration() -> Registration:
    """Deliberately synthetic identities, never real platform registration."""
    return Registration("a" * 40, 900001, 900002, 900003)


def frozen_requests() -> tuple[bytes, ...]:
    """Read only controller-tree bundled data; no caller-controlled path."""
    root = Path(__file__).resolve().parents[1] / "companion_ai_validation/shadow_cases/requests"
    values = []
    for op, digest in zip(OPERATIONS, REQUEST_HASHES, strict=True):
        path = root / f"{op}.json"
        demand(path.is_file() and not path.is_symlink(), "FROZEN_DATA_MISSING")
        raw = path.read_bytes()
        demand(0 < len(raw) < 65536 and hashlib.sha256(raw).hexdigest() == digest,
               "FROZEN_DATA_CHANGED")
        values.append(raw)
    return tuple(values)
