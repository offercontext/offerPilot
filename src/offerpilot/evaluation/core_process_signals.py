"""Three-state proposal observations. Negative claims require closed, bound coverage."""

from __future__ import annotations

from typing import Any

from offerpilot.evaluation.registry import digest

IDENTITY = ("model_call_id", "segment_id", "surface_fingerprint")


def _identity(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, dict) or not all(
        isinstance(value.get(k), str) and value[k].strip() for k in IDENTITY
    ):
        return None
    return tuple(value[k] for k in IDENTITY)


def _execution(value: Any) -> Any:
    return value.get("execution") if isinstance(value, dict) else None


def _position(value: dict[str, Any]) -> bool:
    return all(type(value.get(k)) is int and value[k] > 0 for k in ("epoch", "seq"))


def _public_event(frame: dict[str, Any], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        i + 1
        for i, source in enumerate(inputs)
        if _identity(frame.get("identity")) is not None
        and _identity(frame.get("identity")) == _identity(source.get("identity"))
        and frame.get("epoch") == source.get("epoch")
    ]
    public = {
        k: frame[k]
        for k in (
            "event",
            "epoch",
            "seq",
            "index",
            "tool_call_id",
            "tool_name",
            "persistence",
            "outcome",
            "pending",
        )
        if k in frame
    }
    public.update(call_number=matches[0] if len(matches) == 1 else None, event_hash=digest(frame))
    if isinstance(frame.get("tool_calls"), list):
        public["tool_calls"] = [
            {
                k: call[k]
                for k in ("tool_call_id", "tool_name", "operation_kind", "confirmation_required")
                if k in call
            }
            for call in frame["tool_calls"]
            if isinstance(call, dict)
        ]
    return public


