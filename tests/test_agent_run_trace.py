from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.agent_runtime.events import EventDraft, prepare_event
from offerpilot.agent_runtime.trace import reconstruct_agent_run
from offerpilot.db import init_database, journal_session_factory_for_data_dir
from offerpilot.models import AgentEvent, AgentRun, Conversation
from offerpilot.repositories.agent_runs import (
    AgentRunRepository,
    DispositionCommand,
    StartRunCommand,
    StartSegmentCommand,
)

KEY_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
SEGMENT_ID = "33333333-3333-4333-8333-333333333333"
MODEL_CALL_ID = "44444444-4444-4444-8444-444444444444"
CONFIRMATION_SEGMENT_ID = "55555555-5555-4555-8555-555555555555"
CONFIRMATION_ATTEMPT_ID = "66666666-6666-4666-8666-666666666666"


def _setup(tmp_path: Path) -> tuple[AgentRunRepository, sessionmaker[Session]]:
    primary_factory = init_database(tmp_path / "data.db")
    with primary_factory() as session:
        conversation = Conversation(title="trace")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    journal_factory = journal_session_factory_for_data_dir(tmp_path)
    repository = AgentRunRepository(journal_factory)
    repository.create_run_and_initial_segment(
        StartRunCommand(
            run_id=RUN_ID,
            conversation_id=conversation_id,
            input_message_id=None,
            origin_kind="user_message",
            initial_context_type="workspace",
            initial_context_entity_id=None,
            initial_context_ref_fingerprint=None,
            fingerprint_key_id=KEY_ID,
            initial_transport_mode="sync",
            initial_route_kind="model",
            run_started=prepare_event(
                event_type="run.started",
                execution_segment_id=SEGMENT_ID,
                facts={
                    "agent_run_id": RUN_ID,
                    "origin_kind": "user_message",
                    "conversation_id": conversation_id,
                    "context_type": "workspace",
                    "transport_mode": "sync",
                },
            ),
            segment_started=prepare_event(
                event_type="segment.started",
                execution_segment_id=SEGMENT_ID,
                facts={
                    "request_kind": "initial",
                    "transport_mode": "sync",
                    "execution_path": "model_turn",
                    "transport_run_id": None,
                },
            ),
        )
    )
    return repository, journal_factory


def _terminal_events(status: str) -> tuple[EventDraft, EventDraft]:
    return (
        prepare_event(
            event_type=f"run.{status}",
            execution_segment_id=SEGMENT_ID,
            facts={"agent_run_id": RUN_ID, "status": status, "failure_code": None},
        ),
        prepare_event(
            event_type="segment.finished",
            execution_segment_id=SEGMENT_ID,
            facts={"outcome": status, "terminal_run_status": status},
        ),
    )


def _complete(repository: AgentRunRepository) -> None:
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(target_status="completed", events=_terminal_events("completed")),
    )


def _waiting_events(
    tool_call_id: str,
    segment_id: str = SEGMENT_ID,
) -> tuple[EventDraft, ...]:
    return (
        prepare_event(
            event_type="tool.proposed",
            execution_segment_id=segment_id,
            facts={
                "tool_call_id": tool_call_id,
                "tool_name": "create_application",
                "tool_kind": "write",
                "args_shape_digest": "sha256:" + "a" * 64,
                "proposal_outcome": "confirmation_required",
            },
            source_ref_type="tool_call",
            source_ref_id=tool_call_id,
        ),
        prepare_event(
            event_type="approval.requested",
            execution_segment_id=segment_id,
            facts={
                "tool_call_id": tool_call_id,
                "confirmation_mode": "required",
                "pending_identity_fingerprint": "b" * 64,
            },
            source_ref_type="tool_call",
            source_ref_id=tool_call_id,
            fingerprint_key_id=KEY_ID,
        ),
        prepare_event(
            event_type="run.waiting_confirmation",
            execution_segment_id=segment_id,
            facts={"tool_call_id": tool_call_id},
            source_ref_type="tool_call",
            source_ref_id=tool_call_id,
        ),
        prepare_event(
            event_type="segment.finished",
            execution_segment_id=segment_id,
            facts={"outcome": "suspended", "terminal_run_status": None},
        ),
    )


