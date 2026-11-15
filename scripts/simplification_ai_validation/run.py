"""The only executable entrypoint that can consume this run's dedicated secret.

No provider secret is available to install, test, preflight or product processes.
The runner accepts only a fresh paid-phase admission from the same GitHub run.
"""
from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Iterator

from scripts.simplification_ai_validation.broker import (
    BASE_URL, MODEL, OPERATIONS, OUTPUT_TOKENS, CONTEXT_TOKENS,
    SESSION_SECONDS, Broker, GateError, HTTPSUpstream, PriceWindow, boot_identity,
    clock_number, decode, encode, serve, require_linux,
)

KEY_NAME = "OFFERPILOT_SIMPLIFICATION_DEEPSEEK_20261021"
BASE_NAME = "OFFERPILOT_SIMPLIFICATION_AI_BASE_URL"
MODEL_NAME = "OFFERPILOT_SIMPLIFICATION_AI_MODEL"
ROOT = Path(__file__).resolve().parents[2]


def clean_environment() -> None:
    """No inherited TLS trust/logging, proxy, SDK, tracing or credential settings."""
    os.environ.clear()
    os.environ.update({
        "PATH": os.defpath, "LANG": "C.UTF-8", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "DO_NOT_TRACK": "1",
    })


def check_admission(record: Any, metadata: dict[str, str],
                    entry: PriceWindow) -> tuple[str, PriceWindow]:
    expected = {"schema", "source_sha", "helper_sha", "run_id", "checked_at",
                "checked_monotonic", "boot_id", "price_window"}
    if (not isinstance(record, dict) or set(record) != expected
            or type(record["schema"]) is not int or record["schema"] != 2):
        raise GateError("admission_required")
    sha = record["source_sha"]
    helper = record["helper_sha"]
    if (not isinstance(sha, str) or len(sha) != 40 or
            any(c not in "0123456789abcdef" for c in sha) or
            not isinstance(helper, str) or len(helper) != 40 or
            any(c not in "0123456789abcdef" for c in helper) or sha == helper):
        raise GateError("admission_identity")
    if (metadata.get("GITHUB_SHA") != sha or metadata.get("GITHUB_RUN_ID") != record["run_id"]
            or metadata.get("GITHUB_RUN_ATTEMPT") != "1"
            or metadata.get("GITHUB_EVENT_NAME") != "push"
            or metadata.get("GITHUB_REPOSITORY") != "offercontext/offerPilot"
            or metadata.get("GITHUB_REF") != "refs/heads/feat/20261021-product-simplification"):
        raise GateError("admission_identity")
    if record["boot_id"] != boot_identity():
        raise GateError("invalid_clock")
    window = PriceWindow.restore(record["price_window"])
    checked = clock_number(record["checked_monotonic"])
    if (not window.monotonic <= checked <= entry.monotonic
            or clock_number(record["checked_at"]) < window.wall):
        raise GateError("invalid_clock")
    window.tighten(entry)
    check_freshness(record, window)
    return sha, window


def check_freshness(record: dict[str, Any], window: PriceWindow) -> None:
    # Full-session quote admission is checked again after all I/O, before key access.
    now, monotonic = window.check(SESSION_SECONDS)
    checked = clock_number(record["checked_at"])
    checked_monotonic = clock_number(record["checked_monotonic"])
    if not 0 <= now - checked <= 120 or not 0 <= monotonic - checked_monotonic <= 120:
        raise GateError("admission_expired")


def write_config(data_dir: Path, base_url: str, token: str) -> None:
    """Only an ephemeral loopback credential is persisted in the owned fixture."""
    profile = {
        "id": "simplification-bounded", "label": "Synthetic acceptance",
        "provider": "openai_compatible", "api_key": token, "base_url": base_url,
        "model": MODEL, "enabled": True, "context_window": CONTEXT_TOKENS,
        "max_output_tokens": OUTPUT_TOKENS, "supports_json_schema": False,
    }
    config = {"providers": [profile], "active_provider_id": profile["id"],
              "fallback_provider_id": "", "fallback_provider_ids": [],
              "chat_auto_approve_writes": False, "log_level": "ERROR",
              "confirmation_secret": "synthetic-acceptance-local-confirmation-only"}
    temporary = data_dir / "config.next.json"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encode(config))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, data_dir / "config.json")