def process_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    observations = evidence.get("input_observations", [])
    frames = evidence.get(
        "process_observations", [v for v in observations if v.get("category") == "process"]
    )
    worker_frames = evidence.get(
        "process_workers", [v for v in observations if v.get("category") == "process_worker"]
    )
    requests, covered = [], set()
    positive_write = positive_pending = False
    all_complete = bool(frames) and evidence.get("facts", {}).get("control_closed") is True
    inputs = [
        v for v in evidence.get("input_observations", []) if v.get("category") == "final_input"
    ]
    submissions = [
        v
        for v in evidence.get("http", [])
        if v.get("method") == "POST" and v.get("path") == "/api/pilot/runtime/v1/turns"
    ]
    for number, request in enumerate(submissions, 1):
        body, response = request.get("body") or {}, request.get("response") or {}
        expected: dict[str, Any] = dict(
            request_id=body.get("request_id"),
            turn_id=response.get("turn_id"),
            generation=response.get("execution_generation"),
            conversation_id=response.get("conversation_id"),
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for index, frame in enumerate(frames):
            if (
                frame.get("execution") == expected
                and all(
                    isinstance(expected[k], str) and expected[k].strip()
                    for k in ("request_id", "turn_id")
                )
                and all(
                    type(expected[k]) is int and expected[k] > 0
                    for k in ("generation", "conversation_id")
                )
                and frame.get("scope_id")
            ):
                covered.add(index)
                groups.setdefault(frame["scope_id"], []).append(frame)
        gaps, calls, intervals = [], [], []
        workers = [v for v in worker_frames if v.get("execution") == expected]
        worker_bound = (
            len(workers) == 2
            and all(_position(v) for v in workers)
            and workers[0]["epoch"] == workers[1]["epoch"]
            and workers[0]["seq"] < workers[1]["seq"]
        )
        if (
            not worker_bound
            or [v.get("event") for v in workers] != ["started", "finished"]
            or any(v.get("version") != "process-signals-v1" for v in workers)
            or workers[-1].get("worker_done") is not True
            or workers[-1].get("state") not in {"completed", "waiting_confirmation"}
        ):
            gaps.append("worker_not_closed")
        if not groups:
            gaps.append("request_scope_missing")
        for values in groups.values():
            valid = all(
                type(v.get("epoch")) is int
                and v["epoch"] > 0
                and isinstance(v.get("segment_id"), str)
                and v["segment_id"].strip()
                for v in values
            )
            valid = (
                valid
                and len({v["epoch"] for v in values}) == 1
                and len({v["segment_id"] for v in values}) == 1
            )
            if not valid or any(v.get("version") != "process-signals-v1" for v in values):
                gaps.append("scope_identity_or_version")
                continue
            if (
                not worker_bound
                or any(v.get("epoch") != workers[0].get("epoch") for v in workers + values)
                or not (
                    workers[0].get("seq", 0)
                    < values[0].get("seq", 0)
                    <= values[-1].get("seq", 0)
                    < workers[-1].get("seq", 0)
                )
            ):
                gaps.append("worker_interval_unbound")
            if (
                [v.get("index") for v in values] != list(range(1, len(values) + 1))
                or any(type(v.get("seq")) is not int or v["seq"] <= 0 for v in values)
                or [v["seq"] for v in values] != sorted({v["seq"] for v in values})
            ):
                gaps.append("scope_sequence_gap")
            if (
                values[0].get("event") != "started"
                or values[-1].get("event") != "finished"
                or values[-1].get("outcome") != "completed"
            ):
                gaps.append("scope_not_completed")
            allowed = {"started", "model_input", "model_result", "pending_created", "finished"}
            if any(v.get("event") not in allowed for v in values):
                gaps.append("unknown_event")
            model_inputs = [v for v in values if v.get("event") == "model_input"]
            model_results = [v for v in values if v.get("event") == "model_result"]
            identities = [_identity(v.get("identity")) for v in model_inputs]
            if (
                not identities
                or None in identities
                or len(set(identities)) != len(identities)
                or identities != [_identity(v.get("identity")) for v in model_results]
                or any(
                    identity is not None and identity[1] != values[0]["segment_id"]
                    for identity in identities
                )
            ):
                gaps.append("model_result_gap")
            for frame in model_inputs:
                matches = [
                    i + 1
                    for i, source in enumerate(inputs)
                    if _identity(source.get("identity")) == _identity(frame.get("identity"))
                    and _identity(frame.get("identity")) is not None
                    and source.get("epoch") == frame["epoch"]
                    and _execution(source.get("identity")) == expected
                ]
                if len(matches) != 1:
                    gaps.append("model_input_unbound")
                else:
                    calls.extend(matches)
            for frame in model_results:
                if (
                    _identity(frame.get("identity")) not in identities
                    or _identity(frame.get("identity")) is None
                ):
                    continue
                raw_calls = frame.get("tool_calls")
                if not isinstance(raw_calls, list):
                    gaps.append("tool_calls_missing")
                    continue
                for call in raw_calls:
                    if not isinstance(call, dict):
                        gaps.append("tool_kind_unknown")
                        continue
                    if call.get("operation_kind") == "transactional_write":
                        positive_write = True
                    elif call.get("operation_kind") != "read":
                        gaps.append("tool_kind_unknown")
            pending = [v for v in values if v.get("event") == "pending_created"]
            for frame in pending:
                if (
                    _identity(frame.get("identity")) in identities
                    and _identity(frame.get("identity")) is not None
                    and frame.get("tool_call_id")
                    and frame.get("tool_name")
                ):
                    positive_pending = True
                else:
                    gaps.append("pending_unbound")
            if values[-1].get("pending") is True and not pending:
                gaps.append("pending_creation_missing")
            if type(values[-1].get("pending")) is not bool:
                gaps.append("pending_result_unknown")
            intervals.append(
                dict(
                    epoch=values[0]["epoch"],
                    start=values[0]["seq"],
                    end=values[-1]["seq"],
                    coordinate_space="subject_control_journal",
                )
            )
        expected_calls = [
            i + 1
            for i, source in enumerate(inputs)
            if _execution(source.get("identity")) == expected
        ]
        if not expected_calls or sorted(calls) != expected_calls:
            gaps.append("request_model_coverage_gap")
        complete = (
            not gaps and bool(groups) and evidence.get("facts", {}).get("control_closed") is True
        )
        all_complete = all_complete and complete
        requests.append(
            dict(
                request_number=number,
                request_sha256=digest(
                    dict(method=request.get("method"), path=request.get("path"), body=body)
                ),
                request_hash_basis="canonical-method-path-body",
                call_numbers=sorted(set(calls)),
                intervals=intervals,
                events=[
                    _public_event(frame, inputs) for group in groups.values() for frame in group
                ],
                worker_interval=None
                if not worker_bound
                else dict(
                    epoch=workers[0]["epoch"],
                    start=workers[0]["seq"],
                    end=workers[1]["seq"],
                    worker_done=workers[-1].get("worker_done"),
                    state=workers[-1].get("state"),
                    coordinate_space="subject_control_journal",
                ),
                complete=complete,
                gaps=sorted(set(gaps)),
            )
        )
    if not submissions or len(covered) != len(frames):
        all_complete = False
    return dict(
        version="process-signals-v1",
        complete=all_complete,
        scope="Assistant model proposals before HITL; Pending objects before persistence. No statement about successful persistence or external effects.",
        signals=dict(
            write_intent_present=True if positive_write else False if all_complete else None,
            pending_present=True if positive_pending else False if all_complete else None,
        ),
        requests=requests,
    )
