"""Read-only, per-request quality identities from sealed Runtime evidence."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from offerpilot.evaluation.core_contracts import TrialResultV2
from offerpilot.evaluation.core_review import model_context
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.registry import digest, file_hash


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def output_inventory(row: TrialResultV2) -> dict[str, Any]:
    """Retain each verifiable reply alongside missing requests/calls/outputs."""
    private = Path(row.private_directory)
    evidence = load(private, row.evidence_hash)
    expected = 2 if row.execution_item_id == "E09.memory_withdraw" else 1
    result: dict[str, Any] = dict(
        outputs=[], observed_outputs=[], gaps=[], expected_requests=expected,
        execution_observed=None, subject_started_observed=None, request_accepted_observed=None,
        scope="Bound Runtime worker/terminal execution, separate from subject startup and HTTP submission; not raw Assistant/SSE archive",
    )
    gaps = result["gaps"]
    if any(not key.startswith("file:") for key in evidence["errors"]):
        gaps.append("sealed_quality_fragments_unavailable")
        return result
    submissions = [
        h for h in evidence["http"]
        if h.get("method") == "POST" and h.get("path") == "/api/pilot/runtime/v1/turns"
    ]
    result["subject_started_observed"] = bool(
        evidence["facts"].get("handshakes") or evidence["facts"].get("attempt_started")
    ) or None
    result["request_submission_attempts_observed"] = len(submissions)
    if len(submissions) != expected:
        gaps.append("quality_output_request_denominator_incomplete")
    bindings = []
    finals = [v for v in evidence["input_observations"] if v.get("category") == "final_input"]
    for number, frame in enumerate(finals, 1):
        try:
            _, captured = model_context(private, {**evidence, "input_observations": [frame]})
            bindings.append({**captured[0], "number": number})
        except (ValueError, OSError, KeyError, TypeError):
            gaps.append(f"call{number}:model_input_unavailable")
    database = private / "sealed.db"
    db = None
    before = None
    try:
        safe_path(database)
        before = file_hash(database)
        require(before == evidence["files"].get("sealed.db", {}).get("sha256"),
                "quality database is not bound to sealed evidence")
        db = sqlite3.connect(database.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)
    except (ValueError, OSError, sqlite3.Error):
        gaps.append("quality_sealed_database_unavailable")
    used_requests: set[str] = set()
    used_turns: set[str] = set()
    used_calls: list[int] = []
    try:
        for number, submitted in enumerate(submissions, 1):
            body, initial = submitted.get("body") or {}, submitted.get("response") or {}
            request_id, turn_id = body.get("request_id"), initial.get("turn_id")
            if not (
                isinstance(request_id, str) and request_id and request_id not in used_requests
                and isinstance(turn_id, str) and turn_id and turn_id not in used_turns
            ):
                gaps.append(f"request{number}:duplicate_or_missing_identity")
                continue
            used_requests.add(request_id)
            used_turns.add(turn_id)
            execution: dict[str, Any] = dict(
                request_id=request_id, turn_id=turn_id,
                generation=initial.get("execution_generation"),
                conversation_id=initial.get("conversation_id"),
            )
            if not all(type(execution[k]) is int and execution[k] > 0
                       for k in ("generation", "conversation_id")):
                gaps.append(f"request{number}:execution_identity_incomplete")
                continue
            result["request_accepted_observed"] = True
            if any(
                v.get("category") == "process_worker" and v.get("event") == "started"
                and v.get("execution") == execution for v in evidence["input_observations"]
            ):
                result["execution_observed"] = True
            terminals = [
                (i, h) for i, h in enumerate(evidence["http"])
                if h.get("response", {}).get("turn_id") == turn_id
                and h.get("response", {}).get("request_id") == request_id
                and h.get("response", {}).get("execution_generation") == execution["generation"]
                and h.get("response", {}).get("conversation_id") == execution["conversation_id"]
                and h.get("response", {}).get("state") == "completed"
                and isinstance(h.get("response", {}).get("terminal", {})
                               .get("response", {}).get("message"), str)
            ]
            if not terminals:
                gaps.append(f"request{number}:completed_output_missing")
                continue
            result["execution_observed"] = True
            answers = {h["response"]["terminal"]["response"]["message"] for _, h in terminals}
            if len(answers) != 1:
                gaps.append(f"request{number}:inconsistent_terminal_outputs")
                continue
            answer = answers.pop()
            if not answer.strip():
                gaps.append(f"request{number}:empty_output")
                continue
            calls = [
                b["number"] for b in bindings
                if (b.get("identity") or {}).get("execution") == execution
            ]
            observed = dict(request_number=number, answer=answer,
                            answer_sha256=sha256(answer.encode()).hexdigest())
            result["observed_outputs"].append(observed)
            if not calls or calls != sorted(set(calls)) or (
                used_calls and min(calls) <= max(used_calls)
            ):
                gaps.append(f"request{number}:model_calls_unbound_or_reordered")
                continue
            used_calls.extend(calls)
            if db is None:
                continue
            try:
                messages = db.execute(
                    "SELECT m.id FROM chat_messages m JOIN pilot_turn_messages t "
                    "ON t.message_id=m.id WHERE t.turn_id=? AND m.role='assistant' AND m.content=?",
                    (turn_id, answer),
                ).fetchall()
            except sqlite3.Error:
                messages = []
            if len(messages) != 1:
                gaps.append(f"request{number}:output_not_uniquely_joined_to_runtime")
                continue
            answer_hash = sha256(answer.encode()).hexdigest()
            sample_id = str(uuid5(
                NAMESPACE_URL, digest([*row.identity(), number, calls, answer_hash])
            ))
            index, terminal = terminals[-1]
            result["outputs"].append(dict(
                sample_id=sample_id, trial_key=list(row.identity()),
                evidence_hash=row.evidence_hash, request_number=number, call_numbers=calls,
                answer=answer, answer_sha256=answer_hash,
                user_request=body.get("message"),
                request_sha256=digest(dict(method=submitted["method"], path=submitted["path"], body=body)),
                model_input_bindings=[b for b in bindings if b["number"] in calls],
                execution=execution,
                terminal_observation_index=index, terminal_observation_hash=digest(terminal),
                database_sha256=before, database_message_id=messages[0][0],
                raw_assistant_content_status="not_captured", raw_sse_status="not_captured",
                output_scope="Runtime terminal and sealed database; no historical raw archive inferred",
            ))
    finally:
        if db is not None:
            db.close()
    if before is not None:
        try:
            safe_path(database)
            require(file_hash(database) == before, "quality database changed during observation")
        except (ValueError, OSError):
            gaps.append("quality_database_changed_during_observation")
            result["outputs"] = []
    if used_calls != list(range(1, len(finals)+1)):
        gaps.append("quality_model_call_coverage_incomplete")
    return result


def bound_outputs(row: TrialResultV2) -> list[dict[str, Any]]:
    """Strict acceptance reader; reporting uses the non-lossy inventory above."""
    inventory = output_inventory(row)
    require(not inventory["gaps"], "quality output evidence incomplete: " + str(inventory["gaps"]))
    return list(inventory["outputs"])
