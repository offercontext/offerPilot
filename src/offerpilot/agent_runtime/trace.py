from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from offerpilot.agent_runtime.events import (
    EventDraft,
    JournalEventValidationError,
    validate_context_manifest_json,
    validate_event_draft,
)
from offerpilot.models import AgentContextSnapshot, AgentEvent, AgentRun
from offerpilot.repositories.agent_runs import AgentRunRepository, RunStatus

CompletionStatus = Literal["terminal", "suspended", "open", "stale_open"]
IntegrityStatus = Literal[
    "healthy",
    "known_degraded",
    "sequence_gap",
    "semantic_anomaly",
]


class AgentRunTraceNotFound(LookupError):
    pass


@dataclass(frozen=True)
class EventTrace:
    seq: int
    event_type: str
    execution_segment_id: str
    model_step: int | None
    model_call_id: str | None
    source_ref_type: str | None
    source_ref_id: str | None
    fingerprint_key_id: str | None
    facts: dict[str, object]
    telemetry: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class ContextSnapshotTrace:
    snapshot_id: str
    snapshot_key: str
    snapshot_kind: str
    model_step: int | None
    model_call_id: str | None
    manifest: dict[str, object]
    manifest_digest: str
    logical_input_fingerprint: str
    fingerprint_key_id: str
    estimated_token_count: int | None
    token_estimator_name: str | None
    token_estimator_version: str | None
    created_at: datetime


@dataclass(frozen=True)
class ModelStepTrace:
    model_step: int | None
    model_call_id: str
    snapshot_id: str | None
    requested_seq: int | None
    completed_seq: int | None
    failed_seq: int | None


@dataclass(frozen=True)
class ToolTrace:
    tool_call_id: str
    tool_name: str | None
    tool_kind: str | None
    proposed_seq: int | None
    started_seq: int | None
    completed_seq: int | None
    failed_seq: int | None


@dataclass(frozen=True)
class ApprovalTrace:
    tool_call_id: str
    confirmation_attempt_id: str | None
    requested_seq: int | None
    decided_seq: int | None
    decision: str | None


@dataclass(frozen=True)
class SegmentTrace:
    execution_segment_id: str
    started_seq: int | None
    finished_seq: int | None
    request_kind: str | None
    transport_mode: str | None
    execution_path: str | None
    outcome: str | None
    contexts: tuple[ContextSnapshotTrace, ...]
    model_steps: tuple[ModelStepTrace, ...]
    tools: tuple[ToolTrace, ...]
    approvals: tuple[ApprovalTrace, ...]
    events: tuple[EventTrace, ...]


@dataclass(frozen=True)
class AgentRunTrace:
    run_id: str
    conversation_id: int
    input_message_id: int | None
    origin_kind: str
    initial_context_type: str
    initial_context_entity_id: str | None
    initial_context_ref_fingerprint: str | None
    fingerprint_key_id: str
    initial_transport_mode: str
    initial_route_kind: str
    lifecycle_status: RunStatus
    completion_status: CompletionStatus
    integrity_status: IntegrityStatus
    recording_status: str
    recording_error_count: int
    waiting_tool_call_id: str | None
    failure_code: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    segments: tuple[SegmentTrace, ...]
    anomalies: tuple[str, ...]


