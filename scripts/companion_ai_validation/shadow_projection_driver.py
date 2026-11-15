"""Pinned input-only projection; original quote-v2 protocol and production safeguards."""
from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
from typing import Any, Callable
import zipfile

from experiments.shadow_input_projection.projection import project, quote_origin
from experiments.shadow_quote_v2.harness import _input
from scripts.companion_ai_validation import broker as gate
from scripts.companion_ai_validation import shadow_quote_driver as old
from scripts.companion_ai_validation import shadow_driver, shadow_continuation
from scripts.companion_ai_validation.shadow_driver import RawResponses

VERSION = "input-only-projection-1"
CASES_SHA256 = old.CASES_SHA256
ASSETS = Path(__file__).with_name("shadow_projection_requests")
MAPPING_PATH = Path(__file__).resolve().parents[2] / "experiments/shadow_input_projection/source-mapping.json"
MAPPING_SHA256 = "b0bbce49accc1f58586dbbf372b9a6637c30700579489e8664c77ba732e1ad17"
REQUEST_HASHES = {
    "s01": "31b7ce6e7fa388d001582a4f0ba11cda6bf8ed6938563805a0cb5689ddafb805",
    "s02": "7f733d8d64e0772b105971c6eea4acb5b9e7eed08bf5ce5baf34be8874bdbbc5",
    "s03": "3c7b7be6d25465483dc79aba0133f8cd65e833cce7fb4c0e4f45134a2738704c",
    "s04": "eb58ebdf65707fc1e98fbbc4918e27713aa7d6ff10cceb19da78130178883c6c",
    "s05": "5b62e3586a7acb99005e59efedb63ab3c5e0d32f11b4162aa3c8ddba4c53fd00",
    "s06": "bf99222eed215372faa9f4a36bb0d5a1a4cdb5737a260a5b8500b91a69af70a4",
}


def frozen_request(operation: str) -> bytes:
    if operation not in REQUEST_HASHES:
        raise gate.GateError("shadow_operation")
    return shadow_driver._pinned(ASSETS / (operation + ".json"), REQUEST_HASHES[operation])


def projection_mappings() -> dict[str, Any]:
    metadata = gate.decode(shadow_driver._pinned(MAPPING_PATH, MAPPING_SHA256))
    archive_raw = shadow_driver._pinned(shadow_continuation.EVIDENCE_PATH, shadow_continuation.ARCHIVE_SHA256)
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        original = json.loads(archive.read("responses.jsonl").splitlines()[1])
    if (metadata["version"] != VERSION or metadata["source_archive_sha256"] != shadow_continuation.ARCHIVE_SHA256
            or metadata["response_line_1based"] != 2
            or metadata["source_facts_before_projection"] != original["source_facts_before_projection"]
            or metadata["original_reference"] != original["expected_context"]):
        raise gate.GateError("projection_provenance")
    expected = project(metadata["original_reference"], metadata["segments"])
    if metadata["mappings"] != {"s03": expected, "s04": expected} or expected["merged_count"] != 1:
        raise gate.GateError("projection_mapping")
    mappings: dict[str, Any] = metadata["mappings"]
    return mappings


def cases() -> list[dict[str, Any]]:
    originals = old.cases()
    result = deepcopy(originals)
    mappings = projection_mappings()
    for original, case in zip(originals, result, strict=True):
        if case["id"] in mappings:
            source = next(s for s in case["sources"] if s["id"] == "reference")
            original_text = "".join(row["original"]["text"] for row in mappings[case["id"]]["mappings"])
            if source["text"] != original_text:
                raise gate.GateError("projection_original_source")
            source["text"] = mappings[case["id"]]["view"]
        raw = frozen_request(case["id"])
        baseline = old.frozen_request(case["id"])
        expected = baseline
        if case["id"] in mappings:
            payload = gate.decode(baseline)
            payload["messages"][1]["content"] = gate.encode(_input(case)).decode()
            expected = gate.encode(payload)
        if raw != expected:
            raise gate.GateError("projection_request_scope")
    return result


def validate(content: str, case: dict[str, Any]) -> dict[str, Any]:
    result = old.validate(content, case)
    mappings = projection_mappings()
    result["original_evidence"] = [quote_origin(mappings[case["id"]], quote)
        if case["id"] in mappings and quote["source_id"] == "reference" else deepcopy(quote)
        for quote in result["evidence"]]
    return result


def execute(send: Callable[[bytes], tuple[int, bytes]], source_sha: str, evidence_dir: Path,
            *, window: gate.RunWindow) -> dict[str, Any]:
    from scripts.companion_ai_validation import shadow_projection_continuation as lineage
    from scripts.companion_ai_validation.run import RequestEvidence

    fixed_cases = cases()
    fixed_mappings = projection_mappings()
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    requests: RequestEvidence | None = None
    responses: RawResponses | None = None
    active = ""
    attempted = 0
    states: list[dict[str, Any]] = [{"operation": case["id"], "status": "not_attempted"}
                                    for case in fixed_cases]

    def captured_send(body: bytes) -> tuple[int, bytes]:
        nonlocal attempted
        if requests is None or responses is None:
            raise gate.GateError("shadow_capture_required")
        requests.record(body)
        broker._check_window()
        attempted += 1
        status, raw = send(body)
        responses.record(active, status, raw)
        return status, raw

    broker = gate.Broker(evidence_dir / "budget.jsonl", captured_send, source_sha,
                         window=window, shadow_projection=True)
    outcome: dict[str, Any] = {"passed": False, "error_code": "incomplete",
                               "pass_scope": "structure_only_not_semantic_correctness",
                               "product_modified": False, "cases": states}
    try:
        requests = RequestEvidence(evidence_dir, shadow_projection=True)
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
                  "experiment": {"protocol": "shadow-quote-v2", "projection_version": VERSION,
                                 "projection_mapping_sha256": MAPPING_SHA256,
                                 "projection_mappings": fixed_mappings,
                                 "planned": 6, "attempted": attempted,
                                 "incomplete": not outcome["passed"],
                                 "not_attempted": sum(s["status"] == "not_attempted" for s in states),
                                 "structurally_valid": sum(s["status"] == "structure_valid_semantics_ungraded" for s in states),
                                 "semantic_quality_established": False,
                                 "s01_label_disagreement": "historical_contradicted_disputed_unknown_or_ambiguous_reasonable_not_relabelled",
                                 "cases_sha256": CASES_SHA256, "requests_sha256": REQUEST_HASHES,
                                 "model": gate.MODEL, "output_limit": 768,
                                 "price_valid_until": lineage.PRICE_VALID_UNTIL,
                                 "input_micro_cny_per_token": 2, "output_micro_cny_per_token": 8,
                                 "original_drafts_unchanged": True}}
        (evidence_dir / "summary.json").write_bytes(gate.encode(report) + b"\n")
    return report
