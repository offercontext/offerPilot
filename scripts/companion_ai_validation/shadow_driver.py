"""Six immutable draft-span reviews, never generation, rewriting or tool execution.

Structural validity is not semantic truth. This runner does not grade verdicts or
change any product reply. Only the fixed request bytes may reach the broker.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from scripts.companion_ai_validation import broker as gate

ASSETS = Path(__file__).with_name("shadow_cases")
CASES_SHA256 = "501cd8ac4b49b6a963a6829be487ae0f3e46dd1f77086b1a2bdcfa2cca055b30"
REQUEST_HASHES = {
    "s01": "c41fcc6094892a4fc0531ac99e43bcd5845cb79c51424f90a87af1e10e4100e0",
    "s02": "8a1cb62a6b0eeb71800a7301ebf38ba95f6a63e3b085cb6cc489b29fb32f74ec",
    "s03": "bdf3610e090a9defa725570a9528b849139c49d6b1a436fdf25896452d0e78b2",
    "s04": "25d7c2475fd86d2f8175d637eb5e82085520c785a9f5a92104ccc02224d84f42",
    "s05": "df2660209f2e54138a27a467d44ac8d0826798c8963a47c663914955328b0ec8",
    "s06": "8dadb05cb62fd5e14f766c074cdca7ec63be641a49794ffb153bc67840cf02d4",
}
LABELS = frozenset({"supported", "contradicted", "unknown", "not_factual", "ambiguous"})


def _pinned(path: Path, digest: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise gate.GateError("shadow_asset_missing")
    with path.open("rb") as stream:
        raw = stream.read(gate.MAX_BYTES + 1)
    if not 0 < len(raw) <= gate.MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise gate.GateError("shadow_asset_changed")
    return raw


def frozen_request(operation: str) -> bytes:
    if operation not in REQUEST_HASHES:
        raise gate.GateError("shadow_operation")
    return _pinned(ASSETS / "requests" / (operation + ".json"), REQUEST_HASHES[operation])


def cases() -> list[dict[str, Any]]:
    value: list[dict[str, Any]] = json.loads(_pinned(ASSETS / "cases.json", CASES_SHA256))
    if [case["id"] for case in value] != list(REQUEST_HASHES):
        raise gate.GateError("shadow_cases")
    for case in value:
        payload = gate.decode(frozen_request(case["id"]))
        # Pinned assets also have to agree with each other, not merely their hashes.
        data = json.loads(payload["messages"][1]["content"])
        if data != {key: case[key] for key in ("draft", "target", "sources")}:
            raise gate.GateError("shadow_case_request_mismatch")
    return value


def _span(text: str, span: dict[str, Any], quote_key: str) -> None:
    start, end = span.get("start"), span.get("end")
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
            or text[start:end] != span.get(quote_key)):
        raise gate.GateError("shadow_reference_span")


def validate(content: str, case: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, str) or len(content.encode()) > 16384:
        raise gate.GateError("shadow_response_size")
    value = gate.decode(content.encode())
    if set(value) != {"target", "verdict", "evidence", "rationale"}:
        raise gate.GateError("shadow_output_fields")
    target = value["target"]
    if not isinstance(target, dict) or target != case["target"]:
        raise gate.GateError("shadow_target_changed")
    _span(case["draft"], target, "text")
    if not isinstance(value["verdict"], str) or value["verdict"] not in LABELS:
        raise gate.GateError("shadow_verdict")
    if not isinstance(value["rationale"], str) or len(value["rationale"]) > 500:
        raise gate.GateError("shadow_rationale")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 4:
        raise gate.GateError("shadow_evidence_count")
    sources = {source["id"]: source["text"] for source in case["sources"]}
    for entry in evidence:
        if (not isinstance(entry, dict)
                or set(entry) != {"source_id", "start", "end", "quote"}
                or not isinstance(entry["source_id"], str) or entry["source_id"] not in sources):
            raise gate.GateError("shadow_evidence_scope")
        _span(sources[entry["source_id"]], entry, "quote")
    if value["verdict"] in {"supported", "contradicted"} and not evidence:
        raise gate.GateError("shadow_evidence_missing")
    return value


class RawResponses:
    """Bounded raw bytes before any envelope/usage/content interpretation."""
    def __init__(self, path: Path, *, operations: tuple[str, ...] = tuple(REQUEST_HASHES)):
        self.operations = operations
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.count = 0
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            self.close()
            raise

    def record(self, operation: str, status: int, raw: bytes) -> None:
        if (self.count >= 6 or operation != self.operations[self.count]
                or type(status) is not int or type(raw) is not bytes
                or len(raw) > gate.MAX_BYTES + 1):
            raise gate.GateError("shadow_response_capture")
        line = gate.encode({"operation": operation, "status": status,
                            "raw_base64": base64.b64encode(raw).decode("ascii"),
                            "body_sha256": hashlib.sha256(raw).hexdigest()}) + b"\n"
        view = memoryview(line)
        while view:
            count = os.write(self.fd, view)
            if count <= 0:
                raise gate.GateError("shadow_response_capture")
            view = view[count:]
        os.fsync(self.fd)
        self.count += 1

    def close(self) -> None:
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            os.close(fd)


def execute(send: Callable[[bytes], tuple[int, bytes]], source_sha: str, evidence_dir: Path,
            *, window: gate.RunWindow) -> dict[str, Any]:
    from scripts.companion_ai_validation import shadow_continuation as lineage
    from scripts.companion_ai_validation.run import RequestEvidence

    fixed_cases = cases()
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    requests: RequestEvidence | None = None
    responses: RawResponses | None = None
    active = ""
    states: list[dict[str, Any]] = [{"operation": case["id"], "status": "not_attempted"}
                                    for case in fixed_cases]

    def captured_send(body: bytes) -> tuple[int, bytes]:
        if requests is None or responses is None:
            raise gate.GateError("shadow_capture_required")
        requests.record(body)
        broker._check_window()
        status, raw = send(body)
        responses.record(active, status, raw)
        return status, raw

    broker = gate.Broker(evidence_dir / "budget.jsonl", captured_send, source_sha,
                         window=window, shadow_review=True)
    outcome: dict[str, Any] = {"passed": False, "error_code": "incomplete",
                               "pass_scope": "structure_only_not_semantic_correctness",
                               "product_modified": False, "cases": states}
    try:
        requests = RequestEvidence(evidence_dir, shadow_review=True)
        responses = RawResponses(evidence_dir / "responses.jsonl")
        for case, state in zip(fixed_cases, states, strict=True):
            active = case["id"]
            state["status"] = "failed"
            token = broker.begin_stage(active)
            raw = broker.request(token, frozen_request(active))
            value = validate(gate.response_content(gate.decode(raw)), case)
            broker.close_stage()
            state.update(status="structure_valid_semantics_ungraded", review=value)
        summary = broker.summary()
        if (summary["halted"] or summary["stages_complete"] != 6
                or summary["reserved_micro_cny"] != 0):
            raise gate.GateError("shadow_not_settled")
        outcome.update(passed=True, error_code="")
    except BaseException as exc:
        broker.reject()
        code = str(exc) if isinstance(exc, gate.GateError) else "shadow_validation_blocked"
        outcome["error_code"] = code
        for state in states:
            if state["status"] == "failed":
                state["error_code"] = code
    finally:
        for writer in (requests, responses):
            if writer is not None:
                try:
                    writer.close()
                except OSError:
                    broker.reject()
                    outcome.update(passed=False, error_code="shadow_evidence_close")
        summary = broker.summary()
        try:
            broker.close()
        except gate.GateError:
            outcome.update(passed=False, error_code="journal_failure")
        report = {"schema": lineage.ADMISSION_SCHEMA, "matrix": outcome, "budget": summary,
                  "experiment": {"cases_sha256": CASES_SHA256, "requests_sha256": REQUEST_HASHES,
                                 "model": gate.MODEL, "output_limit": 768,
                                 "price_valid_until": lineage.PRICE_VALID_UNTIL,
                                 "input_micro_cny_per_token": 2, "output_micro_cny_per_token": 8,
                                 "original_drafts_unchanged": True}}
        (evidence_dir / "summary.json").write_bytes(gate.encode(report) + b"\n")
    return report