def reconstruct_agent_run(
    repository: AgentRunRepository,
    run_id: str,
    *,
    as_of: datetime,
    stale_after: timedelta | None,
) -> AgentRunTrace:
    """Read Journal rows only; never mutate or drive recovery."""

    if stale_after is not None and stale_after < timedelta(0):
        raise ValueError("stale threshold cannot be negative")
    rows = repository.read_run_journal(run_id)
    run = rows.run
    if run is None:
        raise AgentRunTraceNotFound(run_id)
    persisted_events = list(rows.events)
    snapshots = list(rows.snapshots)
    events, payload_anomalies = _event_views(persisted_events)

    sequence_anomalies = _sequence_anomalies(run, events)
    completion_status = _completion_status(
        run,
        events,
        as_of=_as_utc(as_of),
        stale_after=stale_after,
    )
    semantic_anomalies = (
        payload_anomalies
        + _semantic_anomalies(
            run,
            events,
            completion_status=completion_status,
        )
        + _snapshot_anomalies(run, events, snapshots)
    )
    degraded_anomalies = (
        ["recording_degraded"] if run.recording_status == "degraded" else []
    )
    anomalies = tuple(
        _deduplicate(sequence_anomalies + semantic_anomalies + degraded_anomalies)
    )
    if sequence_anomalies:
        integrity_status: IntegrityStatus = "sequence_gap"
    elif semantic_anomalies:
        integrity_status = "semantic_anomaly"
    elif degraded_anomalies:
        integrity_status = "known_degraded"
    else:
        integrity_status = "healthy"

    return AgentRunTrace(
        run_id=run.id,
        conversation_id=run.conversation_id,
        input_message_id=run.input_message_id,
        origin_kind=run.origin_kind,
        initial_context_type=run.initial_context_type,
        initial_context_entity_id=run.initial_context_entity_id,
        initial_context_ref_fingerprint=run.initial_context_ref_fingerprint,
        fingerprint_key_id=run.fingerprint_key_id,
        initial_transport_mode=run.initial_transport_mode,
        initial_route_kind=run.initial_route_kind,
        lifecycle_status=cast(RunStatus, run.status),
        completion_status=completion_status,
        integrity_status=integrity_status,
        recording_status=run.recording_status,
        recording_error_count=run.recording_error_count,
        waiting_tool_call_id=run.waiting_tool_call_id,
        failure_code=run.failure_code,
        started_at=_as_utc(run.started_at),
        updated_at=_as_utc(run.updated_at),
        finished_at=_as_utc(run.finished_at) if run.finished_at is not None else None,
        segments=_segment_views(events, snapshots),
        anomalies=anomalies,
    )


def _event_views(rows: list[AgentEvent]) -> tuple[list[EventTrace], list[str]]:
    events: list[EventTrace] = []
    anomalies: list[str] = []
    for row in sorted(rows, key=lambda item: (item.seq, item.id)):
        try:
            payload = json.loads(row.payload_json)
            facts = payload["facts"]
            telemetry = payload["telemetry"]
            if type(facts) is not dict or type(telemetry) is not dict:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            facts = {}
            telemetry = {}
            anomalies.append(f"invalid_event_payload:{row.seq}")
        try:
            validate_event_draft(
                EventDraft(
                    event_type=row.event_type,
                    schema_version=row.schema_version,
                    execution_segment_id=row.execution_segment_id,
                    model_step=row.model_step,
                    model_call_id=row.model_call_id,
                    source_ref_type=row.source_ref_type,
                    source_ref_id=row.source_ref_id,
                    fingerprint_key_id=row.fingerprint_key_id,
                    payload_json=row.payload_json,
                    payload_digest=row.payload_digest,
                    fact_digest=row.fact_digest,
                    dedupe_key=row.dedupe_key,
                )
            )
        except JournalEventValidationError:
            anomalies.append(f"invalid_event_contract:{row.seq}")
        events.append(
            EventTrace(
                seq=row.seq,
                event_type=row.event_type,
                execution_segment_id=row.execution_segment_id,
                model_step=row.model_step,
                model_call_id=row.model_call_id,
                source_ref_type=row.source_ref_type,
                source_ref_id=row.source_ref_id,
                fingerprint_key_id=row.fingerprint_key_id,
                facts=cast(dict[str, object], facts),
                telemetry=cast(dict[str, object], telemetry),
                created_at=_as_utc(row.created_at),
            )
        )
    return events, anomalies


