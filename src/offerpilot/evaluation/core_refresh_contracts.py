"""Explicit local attestations for evidence refresh; not human authentication."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Annotated, Any, Literal, cast
import re

from pydantic import Field

from offerpilot.evaluation.contracts import Contract, Hash
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.registry import file_hash

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def identifier(value: str) -> str:
    require(
        bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value)),
        "invalid generation identifier",
    )
    return value


class BoundFile(Contract):
    path: str
    sha256: Hash


def reference(path: Path) -> dict[str, str]:
    path = safe_path(path.absolute())
    require(path.is_file(), "refresh reference unavailable")
    return dict(path=str(path), sha256=file_hash(path))


def bound_file(value: dict[str, Any] | BoundFile) -> Path:
    ref = value if isinstance(value, BoundFile) else BoundFile.model_validate(value)
    path = Path(ref.path)
    require(path.is_absolute(), "refresh reference must be absolute")
    path = safe_path(path)
    require(path.is_file() and file_hash(path) == ref.sha256, "refresh reference changed")
    return path


def read(path: Path) -> dict[str, Any]:
    value = json.loads(safe_path(path.absolute()).read_bytes())
    require(isinstance(value, dict), "refresh object required")
    return cast(dict[str, Any], value)


def valid_at(created: datetime, expires: datetime, at: datetime, max_days: int = 30) -> bool:
    return bool(
        created.tzinfo
        and expires.tzinfo
        and at.tzinfo
        and created <= at < expires
        and 0 < (expires - created).total_seconds() <= max_days * 86400
    )


class RefreshApproval(Contract):
    schema_version: Literal[3] = 3
    kind: Literal["evaluator-refresh-approval"] = "evaluator-refresh-approval"
    baseline_id: Identifier
    generation_id: Identifier
    source_variant: Literal["baseline"] = "baseline"
    request_hash: Hash
    expected_current: Identifier
    expected_previous_generation: Identifier | None
    reviewer: str = Field(min_length=1)
    role: Literal["maintainer"]
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash


class RefreshRevocation(Contract):
    schema_version: Literal[3] = 3
    kind: Literal["evaluator-refresh-revocation"] = "evaluator-refresh-revocation"
    baseline_id: Identifier
    generation_id: Identifier
    request_hash: Hash
    expected_current: Identifier
    expected_previous_generation: Identifier | None
    reviewer: str = Field(min_length=1)
    role: Literal["maintainer"]
    reason: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime
    record_ref: str
    record_hash: Hash


def maintainer_record(value: RefreshApproval | RefreshRevocation, at: datetime) -> None:
    require(valid_at(value.created_at, value.expires_at, at), "refresh maintainer record stale")
    payload = value.model_dump(mode="json", exclude={"record_ref", "record_hash"})
    actual = read(bound_file(dict(path=value.record_ref, sha256=value.record_hash)))
    require(
        actual == dict(schema_version=3, kind="maintainer-source-record", record=payload),
        "refresh requires the exact frozen maintainer source record",
    )


class RefreshIndependentRecord(Contract):
    schema_version: Literal[3] = 3
    kind: Literal["evaluator-refresh-independent-record"] = "evaluator-refresh-independent-record"
    reviewer: str = Field(min_length=1)
    role: Literal["independent_reviewer"] = "independent_reviewer"
    reviewed_variant: Literal["baseline"] = "baseline"
    baseline_snapshot_hash: Hash
    candidate_snapshot_hash: Hash
    evaluator_hash: Hash
    run_manifest_hash: Hash
    evidence_fingerprint: Hash
    disposition: Literal["accepted", "changes_required"]
    created_at: datetime
    expires_at: datetime
    notes: str = Field(min_length=1)


class GenerationRecord(Contract):
    schema_version: Literal[3] = 3
    kind: Literal["evaluator_evidence_refresh"] = "evaluator_evidence_refresh"
    baseline_id: Identifier
    generation_id: Identifier
    source_variant: Literal["baseline"] = "baseline"
    original_record_digest: Hash
    expected_current: Identifier
    expected_previous_generation: Identifier | None
    created_at: datetime
    request: BoundFile
    request_hash: Hash
    approval_file: BoundFile
    approval: RefreshApproval