def _resume_confirmation(repository: AgentRunRepository, tool_call_id: str) -> None:
    repository.start_segment(
        StartSegmentCommand(
            run_id=RUN_ID,
            segment_started=prepare_event(
                event_type="segment.started",
                execution_segment_id=CONFIRMATION_SEGMENT_ID,
                facts={
                    "request_kind": "confirmation",
                    "transport_mode": "sync",
                    "execution_path": "agent_resume",
                    "transport_run_id": None,
                },
            ),
        )
    )
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="running",
            events=(
                prepare_event(
                    event_type="run.resumed",
                    execution_segment_id=CONFIRMATION_SEGMENT_ID,
                    facts={
                        "confirmation_attempt_id": CONFIRMATION_ATTEMPT_ID,
                        "tool_call_id": tool_call_id,
                    },
                ),
            ),
        ),
    )


def test_trace_reconstructs_normal_terminal_run(tmp_path: Path) -> None:
    repository, _ = _setup(tmp_path)
    _complete(repository)

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=timedelta(minutes=5),
    )

    assert trace.lifecycle_status == "completed"
    assert trace.completion_status == "terminal"
    assert trace.integrity_status == "healthy"
    assert trace.anomalies == ()
    assert len(trace.segments) == 1
    assert trace.segments[0].finished_seq == 4


def test_trace_reconstructs_waiting_confirmation_as_healthy_suspension(
    tmp_path: Path,
) -> None:
    repository, _ = _setup(tmp_path)
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="waiting_confirmation",
            events=_waiting_events("call-1"),
            waiting_tool_call_id="call-1",
        ),
    )

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.lifecycle_status == "waiting_confirmation"
    assert trace.completion_status == "suspended"
    assert trace.integrity_status == "healthy"
    assert trace.anomalies == ()
    assert trace.segments[0].tools[0].tool_call_id == "call-1"
    assert trace.segments[0].approvals[0].requested_seq == 4


def test_trace_treats_historical_waiting_event_as_resumed_lifecycle(
    tmp_path: Path,
) -> None:
    repository, _ = _setup(tmp_path)
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="waiting_confirmation",
            events=_waiting_events("call-first"),
            waiting_tool_call_id="call-first",
        ),
    )
    _resume_confirmation(repository, "call-first")

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.lifecycle_status == "running"
    assert trace.completion_status == "open"
    assert trace.integrity_status == "healthy"
    assert trace.anomalies == ()


def test_trace_projects_only_latest_waiting_confirmation_identity(
    tmp_path: Path,
) -> None:
    repository, _ = _setup(tmp_path)
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="waiting_confirmation",
            events=_waiting_events("call-first"),
            waiting_tool_call_id="call-first",
        ),
    )
    _resume_confirmation(repository, "call-first")
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="waiting_confirmation",
            events=_waiting_events("call-second", CONFIRMATION_SEGMENT_ID),
            waiting_tool_call_id="call-second",
        ),
    )

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.lifecycle_status == "waiting_confirmation"
    assert trace.completion_status == "suspended"
    assert trace.integrity_status == "healthy"
    assert trace.anomalies == ()


def test_stale_uses_latest_activity_instead_of_started_at(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    with factory() as session, session.begin():
        run = session.get(AgentRun, RUN_ID)
        assert run is not None
        run.started_at = now - timedelta(days=7)
        run.updated_at = now

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=now + timedelta(seconds=1),
        stale_after=timedelta(minutes=5),
    )

    assert trace.completion_status == "open"
    assert trace.integrity_status == "healthy"


def test_trace_marks_old_running_run_as_stale_open(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    with factory() as session, session.begin():
        run = session.get(AgentRun, RUN_ID)
        assert run is not None
        run.updated_at = old
        for event in session.scalars(select(AgentEvent).where(AgentEvent.run_id == RUN_ID)):
            event.created_at = old

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=now,
        stale_after=timedelta(minutes=30),
    )

    assert trace.completion_status == "stale_open"