def _sequence_anomalies(run: AgentRun, events: list[EventTrace]) -> list[str]:
    anomalies: list[str] = []
    expected = 1
    for event in events:
        while expected < event.seq:
            anomalies.append(f"sequence_gap:{expected}")
            expected += 1
        if event.seq < expected:
            anomalies.append(f"sequence_order_invalid:{event.seq}")
        else:
            expected = event.seq + 1
    while expected <= run.last_seq:
        anomalies.append(f"sequence_gap:{expected}")
        expected += 1
    if events and run.last_seq < events[-1].seq:
        anomalies.append(f"last_seq_mismatch:{run.last_seq}:{events[-1].seq}")
    if not events and run.last_seq != 0:
        anomalies.append(f"last_seq_mismatch:{run.last_seq}:0")
    return anomalies


def _completion_status(
    run: AgentRun,
    events: list[EventTrace],
    *,
    as_of: datetime,
    stale_after: timedelta | None,
) -> CompletionStatus:
    if run.status in {"completed", "failed", "cancelled", "timed_out"}:
        return "terminal"
    if run.status == "waiting_confirmation":
        return "suspended"
    if stale_after is None:
        return "open"
    activity = _as_utc(run.updated_at)
    if events:
        activity = max(activity, max(event.created_at for event in events))
    return "stale_open" if as_of - activity > stale_after else "open"


