"""The only executable entrypoint that can consume this run's dedicated secret.

No provider secret is available to install, test, preflight or product processes.
The runner accepts only a fresh paid-phase admission from the same GitHub run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterator

from scripts.companion_ai_validation import continuation
from scripts.companion_ai_validation.broker import (
    MODEL, OPERATIONS, OUTPUT_TOKENS, CONTEXT_TOKENS, PRICE_VALID_UNTIL,
    MAX_BYTES, SESSION_SECONDS, Broker, GateError, HTTPSUpstream, RunWindow, decode, encode,
    require_linux, serve, profile_lineage,
)

KEY_NAME = "OFFERPILOT_COMPANION_DEEPSEEK_20261021"
MODEL_NAME = "OFFERPILOT_COMPANION_AI_MODEL"
ROOT = Path(__file__).resolve().parents[2]


def clean_environment() -> None:
    """No inherited TLS trust/logging, proxy, SDK, tracing or credential settings."""
    os.environ.clear()
    os.environ.update({
        "PATH": os.defpath, "LANG": "C.UTF-8", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "DO_NOT_TRACK": "1",
    })


def check_admission(record: Any, metadata: dict[str, str], now: float, *,
                    shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False, experience_retest: bool = False) -> str:
    lineage = profile_lineage(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest)
    expected = lineage.LINEAGE_KEYS | {
        "schema", "source_sha", "helper_sha", "run_id", "run_attempt", "checked_at",
        "checked_at_monotonic_ns", "quote_deadline_monotonic_ns",
    }
    if (not isinstance(record, dict) or set(record) != expected
            or type(record["schema"]) is not int
            or record["schema"] != lineage.ADMISSION_SCHEMA):
        raise GateError("admission_required")
    try:
        lineage.validate_lineage(record)
    except continuation.ContinuationError as exc:
        raise GateError("admission_lineage") from exc
    if (shadow_review or shadow_quote_v2 or shadow_projection or shadow_single_target or experience or experience_retest) and (not isinstance(record["source_sha"], str)
                          or not isinstance(record["helper_sha"], str)
                          or record["source_sha"] in lineage.ALL_HISTORICAL_IDENTITIES
                          or record["helper_sha"] in lineage.ALL_HISTORICAL_IDENTITIES):
        raise GateError("admission_identity")
    sha = record["source_sha"]
    helper = record["helper_sha"]
    run_id = record["run_id"]
    if (not isinstance(sha, str) or len(sha) != 40 or
            any(c not in "0123456789abcdef" for c in sha) or
            not isinstance(helper, str) or len(helper) != 40 or
            any(c not in "0123456789abcdef" for c in helper) or sha == helper
            or sha in {continuation.ORIGINAL_SOURCE_SHA, continuation.ORIGINAL_HELPER_SHA,
                          continuation.SECOND_SOURCE_SHA, continuation.SECOND_HELPER_SHA,
                          record["predecessor_source_sha"], record["predecessor_helper_sha"]}
            or helper in {continuation.ORIGINAL_SOURCE_SHA, continuation.ORIGINAL_HELPER_SHA,
                          continuation.SECOND_SOURCE_SHA, continuation.SECOND_HELPER_SHA,
                             record["predecessor_source_sha"], record["predecessor_helper_sha"]}
            or not isinstance(run_id, str) or not run_id or run_id[0] == "0"
            or any(c not in "0123456789" for c in run_id)
            or record["run_attempt"] != "1"):
        raise GateError("admission_identity")
    if (metadata.get("GITHUB_SHA") != sha or metadata.get("GITHUB_RUN_ID") != record["run_id"]
            or metadata.get("GITHUB_RUN_ATTEMPT") != "1"
            or metadata.get("GITHUB_EVENT_NAME") != "push"
            or metadata.get("GITHUB_REPOSITORY") != "offercontext/offerPilot"
            or metadata.get("GITHUB_REF") != "refs/heads/feat/20261021-companion-life-v1"
            or metadata.get("GITHUB_WORKFLOW_SHA") != sha
            or metadata.get("GITHUB_WORKFLOW_REF") != (
                "offercontext/offerPilot/.github/workflows/companion-real-ai.yml"
                "@refs/heads/feat/20261021-companion-life-v1")):
        raise GateError("admission_identity")
    check_admission_time(record, now, shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest)
    return sha


def check_admission_time(record: dict[str, Any], now: float, *,
                         shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False, experience_retest: bool = False) -> None:
    lineage = profile_lineage(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest)
    deadline = lineage.PRICE_VALID_UNTIL if (shadow_review or shadow_quote_v2 or shadow_projection or shadow_single_target or experience or experience_retest) else PRICE_VALID_UNTIL
    checked = record["checked_at"]
    if type(checked) is not int or not 0 <= now - checked <= 120:
        raise GateError("admission_expired")
    monotonic = time.monotonic()
    if not math.isfinite(monotonic) or not 0 <= monotonic < 9_223_372_036:
        raise GateError("clock_anomaly")
    now_ns = math.floor(monotonic * 1e9)
    checked_ns, quote_ns = record["checked_at_monotonic_ns"], record["quote_deadline_monotonic_ns"]
    if (type(checked_ns) is not int or not 0 <= checked_ns < 2**63
            or type(quote_ns) is not int or not checked_ns < quote_ns < 2**63):
        raise GateError("admission_required")
    if not 0 <= now_ns - checked_ns <= 120_000_000_000:
        raise GateError("admission_expired")
    if quote_ns - now_ns < SESSION_SECONDS * 1_000_000_000:
        raise GateError("price_review_expired")
    if now + SESSION_SECONDS > deadline:
        raise GateError("price_review_expired")


def write_config(data_dir: Path, base_url: str, token: str) -> None:
    """Only an ephemeral loopback credential is persisted in the owned fixture."""
    profile = {
        "id": "companion-bounded", "label": "Synthetic acceptance",
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


class RequestEvidence:
    """Exact outgoing bodies from this fixed synthetic harness, never headers.

    The broker has already validated and reserved the request. Durable capture
    must finish before transport; failure retains that conservative reservation.
    """
    def __init__(self, evidence_dir: Path, *, shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False, experience_retest: bool = False):
        self._operations: tuple[str, ...] = profile_lineage(
            shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest).OPERATIONS
        self._fd = os.open(evidence_dir / "requests.jsonl",
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._count = 0
        try:
            directory_fd = os.open(evidence_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            self.close()
            raise

    def record(self, body: bytes) -> None:
        if (type(body) is not bytes or not 0 < len(body) <= MAX_BYTES
                or self._count >= len(self._operations) or self._fd < 0):
            raise GateError("request_evidence_shape")
        try:
            raw_body = body.decode("utf-8", errors="strict")
        except UnicodeError:
            raise GateError("request_evidence_shape") from None
        line = encode({"operation": self._operations[self._count], "ordinal": self._count + 1,
                       "body_sha256": hashlib.sha256(body).hexdigest(),
                       "raw_body": raw_body}) + b"\n"
        try:
            view = memoryview(line)
            while view:
                count = os.write(self._fd, view)
                if count <= 0:
                    raise GateError("request_evidence_write")
                view = view[count:]
            os.fsync(self._fd)
        except OSError:
            raise GateError("request_evidence_write") from None
        self._count += 1

    def close(self) -> None:
        if self._fd >= 0:
            fd, self._fd = self._fd, -1
            os.close(fd)


class ResponseEvidence:
    """Bounded successful synthetic samples survive a later failed matrix turn."""
    def __init__(self, path: Path, *, operations: tuple[str, ...] = OPERATIONS):
        self._operations = operations
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._count, self._size = 0, 0

    def record(self, entry: dict[str, Any]) -> None:
        fields = {"operation", "user_input", "assistant_text", "expected_context", "snapshot",
                  "provenance", "ordinary_turn_read_only", "source_verified",
                  "source_facts_before_projection"}
        if (not isinstance(entry, dict) or set(entry) != fields
                or self._count >= len(self._operations)
                or entry.get("operation") != self._operations[self._count]
                or entry.get("source_verified") is not True
                or entry.get("ordinary_turn_read_only") is not True
                or not isinstance(entry.get("assistant_text"), str)
                or not 0 < len(entry["assistant_text"]) <= 8192
                or not isinstance(entry.get("user_input"), str)
                or len(entry["user_input"]) > 4096):
            raise GateError("synthetic_evidence_shape")
        line = encode(entry) + b"\n"
        if len(line) > 32768 or self._size + len(line) > 131072:
            raise GateError("synthetic_evidence_limit")
        view = memoryview(line)
        while view:
            count = os.write(self._fd, view)
            if count <= 0:
                raise GateError("synthetic_evidence_write")
            view = view[count:]
        os.fsync(self._fd)
        self._size += len(line)
        self._count += 1

    def close(self) -> None:
        os.close(self._fd)


def execute(send: Callable[[bytes], tuple[int, bytes]], source_sha: str,
            evidence_dir: Path, *, window: RunWindow | None = None,
            shadow_review: bool = False, shadow_quote_v2: bool = False, shadow_projection: bool = False, shadow_single_target: bool = False, experience: bool = False, experience_retest: bool = False) -> dict[str, Any]:
    require_linux()
    profile_lineage(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest)
    window = window if window is not None else RunWindow(shadow_review=shadow_review, shadow_quote_v2=shadow_quote_v2, shadow_projection=shadow_projection, shadow_single_target=shadow_single_target, experience=experience, experience_retest=experience_retest)
    if shadow_single_target:
        from scripts.companion_ai_validation.shadow_single_target_driver import execute as execute_single
        return execute_single(send, source_sha, evidence_dir, window=window)
    if shadow_projection:
        from scripts.companion_ai_validation.shadow_projection_driver import execute as execute_projection
        return execute_projection(send, source_sha, evidence_dir, window=window)
    if shadow_quote_v2:
        from scripts.companion_ai_validation.shadow_quote_driver import execute as execute_quote
        return execute_quote(send, source_sha, evidence_dir, window=window)
    if shadow_review:
        from scripts.companion_ai_validation.shadow_driver import execute as execute_shadow
        return execute_shadow(send, source_sha, evidence_dir, window=window)
    # Defer production imports until runtime environment is sanitized by main.
    # Offline callers are protected by pytest's early loopback-only network guard.
    from scripts.companion_ai_validation.driver import MatrixFailure, run_matrix
    lineage = profile_lineage(experience=experience, experience_retest=experience_retest)

    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    requests: RequestEvidence | None = None

    def captured_send(body: bytes) -> tuple[int, bytes]:
        if requests is None:
            raise GateError("request_evidence_required")
        requests.record(body)
        # The new durable I/O cannot extend the broker's original send window.
        broker._check_window()
        return send(body)

    broker = Broker(evidence_dir / "budget.jsonl", captured_send, source_sha, window=window, experience=experience, experience_retest=experience_retest)
    outcome: dict[str, Any] = {"passed": False, "error_code": "incomplete"}
    samples: ResponseEvidence | None = None
    try:
        requests = RequestEvidence(evidence_dir, experience=experience, experience_retest=experience_retest)
        samples = ResponseEvidence(evidence_dir / "responses.jsonl", operations=lineage.OPERATIONS)
        with tempfile.TemporaryDirectory(prefix="offerpilot-companion-owned-") as owned:
            data_dir = Path(owned) / "data"
            data_dir.mkdir(mode=0o700)
            with serve(broker) as base_url:
                def stage_for_data_dir(owned_data_dir: Path) -> Callable[[str], Any]:
                    @contextmanager
                    def stage(operation: str) -> Iterator[None]:
                        token = broker.begin_stage(operation)
                        write_config(owned_data_dir, base_url, token)
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
                    return stage

                if experience or experience_retest:
                    from scripts.companion_ai_validation.experience_driver import run_matrix as experience_matrix
                    outcome = experience_matrix(data_dir, stage_for_data_dir, record_response=samples.record,
                                                **({"retest": True} if experience_retest else {}))
                else:
                    outcome = run_matrix(data_dir, stage_for_data_dir(data_dir), record_response=samples.record)
                summary = broker.summary()
                if (summary["halted"] or summary["stages_complete"] != len(lineage.OPERATIONS)
                        or summary["reserved_micro_cny"] != 0):
                    raise GateError("matrix_not_settled")
    except (GateError, MatrixFailure) as exc:
        outcome = {"passed": False, "error_code": str(exc)}
    except BaseException:
        outcome = {"passed": False, "error_code": "validation_blocked"}
    finally:
        if requests is not None:
            try:
                requests.close()
            except OSError:
                outcome = {"passed": False, "error_code": "request_evidence_write"}
        if samples is not None:
            try:
                samples.close()
            except OSError:
                outcome = {"passed": False, "error_code": "synthetic_evidence_write"}
        summary = broker.summary()
        try:
            broker.close()
        except GateError:
            outcome = {"passed": False, "error_code": "journal_failure"}
        report = {"schema": continuation.ADMISSION_SCHEMA, "matrix": outcome, "budget": summary}
        if experience or experience_retest:
            from scripts.companion_ai_validation import experience_candidate, experience_retest_candidate
            candidate = experience_retest_candidate if experience_retest else experience_candidate
            report["product_candidate"] = {"source_sha": candidate.SOURCE_SHA,
                                           "tree_sha": candidate.TREE_SHA,
                                           "objects": candidate.OBJECTS,
                                           "web_executed": False}
        (evidence_dir / "summary.json").write_bytes(encode(report) + b"\n")
    return report


def main() -> int:
    try:
        require_linux()
        parser = argparse.ArgumentParser()
        parser.add_argument("--live", action="store_true", required=True)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--shadow-review", action="store_true")
        modes.add_argument("--shadow-quote-v2", action="store_true")
        modes.add_argument("--shadow-projection", action="store_true")
        modes.add_argument("--shadow-single-target", action="store_true")
        modes.add_argument("--experience", action="store_true")
        modes.add_argument("--experience-retest", action="store_true")
        parser.add_argument("--admission-file", type=Path, required=True)
        parser.add_argument("--evidence-dir", type=Path, required=True)
        args = parser.parse_args()
        window = RunWindow(shadow_review=args.shadow_review, shadow_quote_v2=args.shadow_quote_v2, shadow_projection=args.shadow_projection, shadow_single_target=args.shadow_single_target, experience=args.experience, experience_retest=args.experience_retest)
        keys = ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME",
                "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF")
        metadata = {name: os.environ.get(name, "") for name in keys}
        with args.admission_file.open("rb") as admission_file:
            admission = decode(admission_file.read(MAX_BYTES + 1))
        source_sha = check_admission(admission, metadata, window.effective_wall(),
                                     shadow_review=args.shadow_review, shadow_quote_v2=args.shadow_quote_v2, shadow_projection=args.shadow_projection, shadow_single_target=args.shadow_single_target, experience=args.experience, experience_retest=args.experience_retest)
        window.restrict_quote_deadline(admission["quote_deadline_monotonic_ns"])
        # Verify checked-out files, not merely a supplied environment string.
        git_environment = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
                           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1"}
        checkout = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  env=git_environment, capture_output=True,
                                  check=True).stdout.decode().strip()
        dirty = subprocess.run(["git", "-c", "core.fsmonitor=false", "status", "--porcelain",
                                "--untracked-files=no"], cwd=ROOT, env=git_environment,
                               capture_output=True, check=True).stdout
        if checkout != source_sha or dirty:
            raise GateError("checkout_changed")
        if args.experience or args.experience_retest:
            from scripts.companion_ai_validation import experience_candidate, experience_retest_candidate
            candidate = experience_retest_candidate if args.experience_retest else experience_candidate
            actual = {name: subprocess.run(
                ["git", "rev-parse", f"HEAD:{name}"], cwd=ROOT, env=git_environment,
                capture_output=True, check=True).stdout.decode().strip()
                for name in candidate.OBJECTS}
            candidate.validate_objects(actual)
        # Read the raw commit: HEAD^ and pretty-format parents can disappear in
        # Actions' shallow checkout. The immutable commit still names its parent.
        commit = subprocess.run(["git", "cat-file", "-p", "HEAD"], cwd=ROOT,
                                env=git_environment, capture_output=True,
                                check=True).stdout
        header = commit.partition(b"\n\n")[0].splitlines()
        parents = [line[7:] for line in header if line.startswith(b"parent ")]
        if parents != [admission["helper_sha"].encode("ascii")]:
            raise GateError("checkout_helper_mismatch")
        if os.environ.get(MODEL_NAME) != MODEL:
            raise GateError("provider_metadata_mismatch")
        # Checkout/provenance checks may stall. They never extend admission age
        # or the fixed quote, and predecessor bytes must still verify here.
        check_admission(admission, metadata, window.effective_wall(),
                                     shadow_review=args.shadow_review, shadow_quote_v2=args.shadow_quote_v2, shadow_projection=args.shadow_projection, shadow_single_target=args.shadow_single_target, experience=args.experience, experience_retest=args.experience_retest)
        # The final lineage verification can also stall. No further file or
        # metadata I/O may sit between this anchored check and secret access.
        check_admission_time(admission, window.effective_wall(),
                             shadow_review=args.shadow_review, shadow_quote_v2=args.shadow_quote_v2, shadow_projection=args.shadow_projection, shadow_single_target=args.shadow_single_target, experience=args.experience, experience_retest=args.experience_retest)
        # Read the one explicitly authorized environment secret only after all gates.
        key = os.environ.pop(KEY_NAME, "")
        clean_environment()
        transport = HTTPSUpstream(key, window=window)
        key = ""
        if args.experience_retest:
            report = execute(transport, source_sha, args.evidence_dir, window=window, experience_retest=True)
        elif args.experience:
            report = execute(transport, source_sha, args.evidence_dir, window=window, experience=True)
        elif args.shadow_single_target:
            report = execute(transport, source_sha, args.evidence_dir, window=window, shadow_single_target=True)
        elif args.shadow_projection:
            report = execute(transport, source_sha, args.evidence_dir, window=window, shadow_projection=True)
        elif args.shadow_quote_v2:
            report = execute(transport, source_sha, args.evidence_dir, window=window, shadow_quote_v2=True)
        elif args.shadow_review:
            report = execute(transport, source_sha, args.evidence_dir, window=window,
                             shadow_review=True)
        else:
            report = execute(transport, source_sha, args.evidence_dir, window=window)
        print(json.dumps({"passed": report["matrix"]["passed"], "budget": report["budget"]},
                         sort_keys=True))
        return 0 if report["matrix"]["passed"] else 1
    except BaseException as exc:
        code = str(exc) if isinstance(exc, GateError) else "validation_blocked"
        print(json.dumps({"passed": False, "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
