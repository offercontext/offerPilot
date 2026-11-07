"""Read-only imports of independent checks; never create Harness trial results."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from offerpilot.evaluation.contracts import Contract, Hash
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import snapshot


class ExternalReference(Contract):
    path: str = Field(min_length=1)
    sha256: Hash


class ExternalCodeSnapshot(Contract):
    checkout: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_hash: Hash
    lock_hash: Hash
    files_hash: Hash


class ExternalModelSnapshot(Contract):
    profile_hash: Hash
    profile: ExternalReference
    actual_models: list[str] = Field(min_length=1)
    evidence: list[ExternalReference] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> ExternalModelSnapshot:
        if len(set(self.actual_models)) != len(self.actual_models) or any(
            not m or len(m) > 128 for m in self.actual_models
        ):
            raise ValueError("invalid external model identities")
        if len({Path(r.path).resolve() for r in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate external model evidence")
        return self


class ExternalCheckReceipt(Contract):
    schema_version: Literal[2] = 2
    kind: Literal["external-check-receipt"] = "external-check-receipt"
    check_id: UUID
    check: Literal["oc-verify", "knowledge-acceptance"]
    command: list[str] = Field(min_length=2)
    exit_code: int = Field(strict=True, ge=-(2**31), le=2**31 - 1)
    execution_complete: bool = Field(strict=True)
    started_at: datetime
    finished_at: datetime
    mode: Literal["deterministic", "real"]
    report: ExternalReference
    report_capture: Literal["stdout", "file"]
    code: ExternalCodeSnapshot
    data_definition: str | None = None
    data_files: list[ExternalReference] = Field(default_factory=list)
    data_manifest: ExternalReference | None = None
    model: ExternalModelSnapshot | None = None
    metric_definitions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def declared_scope(self) -> ExternalCheckReceipt:
        if (
            self.started_at.tzinfo is None
            or self.finished_at.tzinfo is None
            or self.finished_at < self.started_at
            or self.finished_at > datetime.now(timezone.utc)
        ):
            raise ValueError("external execution timestamps must be ordered and aware")
        argv = self.command[2:] if self.command[:2] == ["uv", "run"] else self.command
        if argv[:2] != ["oc", "verify" if self.check == "oc-verify" else self.check]:
            raise ValueError("unregistered external command")
        options = (
            {"--profile", "--static-dir"}
            if self.check == "oc-verify"
            else {"--profile", "--fixtures-dir", "--report", "--real-ai"}
        )
        values: dict[str, str] = {}
        i = 2
        while i < len(argv):
            option = argv[i]
            if option not in options or option in values:
                raise ValueError("external command options must be explicit and unique")
            if option == "--real-ai":
                values[option] = "true"
                i += 1
            else:
                if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                    raise ValueError("missing external command option value")
                values[option] = argv[i + 1]
                i += 2
        profile = values.get("--profile", "local" if self.check == "oc-verify" else "v1")
        real = profile == "real-ai" if self.check == "oc-verify" else "--real-ai" in values
        if profile not in ({"local", "real-ai"} if self.check == "oc-verify" else {"v1", "brief"}):
            raise ValueError("unknown external profile")
        if real != (self.mode == "real") or self.mode == "deterministic" and self.model:
            raise ValueError("external mode/model contradicts command")
        if self.check == "knowledge-acceptance":
            if profile == "v1" and real:
                raise ValueError("real AI only supported in brief profile")
            if (
                "--report" in values
                and command_path(self, values["--report"]) != Path(self.report.path).resolve()
            ):
                raise ValueError("external report differs from command output")
            if self.report_capture == "file" and "--report" not in values:
                raise ValueError("file capture requires the actual report option")
        elif self.report_capture != "stdout":
            raise ValueError("oc verify only supports stdout capture")
        if re.search(r"\bsk-[A-Za-z0-9_-]{10,}|Bearer\s+\S+", self.model_dump_json()):
            raise ValueError("credential-like external metadata is forbidden")
        return self


class ExternalCheckResult(Contract):
    schema_version: Literal[2] = 2
    kind: Literal["external-check-result"] = "external-check-result"
    receipt: ExternalCheckReceipt
    receipt_hash: Hash
    data_snapshot_hash: Hash | None
    verdict: Literal["pass", "fail", "incomplete"]
    reasons: list[str]
    metrics: dict[str, float]
    completeness: dict[str, bool]
    quality: Literal["unknown"] = "unknown"
    release_eligible: Literal[False] = False


def verify_references(refs: list[ExternalReference]) -> None:
    paths = [Path(r.path).resolve() for r in refs]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate external evidence reference")
    for ref, path in zip(refs, paths):
        if not path.is_file() or file_hash(path) != ref.sha256:
            raise ValueError("external evidence missing or changed")


def read_reference(ref: ExternalReference, limit: int = 8 * 1024 * 1024) -> bytes:
    with Path(ref.path).open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit or hashlib.sha256(content).hexdigest() != ref.sha256:
        raise ValueError("external evidence changed or exceeds supported size")
    return content


def command_path(receipt: ExternalCheckReceipt, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else Path(receipt.code.checkout) / path).resolve()


def data_complete(receipt: ExternalCheckReceipt) -> bool:
    if not receipt.data_files or not (receipt.data_definition or "").strip():
        return False
    if receipt.check == "oc-verify":
        if not receipt.data_manifest:
            return False
        try:
            manifest = json.loads(read_reference(receipt.data_manifest))
        except json.JSONDecodeError:
            return False
        return bool(
            manifest
            == dict(
                schema_version=1,
                kind="external-data-snapshot",
                check_id=str(receipt.check_id),
                check=receipt.check,
                definition=receipt.data_definition,
                files=[r.model_dump() for r in receipt.data_files],
            )
        )
    argv = receipt.command
    fixtures = (
        command_path(receipt, argv[argv.index("--fixtures-dir") + 1])
        if "--fixtures-dir" in argv
        else Path(receipt.code.checkout) / "tests/fixtures/knowledge"
    )
    if not fixtures.is_dir():
        return False
    actual = {p.resolve() for p in fixtures.rglob("*") if p.is_file()}
    return (
        bool(actual)
        and (fixtures / "manifest.json").is_file()
        and (fixtures / "queries.json").is_file()
        and actual == {Path(r.path).resolve() for r in receipt.data_files}
    )


def import_external(receipt_path: Path, output: Path) -> ExternalCheckResult:
    """Validate a captured local receipt. No receipt command is ever executed."""
    if output.exists():
        raise ValueError("external output already exists")
    raw_receipt = receipt_path.read_bytes()
    if len(raw_receipt) > 256 * 1024:
        raise ValueError("external receipt exceeds supported size")
    receipt = ExternalCheckReceipt.model_validate_json(raw_receipt)
    verify_references(receipt.data_files)
    if receipt.model:
        profile = json.loads(read_reference(receipt.model.profile, 256 * 1024))
        if not isinstance(profile, dict) or digest(profile) != receipt.model.profile_hash:
            raise ValueError("external model profile differs from frozen hash")
        if not all(isinstance(profile.get(k), str) and profile[k] for k in ("endpoint", "model")):
            raise ValueError("external model profile lacks endpoint/model")
        actual_models = set()
        for ref in receipt.model.evidence:
            response = json.loads(read_reference(ref))
            if (
                not isinstance(response, dict)
                or not isinstance(response.get("id"), str)
                or not response["id"]
                or not isinstance(response.get("usage"), dict)
                or not isinstance(response.get("model"), str)
                or not response["model"]
            ):
                raise ValueError("external model evidence is not a provider JSON response")
            if not all(
                type(response["usage"].get(k)) is int and response["usage"][k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                raise ValueError("external provider usage is missing or invalid")
            actual_models.add(response["model"])
        if actual_models != set(receipt.model.actual_models):
            raise ValueError("external actual model differs from provider evidence")
    current = snapshot(Path(receipt.code.checkout))
    if current != receipt.code.model_dump(exclude={"checkout"}):
        raise ValueError("external code snapshot changed")
    raw_report = read_reference(receipt.report)
    text = raw_report.decode("utf-8-sig")
    metrics: dict[str, float] = {}
    known_failure = receipt.exit_code != 0
    source_complete = False
    metrics_complete = receipt.check == "oc-verify"
    if receipt.check == "oc-verify":
        profile = "real-ai" if receipt.mode == "real" else "local"
        lines = text.splitlines()
        source_complete = (
            bool(lines)
            and lines[-1].strip() == f"Verify {profile} passed"
            and any(line.startswith("ok ") for line in lines)
        )
    else:
        try:
            payload: Any = json.loads(text)
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        recognized = type(payload.get("passed")) is bool
        known_failure = (
            known_failure
            or payload.get("passed") is False
            or bool(payload.get("failures") or payload.get("fixture_errors"))
        )
        source_complete = (
            recognized
            and all(
                isinstance(payload.get(key), list) and bool(payload[key])
                for key in ("sources", "queries")
            )
            and all(
                key in payload
                for key in ("gates", "failures", "fixture_errors", "provider_summary")
            )
        )
        source_metrics = payload.get("metrics")
        metrics_complete = isinstance(source_metrics, dict) and bool(source_metrics)
        for name, value in source_metrics.items() if isinstance(source_metrics, dict) else []:
            try:
                numeric = type(value) in {int, float} and math.isfinite(value)
            except OverflowError:
                numeric = False
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.@-]{0,127}", name) or not numeric:
                metrics_complete = False
                continue
            metrics[name] = float(value)
    completeness = {
        "execution": receipt.execution_complete,
        "source_report": source_complete,
        "code_snapshot": True,
        "data_snapshot": data_complete(receipt),
        "model_snapshot": receipt.mode == "deterministic" or receipt.model is not None,
        "metric_definitions": metrics_complete
        and set(metrics) == set(receipt.metric_definitions)
        and all(k and v.strip() for k, v in receipt.metric_definitions.items()),
    }
    reasons = [f"missing_{k}" for k, complete in completeness.items() if not complete]
    if known_failure:
        reasons.insert(0, "external_command_or_report_failed")
    result = ExternalCheckResult(
        receipt=receipt,
        receipt_hash=hashlib.sha256(raw_receipt).hexdigest(),
        data_snapshot_hash=digest([r.model_dump() for r in receipt.data_files])
        if receipt.data_files
        else None,
        verdict="fail" if known_failure else "incomplete" if reasons else "pass",
        reasons=reasons,
        metrics=metrics,
        completeness=completeness,
    )
    atomic_json(output, result.model_dump(mode="json"))
    return result
