"""Execute the originally approved evaluator as a read-only historical reader."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
from base64 import b64encode
import json
import subprocess
import time
from typing import Any
from uuid import uuid4

from offerpilot.evaluation.contracts import VariantConfig
from offerpilot.evaluation.core_refresh_contracts import bound_file, read, reference, require
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.runner import (
    ROOT,
    process_environment,
    snapshot,
    target_environment,
    source_cache_prefix,
)


class HistoryReadError(ValueError):
    def __init__(self, receipt: dict[str, Any]) -> None:
        super().__init__("original reader native failure; full receipt retained")
        self.receipt = receipt


def captured_bytes(value: bytes | str | None) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value or b""


def audit_history(
    store: Path, record: dict[str, Any], descriptor: Path, receipt_directory: Path | None = None
) -> dict[str, Any]:
    """No recovery, store write or package import from the new evaluator in the child."""
    config = read(descriptor)
    require(
        set(config) == {"schema_version", "kind", "native_receipt", "approval_file"}
        and config["schema_version"] == 3
        and config["kind"] == "baseline-history-reader",
        "historical reader descriptor invalid",
    )
    native = bound_file(config["native_receipt"])
    approval = bound_file(config["approval_file"])
    manifest = read(Path(record["run_directory"]) / "run-manifest.json")
    require(digest(manifest) == record["manifest_hash"], "original manifest changed")
    target = manifest["variants"]["candidate"]
    checkout = Path(target["checkout"])
    interpreter = Path(target["interpreter"])
    require(
        checkout.is_absolute() and interpreter.is_absolute(), "historical paths must be absolute"
    )
    checkout = safe_path(checkout)
    if receipt_directory is not None:
        receipt_directory = safe_path(receipt_directory.absolute())
        require(
            not any(
                receipt_directory.is_relative_to(p.resolve())
                for p in (store, checkout, Path(record["run_directory"]))
            ),
            "historical receipt must be outside the approved store/checkout/raw",
        )
    variant = VariantConfig(
        variant_id="candidate", checkout=str(checkout), interpreter=str(interpreter)
    )
    before = snapshot(checkout)
    runtime_before = target_environment(variant)
    require(
        all(before[k] == target[k] for k in before) and runtime_before == target["runtime"],
        "original checkout or runtime cannot be reconstructed",
    )
    reader = checkout / "src/offerpilot/evaluation/core_runner.py"
    reader_sha = file_hash(reader)
    child = ROOT / "scripts/evaluation_subject/baseline_history.py"
    child_before = reference(child)
    record_path = store / "records" / (record["baseline_id"] + ".json")
    cache_prefix = str(source_cache_prefix())
    args = dict(
        checkout=str(checkout.resolve()),
        interpreter=str(interpreter.resolve()),
        evaluator_hash=record["evaluator_hash"],
        store=str(store.resolve()),
        record_path=str(record_path.resolve()),
        record_sha256=file_hash(record_path),
        native_return=str(native),
        approval_file=str(approval),
        bytecode_prefix=cache_prefix,
    )
    argv = [str(interpreter), "-I", "-B", "-X", "pycache_prefix=" + cache_prefix, str(child)]
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            input=json.dumps(args).encode("utf-8"),
            capture_output=True,
            cwd=checkout,
            env=process_environment(checkout, checkout / ".codex"),
            timeout=180,
        )
        receipt = dict(
            argv=argv,
            cwd=str(checkout),
            native_exit=result.returncode,
            timed_out=False,
            elapsed_seconds=time.monotonic() - started,
        )
        stdout_raw, stderr_raw = result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        receipt = dict(
            argv=argv,
            cwd=str(checkout),
            native_exit=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started,
        )
        stdout_raw, stderr_raw = captured_bytes(exc.stdout), captured_bytes(exc.stderr)
    receipt.update(
        stdout=stdout_raw.decode("utf-8", errors="replace"),
        stderr=stderr_raw.decode("utf-8", errors="replace"),
        stdout_base64=b64encode(stdout_raw).decode("ascii"),
        stderr_base64=b64encode(stderr_raw).decode("ascii"),
        stdout_sha256=sha256(stdout_raw).hexdigest(),
        stderr_sha256=sha256(stderr_raw).hexdigest(),
    )
    receipt["child_before"] = child_before
    receipt["child_after"] = reference(child)
    if receipt_directory is not None:
        atomic_json(receipt_directory / ("historical-native-" + str(uuid4()) + ".json"), receipt)
    if receipt["native_exit"] != 0 or receipt["timed_out"]:
        raise HistoryReadError(receipt)
    require(reference(child) == child_before, "historical child script changed during execution")
    proof = json.loads(stdout_raw)
    require(
        isinstance(proof, dict)
        and proof.get("schema_version") == 3
        and proof.get("source_loading_policy") == "empty-prefix-bytecode-disabled"
        and proof.get("kind") == "original-evaluator-readonly-audit"
        and proof.get("original_raw_byte_binding") is True
        and proof.get("original_record_digest") == digest(record)
        and proof.get("evaluator_hash") == record["evaluator_hash"]
        and Path(proof["reader_file"]).resolve() == reader.resolve()
        and proof["reader_file_sha256"] == reader_sha
        and Path(proof["interpreter"]).resolve() == interpreter.resolve()
        and proof["approved"]["target"] == target,
        "historical reader returned a foreign proof",
    )
    after = snapshot(checkout)
    runtime_after = target_environment(variant)
    require(
        after == before and runtime_after == runtime_before and file_hash(reader) == reader_sha,
        "historical reader source/runtime changed",
    )
    return dict(
        schema_version=3,
        descriptor=reference(descriptor),
        child=child_before,
        argv=argv,
        native_exit=result.returncode,
        elapsed_seconds=time.monotonic() - started,
        stdout_sha256=sha256(stdout_raw).hexdigest(),
        native_receipt=receipt,
        proof=proof,
        source_before=before,
        source_after=after,
        runtime_before=runtime_before,
        runtime_after=runtime_after,
        historical_native_receipt=reference(native),
        native_receipt_scope="corroborating interval, not an originally pinned commit timestamp",
    )
