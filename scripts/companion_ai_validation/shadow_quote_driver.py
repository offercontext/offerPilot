"""Fixed v2 quote review adapter; structural evidence only, never product rewriting."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from experiments.shadow_quote_v2.harness import ProtocolError, validate_response, _input
from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import shadow_driver as old
from scripts.companion_ai_validation.shadow_driver import RawResponses

CASES_SHA256 = old.CASES_SHA256
ASSETS = Path(__file__).with_name("shadow_quote_requests")
REQUEST_HASHES = {
    "s01": "31b7ce6e7fa388d001582a4f0ba11cda6bf8ed6938563805a0cb5689ddafb805",
    "s02": "7f733d8d64e0772b105971c6eea4acb5b9e7eed08bf5ce5baf34be8874bdbbc5",
    "s03": "2e20e7b0b3c8dc5f046d41674324a34ea65ce1fdbab56d570a1159f5a44d62c3",
    "s04": "42e16b7f501eb5ac694bf1a5bb7fc43b81122267a9cac72a474b2d37958263d4",
    "s05": "5b62e3586a7acb99005e59efedb63ab3c5e0d32f11b4162aa3c8ddba4c53fd00",
    "s06": "bf99222eed215372faa9f4a36bb0d5a1a4cdb5737a260a5b8500b91a69af70a4",
}


def frozen_request(operation: str) -> bytes:
    if operation not in REQUEST_HASHES:
        raise gate.GateError("shadow_operation")
    return old._pinned(ASSETS / (operation + ".json"), REQUEST_HASHES[operation])


def cases() -> list[dict[str, Any]]:
    result = old.cases()
    for case in result:
        payload = gate.decode(frozen_request(case["id"]))
        if json.loads(payload["messages"][1]["content"]) != _input(case):
            raise gate.GateError("shadow_case_request_mismatch")
    return result


def validate(content: str, case: dict[str, Any]) -> dict[str, Any]:
    if type(content) is not str:
        raise gate.GateError("shadow_response_size")
    try:
        return validate_response(content.encode("utf-8"), case)
    except (ProtocolError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, ProtocolError) else "invalid_unicode"
        raise gate.GateError("shadow_quote_" + code) from None


def execute(send: Callable[[bytes], tuple[int, bytes]], source_sha: str, evidence_dir: Path,
            *, window: gate.RunWindow) -> dict[str, Any]:
    from scripts.companion_ai_validation import shadow_quote_continuation as lineage
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
                         window=window, shadow_quote_v2=True)
    outcome: dict[str, Any] = {"passed": False, "error_code": "incomplete",
                               "pass_scope": "structure_only_not_semantic_correctness",
                               "product_modified": False, "cases": states}
    try:
        requests = RequestEvidence(evidence_dir, shadow_quote_v2=True)
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
                  "experiment": {"protocol": "shadow-quote-v2", "cases_sha256": CASES_SHA256, "requests_sha256": REQUEST_HASHES,
                                 "model": gate.MODEL, "output_limit": 768,
                                 "price_valid_until": lineage.PRICE_VALID_UNTIL,
                                 "input_micro_cny_per_token": 2, "output_micro_cny_per_token": 8,
                                 "original_drafts_unchanged": True}}
        (evidence_dir / "summary.json").write_bytes(gate.encode(report) + b"\n")
    return report