def execute(send: Callable[[bytes], tuple[int, bytes]], source_sha: str,
            evidence_dir: Path, price_window: PriceWindow | None = None) -> dict[str, Any]:
    require_linux()
    # Defer production imports until runtime environment is sanitized by main.
    # Offline callers are protected by pytest's early loopback-only network guard.
    from scripts.simplification_ai_validation.driver import MatrixFailure, run_matrix

    evidence_dir.mkdir(parents=True, exist_ok=False)
    broker = Broker(evidence_dir / "budget.jsonl", send, source_sha, price_window)
    outcome: dict[str, Any] = {"passed": False, "error_code": "incomplete"}
    try:
        with tempfile.TemporaryDirectory(prefix="offerpilot-simplification-owned-") as owned:
            data_dir = Path(owned) / "data"
            data_dir.mkdir(mode=0o700)
            with serve(broker) as base_url:
                @contextmanager
                def stage(operation: str) -> Iterator[None]:
                    token = broker.begin_stage(operation)
                    write_config(data_dir, base_url, token)
                    try:
                        yield
                    except BaseException:
                        broker.reject()
                        try:
                            broker.close_stage()
                        except GateError:
                            pass
                        raise
                    else:
                        broker.close_stage()

                outcome = run_matrix(data_dir, stage)
                summary = broker.summary()
                if (summary["halted"] or summary["stages_complete"] != len(OPERATIONS)
                        or summary["reserved_micro_cny"] != 0):
                    raise GateError("matrix_not_settled")
    except (GateError, MatrixFailure) as exc:
        outcome = {"passed": False, "error_code": str(exc)}
    except BaseException:
        outcome = {"passed": False, "error_code": "validation_blocked"}
    finally:
        summary = broker.summary()
        try:
            broker.close()
        except GateError:
            outcome = {"passed": False, "error_code": "journal_failure"}
        report = {"schema": 1, "matrix": outcome, "budget": summary}
        (evidence_dir / "summary.json").write_bytes(encode(report) + b"\n")
    return report


def main() -> int:
    try:
        require_linux()
        parser = argparse.ArgumentParser()
        parser.add_argument("--live", action="store_true", required=True)
        parser.add_argument("--admission-file", type=Path, required=True)
        parser.add_argument("--evidence-dir", type=Path, required=True)
        args = parser.parse_args()
        keys = ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME",
                "GITHUB_REPOSITORY", "GITHUB_REF")
        metadata = {name: os.environ.get(name, "") for name in keys}
        entry = PriceWindow.capture()
        entry.check(SESSION_SECONDS)
        record = decode(args.admission_file.read_bytes())
        source_sha, price_window = check_admission(record, metadata, entry)
        # Verify checked-out files, not merely a supplied environment string.
        git_environment = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
                           "GIT_CONFIG_GLOBAL": os.devnull}
        checkout = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  env=git_environment, capture_output=True,
                                  check=True).stdout.decode().strip()
        dirty = subprocess.run(["git", "-c", "core.fsmonitor=false", "status", "--porcelain",
                                "--untracked-files=no"], cwd=ROOT, env=git_environment,
                               capture_output=True, check=True).stdout
        if checkout != source_sha or dirty:
            raise GateError("checkout_changed")
        if os.environ.get(BASE_NAME) != BASE_URL or os.environ.get(MODEL_NAME) != MODEL:
            raise GateError("provider_metadata_mismatch")
        check_freshness(record, price_window)
        # Read the one explicitly authorized environment secret only after all gates.
        key = os.environ.pop(KEY_NAME, "")
        clean_environment()
        transport = HTTPSUpstream(key, price_window)
        key = ""
        report = execute(transport, source_sha, args.evidence_dir, price_window)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["matrix"]["passed"] else 1
    except BaseException as exc:
        code = str(exc) if isinstance(exc, GateError) else "validation_blocked"
        print(json.dumps({"passed": False, "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