def _semantic_anomalies(
    run: AgentRun,
    events: list[EventTrace],
    *,
    completion_status: CompletionStatus,
) -> list[str]:
    anomalies: list[str] = []
    for event in events:
        if (
            event.fingerprint_key_id is not None
            and event.fingerprint_key_id != run.fingerprint_key_id
        ):
            anomalies.append(f"fingerprint_key_domain_mismatch:{event.seq}")
    segment_rows: dict[str, list[EventTrace]] = {}
    for event in events:
        segment_rows.setdefault(_event_segment_id(event), []).append(event)

    for segment_id, segment_events in segment_rows.items():
        starts = [event for event in segment_events if event.event_type == "segment.started"]
        finishes = [event for event in segment_events if event.event_type == "segment.finished"]
        if len(starts) > 1:
            anomalies.append(f"segment_multiple_starts:{segment_id}")
        if len(finishes) > 1:
            anomalies.append(f"segment_multiple_finishes:{segment_id}")
        if finishes and not starts:
            anomalies.append(f"segment_finish_without_start:{segment_id}")
        if run.status != "running" and starts and not finishes:
            anomalies.append(f"segment_missing_finish:{segment_id}")
        if starts and finishes and finishes[0].seq < starts[0].seq:
            anomalies.append(f"segment_finish_before_start:{segment_id}")

    model_events: dict[str, list[EventTrace]] = {}
    for event in events:
        if event.event_type.startswith("model.") and event.model_call_id is not None:
            model_events.setdefault(event.model_call_id, []).append(event)
    check_incomplete = run.status != "running" or completion_status == "stale_open"
    for model_call_id, call_events in model_events.items():
        requested = [event for event in call_events if event.event_type == "model.requested"]
        outcomes = [
            event
            for event in call_events
            if event.event_type in {"model.completed", "model.failed"}
        ]
        if outcomes and not requested:
            anomalies.append(f"model_completion_without_request:{model_call_id}")
        if check_incomplete and requested and not outcomes:
            anomalies.append(f"model_call_incomplete:{model_call_id}")
        if len(requested) > 1 or len(outcomes) > 1:
            anomalies.append(f"model_call_event_conflict:{model_call_id}")
        if requested and outcomes and outcomes[0].seq < requested[0].seq:
            anomalies.append(f"model_completion_before_request:{model_call_id}")

    tool_events: dict[str, list[EventTrace]] = {}
    for event in events:
        tool_call_id = event.facts.get("tool_call_id")
        if event.event_type.startswith("tool.") and type(tool_call_id) is str:
            tool_events.setdefault(tool_call_id, []).append(event)
    for tool_call_id, call_events in tool_events.items():
        starts = [event for event in call_events if event.event_type == "tool.started"]
        outcomes = [
            event
            for event in call_events
            if event.event_type in {"tool.completed", "tool.failed"}
        ]
        if outcomes and not starts:
            anomalies.append(f"tool_completion_without_start:{tool_call_id}")
        if check_incomplete and starts and not outcomes:
            anomalies.append(f"tool_call_incomplete:{tool_call_id}")
        if len(starts) > 1 or len(outcomes) > 1:
            anomalies.append(f"tool_call_event_conflict:{tool_call_id}")
        if starts and outcomes and outcomes[0].seq < starts[0].seq:
            anomalies.append(f"tool_completion_before_start:{tool_call_id}")

    lifecycle_event_types = {
        "run.waiting_confirmation",
        "run.resumed",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.timed_out",
    }
    lifecycle_events = [
        event for event in events if event.event_type in lifecycle_event_types
    ]
    latest_lifecycle_event = lifecycle_events[-1] if lifecycle_events else None
    terminal_types = {event.event_type for event in lifecycle_events}
    if run.status in {"completed", "failed", "cancelled", "timed_out"}:
        expected = f"run.{run.status}"
        terminal_events = [event for event in events if event.event_type == expected]
        if not terminal_events:
            anomalies.append(f"terminal_event_missing:{run.status}")
        elif latest_lifecycle_event is not None and latest_lifecycle_event.event_type != expected:
            anomalies.append(f"lifecycle_event_conflict:{run.status}")
        for event in terminal_events:
            if event.facts != {
                "agent_run_id": run.id,
                "failure_code": run.failure_code,
                "status": run.status,
            }:
                anomalies.append(f"terminal_projection_mismatch:{event.seq}")
            if not any(
                finish.event_type == "segment.finished"
                and finish.execution_segment_id == event.execution_segment_id
                and finish.facts
                == {"outcome": run.status, "terminal_run_status": run.status}
                for finish in events
            ):
                anomalies.append(
                    f"terminal_segment_finish_missing:{event.execution_segment_id}"
                )
        if run.waiting_tool_call_id is not None:
            anomalies.append("terminal_waiting_identity_present")
        if run.finished_at is None:
            anomalies.append("terminal_finished_at_missing")
        conflicting_terminal_types = {
            "run.completed",
            "run.failed",
            "run.cancelled",
            "run.timed_out",
        } - {expected}
        for other in sorted(conflicting_terminal_types):
            if other in terminal_types:
                anomalies.append(f"terminal_event_conflict:{other}")
    elif run.status == "waiting_confirmation":
        waiting_events = [
            event for event in events if event.event_type == "run.waiting_confirmation"
        ]
        if not waiting_events:
            anomalies.append("waiting_event_missing")
        elif (
            latest_lifecycle_event is not None
            and latest_lifecycle_event.event_type != "run.waiting_confirmation"
        ):
            anomalies.append("lifecycle_event_conflict:waiting_confirmation")
        if run.waiting_tool_call_id is None:
            anomalies.append("waiting_identity_missing")
        elif (
            waiting_events
            and waiting_events[-1].facts.get("tool_call_id") != run.waiting_tool_call_id
        ):
            anomalies.append("waiting_projection_mismatch")
        for event in waiting_events:
            if not any(
                finish.event_type == "segment.finished"
                and finish.execution_segment_id == event.execution_segment_id
                and finish.facts
                == {"outcome": "suspended", "terminal_run_status": None}
                for finish in events
            ):
                anomalies.append(
                    f"waiting_segment_finish_missing:{event.execution_segment_id}"
                )
        if run.finished_at is not None:
            anomalies.append("waiting_finished_at_present")
    elif any(
        event_type in terminal_types
        for event_type in {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}
    ) or (
        latest_lifecycle_event is not None
        and latest_lifecycle_event.event_type != "run.resumed"
    ):
        anomalies.append("lifecycle_event_conflict:running")
    elif run.finished_at is not None or run.waiting_tool_call_id is not None:
        anomalies.append("running_projection_mismatch")
    return anomalies