def test_sequence_gap_has_highest_integrity_priority(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    route = repository.append_event(
        RUN_ID,
        prepare_event(
            event_type="route.selected",
            execution_segment_id=SEGMENT_ID,
            facts={"route_kind": "model", "route_reason_code": "model_default"},
        ),
    )
    with factory() as session, session.begin():
        persisted = session.get(AgentEvent, route.id)
        run = session.get(AgentRun, RUN_ID)
        assert persisted is not None and run is not None
        persisted.seq = 4
        run.last_seq = 4
        run.recording_status = "degraded"

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "sequence_gap"
    assert "sequence_gap:3" in trace.anomalies
    assert "recording_degraded" in trace.anomalies


def test_trace_detects_missing_model_completion(tmp_path: Path) -> None:
    repository, _ = _setup(tmp_path)
    repository.append_event(
        RUN_ID,
        prepare_event(
            event_type="model.requested",
            execution_segment_id=SEGMENT_ID,
            model_step=1,
            model_call_id=MODEL_CALL_ID,
            facts={
                "snapshot_id": "55555555-5555-4555-8555-555555555555",
                "provider_kind": "openai",
                "model_id_fingerprint": "a" * 64,
                "supports_tools": True,
                "supports_json_schema": False,
                "stream": False,
                "tools_count": 1,
                "response_format_kind": "text",
            },
            fingerprint_key_id=KEY_ID,
        ),
    )
    _complete(repository)

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "semantic_anomaly"
    assert f"model_call_incomplete:{MODEL_CALL_ID}" in trace.anomalies


def test_trace_detects_missing_tool_completion(tmp_path: Path) -> None:
    repository, _ = _setup(tmp_path)
    tool_call_id = "call-incomplete"
    repository.append_event(
        RUN_ID,
        prepare_event(
            event_type="tool.proposed",
            execution_segment_id=SEGMENT_ID,
            facts={
                "tool_call_id": tool_call_id,
                "tool_name": "get_application",
                "tool_kind": "read",
                "args_shape_digest": "sha256:" + "c" * 64,
                "proposal_outcome": "execution_allowed",
            },
            source_ref_type="tool_call",
            source_ref_id=tool_call_id,
        ),
    )
    repository.append_event(
        RUN_ID,
        prepare_event(
            event_type="tool.started",
            execution_segment_id=SEGMENT_ID,
            facts={
                "tool_call_id": tool_call_id,
                "tool_name": "get_application",
                "result_contract": "legacy_string_v1",
            },
            source_ref_type="tool_call",
            source_ref_id=tool_call_id,
        ),
    )
    _complete(repository)

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "semantic_anomaly"
    assert f"tool_call_incomplete:{tool_call_id}" in trace.anomalies


def test_trace_detects_missing_segment_finish_and_terminal_event(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    with factory() as session, session.begin():
        run = session.get(AgentRun, RUN_ID)
        assert run is not None
        run.status = "completed"
        run.finished_at = now
        run.updated_at = now

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=now,
        stale_after=None,
    )

    assert trace.integrity_status == "semantic_anomaly"
    assert f"segment_missing_finish:{SEGMENT_ID}" in trace.anomalies
    assert "terminal_event_missing:completed" in trace.anomalies


def test_trace_reports_known_degraded_separately(tmp_path: Path) -> None:
    repository, _ = _setup(tmp_path)
    repository.mark_degraded(RUN_ID)
    _complete(repository)

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "known_degraded"
    assert trace.anomalies == ("recording_degraded",)


def test_trace_detects_waiting_projection_identity_mismatch(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    repository.converge_disposition(
        RUN_ID,
        DispositionCommand(
            target_status="waiting_confirmation",
            events=_waiting_events("call-original"),
            waiting_tool_call_id="call-original",
        ),
    )
    with factory() as session, session.begin():
        run = session.get(AgentRun, RUN_ID)
        assert run is not None
        run.waiting_tool_call_id = "call-different"

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "semantic_anomaly"
    assert "waiting_projection_mismatch" in trace.anomalies


def test_trace_detects_completion_without_request(tmp_path: Path) -> None:
    repository, _ = _setup(tmp_path)
    repository.append_event(
        RUN_ID,
        prepare_event(
            event_type="model.completed",
            execution_segment_id=SEGMENT_ID,
            model_step=1,
            model_call_id=MODEL_CALL_ID,
            facts={
                "assistant_kind": "text",
                "tool_call_count": 0,
                "finish_category": "stop",
            },
        ),
    )
    _complete(repository)

    trace = reconstruct_agent_run(
        repository,
        RUN_ID,
        as_of=datetime.now(timezone.utc),
        stale_after=None,
    )

    assert trace.integrity_status == "semantic_anomaly"
    assert f"model_completion_without_request:{MODEL_CALL_ID}" in trace.anomalies