def _segment_views(
    events: list[EventTrace],
    snapshots: list[AgentContextSnapshot],
) -> tuple[SegmentTrace, ...]:
    snapshot_views: dict[str, list[ContextSnapshotTrace]] = {}
    for snapshot in snapshots:
        try:
            manifest = json.loads(snapshot.manifest_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            manifest = {}
        snapshot_views.setdefault(snapshot.execution_segment_id, []).append(
            ContextSnapshotTrace(
                snapshot_id=snapshot.id,
                snapshot_key=snapshot.snapshot_key,
                snapshot_kind=snapshot.snapshot_kind,
                model_step=snapshot.model_step,
                model_call_id=snapshot.model_call_id,
                manifest=cast(dict[str, object], manifest),
                manifest_digest=snapshot.manifest_digest,
                logical_input_fingerprint=snapshot.logical_input_fingerprint,
                fingerprint_key_id=snapshot.fingerprint_key_id,
                estimated_token_count=snapshot.estimated_token_count,
                token_estimator_name=snapshot.token_estimator_name,
                token_estimator_version=snapshot.token_estimator_version,
                created_at=_as_utc(snapshot.created_at),
            )
        )

    by_segment: dict[str, list[EventTrace]] = {}
    order: list[str] = []
    for event in events:
        segment_id = _event_segment_id(event)
        if segment_id not in by_segment:
            by_segment[segment_id] = []
            order.append(segment_id)
        by_segment[segment_id].append(event)
    for segment_id in snapshot_views:
        if segment_id not in by_segment:
            by_segment[segment_id] = []
            order.append(segment_id)

    return tuple(
        _segment_view(
            segment_id,
            by_segment[segment_id],
            snapshot_views.get(segment_id, []),
        )
        for segment_id in order
    )


def _snapshot_anomalies(
    run: AgentRun,
    events: list[EventTrace],
    snapshots: list[AgentContextSnapshot],
) -> list[str]:
    anomalies: list[str] = []
    snapshot_ids: set[str] = set()
    model_call_ids: set[str] = set()
    captured_ids: set[str] = set()
    for event in events:
        snapshot_id = event.facts.get("snapshot_id")
        if event.event_type == "context.captured" and type(snapshot_id) is str:
            captured_ids.add(snapshot_id)
    for snapshot in snapshots:
        snapshot_ids.add(snapshot.id)
        if snapshot.fingerprint_key_id != run.fingerprint_key_id:
            anomalies.append(f"snapshot_key_domain_mismatch:{snapshot.id}")
        try:
            validate_context_manifest_json(snapshot.manifest_json)
        except JournalEventValidationError:
            anomalies.append(f"invalid_context_manifest:{snapshot.id}")
        if snapshot.id not in captured_ids:
            anomalies.append(f"snapshot_missing_event:{snapshot.id}")
        if snapshot.model_call_id is not None:
            if snapshot.model_call_id in model_call_ids:
                anomalies.append(f"duplicate_model_snapshot:{snapshot.model_call_id}")
            model_call_ids.add(snapshot.model_call_id)
    for snapshot_id in sorted(captured_ids - snapshot_ids):
        anomalies.append(f"context_event_missing_snapshot:{snapshot_id}")
    return anomalies


def _segment_view(
    segment_id: str,
    events: list[EventTrace],
    contexts: list[ContextSnapshotTrace],
) -> SegmentTrace:
    started = next((event for event in events if event.event_type == "segment.started"), None)
    finished = next((event for event in events if event.event_type == "segment.finished"), None)
    model_groups: dict[str, list[EventTrace]] = {}
    tool_groups: dict[str, list[EventTrace]] = {}
    approval_groups: dict[str, list[EventTrace]] = {}
    for event in events:
        if event.event_type.startswith("model.") and event.model_call_id is not None:
            model_groups.setdefault(event.model_call_id, []).append(event)
        tool_call_id = event.facts.get("tool_call_id")
        if type(tool_call_id) is str:
            if event.event_type.startswith("tool."):
                tool_groups.setdefault(tool_call_id, []).append(event)
            if event.event_type.startswith("approval."):
                approval_groups.setdefault(tool_call_id, []).append(event)

    return SegmentTrace(
        execution_segment_id=segment_id,
        started_seq=started.seq if started is not None else None,
        finished_seq=finished.seq if finished is not None else None,
        request_kind=_string_fact(started, "request_kind"),
        transport_mode=_string_fact(started, "transport_mode"),
        execution_path=_string_fact(started, "execution_path"),
        outcome=_string_fact(finished, "outcome"),
        contexts=tuple(
            sorted(contexts, key=lambda item: (item.model_step or 0, item.snapshot_key))
        ),
        model_steps=tuple(
            _model_step_view(model_call_id, rows)
            for model_call_id, rows in sorted(
                model_groups.items(),
                key=lambda item: min(row.seq for row in item[1]),
            )
        ),
        tools=tuple(
            _tool_view(tool_call_id, rows)
            for tool_call_id, rows in sorted(
                tool_groups.items(),
                key=lambda item: min(row.seq for row in item[1]),
            )
        ),
        approvals=tuple(
            _approval_view(tool_call_id, rows)
            for tool_call_id, rows in sorted(
                approval_groups.items(),
                key=lambda item: min(row.seq for row in item[1]),
            )
        ),
        events=tuple(events),
    )


def _model_step_view(model_call_id: str, rows: list[EventTrace]) -> ModelStepTrace:
    requested = next((row for row in rows if row.event_type == "model.requested"), None)
    completed = next((row for row in rows if row.event_type == "model.completed"), None)
    failed = next((row for row in rows if row.event_type == "model.failed"), None)
    identity = requested or completed or failed
    return ModelStepTrace(
        model_step=identity.model_step if identity is not None else None,
        model_call_id=model_call_id,
        snapshot_id=_string_fact(requested, "snapshot_id"),
        requested_seq=requested.seq if requested is not None else None,
        completed_seq=completed.seq if completed is not None else None,
        failed_seq=failed.seq if failed is not None else None,
    )


def _tool_view(tool_call_id: str, rows: list[EventTrace]) -> ToolTrace:
    proposed = next((row for row in rows if row.event_type == "tool.proposed"), None)
    started = next((row for row in rows if row.event_type == "tool.started"), None)
    completed = next((row for row in rows if row.event_type == "tool.completed"), None)
    failed = next((row for row in rows if row.event_type == "tool.failed"), None)
    identity = proposed or started or completed or failed
    return ToolTrace(
        tool_call_id=tool_call_id,
        tool_name=_string_fact(identity, "tool_name"),
        tool_kind=_string_fact(proposed, "tool_kind"),
        proposed_seq=proposed.seq if proposed is not None else None,
        started_seq=started.seq if started is not None else None,
        completed_seq=completed.seq if completed is not None else None,
        failed_seq=failed.seq if failed is not None else None,
    )


def _approval_view(tool_call_id: str, rows: list[EventTrace]) -> ApprovalTrace:
    requested = next((row for row in rows if row.event_type == "approval.requested"), None)
    decided = next((row for row in rows if row.event_type == "approval.decided"), None)
    return ApprovalTrace(
        tool_call_id=tool_call_id,
        confirmation_attempt_id=_string_fact(decided, "confirmation_attempt_id"),
        requested_seq=requested.seq if requested is not None else None,
        decided_seq=decided.seq if decided is not None else None,
        decision=_string_fact(decided, "decision"),
    )


def _event_segment_id(event: EventTrace) -> str:
    return event.execution_segment_id


def _string_fact(event: EventTrace | None, key: str) -> str | None:
    if event is None:
        return None
    value = event.facts.get(key)
    return value if type(value) is str else None


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "AgentRunTrace",
    "AgentRunTraceNotFound",
    "ApprovalTrace",
    "ContextSnapshotTrace",
    "EventTrace",
    "ModelStepTrace",
    "SegmentTrace",
    "ToolTrace",
    "reconstruct_agent_run",
]
