from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from offerpilot.context_sources.binding_scope import current_frozen_readiness, frozen_readiness_scope
from offerpilot.context_sources.contracts import ContextPolicies, ContributorPolicy
from offerpilot.context_sources.loader import load_optional_sources
from offerpilot.context_sources.models import ContextContributorSettings
from offerpilot.context_sources.readiness import (
    ReadinessContextBinding,
    ReadinessContextConflict,
    ReadinessContextRequest,
    ReadinessContextRepository,
    ReadinessContextUnavailable,
    load_readiness_source,
)
from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.db import init_database
from offerpilot.models import (
    ApplicationEvent,
    Conversation,
    InterviewNote,
    InterviewReadinessSignal,
    InterviewReadinessSignalEvidence,
    InterviewReadinessSignalVersion,
    Resume,
)
from offerpilot.repositories.chat import ChatRepository, ConversationScopeMutationSnapshot
from tests.review_readiness_support import seed_review_candidate
from tests.test_review_readiness_projection import _commit_signal, _reject_signal_operation


@pytest.fixture
def readiness_case(tmp_path):  # type: ignore[no-untyped-def]
    database = tmp_path / "readiness-context.sqlite3"
    sessions = init_database(database)
    seeded = seed_review_candidate(
        sessions,
        practice_focuses=[
            {
                "id": "focus-1",
                "text": "先澄清约束条件，再说明缓存一致性的取舍。",
                "evidence_refs": [
                    {
                        "source": "interview_note",
                        "path": "/difficulty_points",
                        "excerpt": "cache consistency tradeoffs",
                    }
                ],
            },
            {
                "id": "focus-2",
                "text": "补充故障恢复路径和监控指标。",
                "evidence_refs": [
                    {
                        "source": "interview_note",
                        "path": "/self_reflection",
                        "excerpt": "clarify constraints",
                    }
                ],
            },
        ],
    )
    with sessions() as session:
        target = ApplicationEvent(
            application_id=int(seeded["application_id"]),
            event_type="interview",
            subtype="onsite",
            round=3,
            scheduled_at=datetime(2026, 9, 20, 10, tzinfo=timezone.utc),
            duration_minutes=60,
            status="todo",
        )
        resume = Resume(
            title="Backend resume",
            name="Backend resume",
            parse_status="text-ready",
            content_json=json.dumps({"experience": []}),
        )
        conversation = Conversation(
            title="准备重点对话",
            context_type="application",
            context_ref=str(seeded["application_id"]),
        )
        session.add_all((target, resume, conversation))
        session.commit()
        target_id = target.id
        resume_id = resume.id
        conversation_id = conversation.id

    signal_id, version_id = _commit_signal(
        sessions,
        seeded,
        focus_id=str(seeded["focus_id"]),
        idempotency_key=str(uuid4()),
    )
    retraction_operation_id = _reject_signal_operation(
        sessions,
        seeded,
        focus_id="focus-2",
    )
    with sessions() as session:
        # Enable only the contributor under test.  The binding itself is still
        # explicit; policy enablement must never select a target.
        session.add(
            ContextContributorSettings(
                id=1,
                revision=1,
                settings_json=ContextPolicies(
                    confirmed_readiness=ContributorPolicy(enabled=True),
                    confirmed_memory=ContributorPolicy(enabled=False),
                ).model_dump_json(),
            )
        )
        session.commit()

    loader = ContextSourceLoader(database)
    yield {
        "database": database,
        "sessions": sessions,
        "loader": loader,
        "seeded": seeded,
        "conversation_id": conversation_id,
        "target_id": target_id,
        "resume_id": resume_id,
        "version_id": version_id,
        "signal_id": signal_id,
        "retraction_operation_id": retraction_operation_id,
    }
    loader.close()


def _append_retraction(case) -> None:  # type: ignore[no-untyped-def]
    with case["sessions"]() as session:
        parent = session.get(InterviewReadinessSignalVersion, case["version_id"])
        signal = session.get(InterviewReadinessSignal, case["signal_id"])
        assert parent is not None and signal is not None
        evidence = tuple(
            session.scalars(
                select(InterviewReadinessSignalEvidence).where(
                    InterviewReadinessSignalEvidence.signal_version_id == parent.id
                )
            )
        )
        child = InterviewReadinessSignalVersion(
            signal_id=signal.id,
            version_number=parent.version_number + 1,
            parent_version_id=parent.id,
            disposition="retracted",
            schema_version=parent.schema_version,
            statement_text=parent.statement_text,
            user_note=parent.user_note,
            source_note_revision=parent.source_note_revision,
            source_note_fingerprint=parent.source_note_fingerprint,
            source_proposal_hash=parent.source_proposal_hash,
            candidate_fingerprint=parent.candidate_fingerprint,
            domain_idempotency_key=str(uuid4()),
            write_operation_id=case["retraction_operation_id"],
        )
        session.add(child)
        session.flush()
        session.add_all(
            InterviewReadinessSignalEvidence(
                signal_version_id=child.id,
                ordinal=item.ordinal,
                source_path=item.source_path,
                excerpt=item.excerpt,
                excerpt_sha256=item.excerpt_sha256,
                source_field_sha256=item.source_field_sha256,
            )
            for item in evidence
        )
        signal.current_version_id = child.id
        signal.revision += 1
        session.commit()


def _confirm(case, *, expected_revision: int = 0, ordered_version_ids: list[int] | None = None):  # type: ignore[no-untyped-def]
    command = ReadinessContextRequest(
        mutation_id=uuid4(),
        expected_revision=expected_revision,
        confirmed=True,
        target_event_id=case["target_id"],
        resume_id=case["resume_id"],
        ordered_version_ids=ordered_version_ids or [],
    )
    return command, ReadinessContextRepository(case["sessions"]).confirm(
        case["conversation_id"], command
    )


def test_confirm_replay_and_cas_preserve_one_explicit_binding(readiness_case) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    command, first = _confirm(case, ordered_version_ids=[case["version_id"]])

    assert first["state"] == "confirmed"
    assert first["revision"] == 1
    assert first["ordered_version_ids"] == [case["version_id"]]
    assert first["selection_fingerprint"].startswith("sha256:")
    assert repository.confirm(case["conversation_id"], command) == first

    conflicting = ReadinessContextRequest(
        mutation_id=uuid4(),
        expected_revision=0,
        confirmed=True,
        target_event_id=case["target_id"],
        resume_id=case["resume_id"],
        ordered_version_ids=[],
    )
    with pytest.raises(ReadinessContextConflict, match="readiness_context_changed"):
        repository.confirm(case["conversation_id"], conflicting)

    binding = repository.load_binding(case["conversation_id"])
    assert isinstance(binding, ReadinessContextBinding)
    assert binding.ordered_version_ids == (case["version_id"],)
    assert binding.scope_revision == 0
    assert binding.revision == 1


def test_clear_is_explicit_idempotent_withdrawal_and_stops_consumption(readiness_case) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    _confirm(case, ordered_version_ids=[case["version_id"]])

    from offerpilot.context_sources.readiness import ReadinessContextClearRequest

    command = ReadinessContextClearRequest(
        mutation_id=uuid4(), expected_revision=1, confirmed=True
    )
    withdrawn = repository.clear(case["conversation_id"], command)
    assert withdrawn == {
        "schema_version": 1,
        "state": "withdrawn",
        "conversation_id": case["conversation_id"],
        "application_id": case["seeded"]["application_id"],
        "scope_revision": 0,
        "revision": 2,
    }
    assert repository.clear(case["conversation_id"], command) == withdrawn
    assert repository.load_binding(case["conversation_id"]) is None

    with case["sessions"]() as session:
        conversation = session.get(Conversation, case["conversation_id"])
        assert conversation is not None
        assert conversation.context_type == "application"
        assert conversation.scope_revision == 0
    result = load_optional_sources(case["loader"], case["conversation_id"], "缓存一致性")
    assert result.contributors[0].status == "not_applicable"
    assert result.sources == ()


def test_explicit_empty_frozen_selection_does_not_auto_apply_a_signal(readiness_case) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    _command, confirmed = _confirm(case)
    assert confirmed["ordered_version_ids"] == []
    binding = repository.load_binding(case["conversation_id"])
    assert isinstance(binding, ReadinessContextBinding)
    assert binding.ordered_version_ids == ()

    with frozen_readiness_scope(None):
        frozen = current_frozen_readiness()
        assert frozen is not None and frozen.binding is None
        result = load_optional_sources(case["loader"], case["conversation_id"], "缓存一致性")
    assert result.contributors[0].status == "not_applicable"
    assert result.sources == ()


def test_confirmed_selection_loads_only_the_frozen_version(readiness_case) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    _confirm(case, ordered_version_ids=[case["version_id"]])
    binding = repository.load_binding(case["conversation_id"])
    assert binding is not None

    loaded = case["loader"].load(
        lambda connection: load_readiness_source(connection, binding),
        lambda value: value,
    )
    assert len(loaded) == 1
    assert loaded[0]["kind"] == "confirmed_readiness"
    assert loaded[0]["signal_version_id"] == case["version_id"]
    assert loaded[0]["target_event_id"] == case["target_id"]
    assert loaded[0]["resume_id"] == case["resume_id"]
    assert loaded[0]["target_event"] == {
        "id": case["target_id"], "event_type": "interview", "round": 3,
        "subtype": "onsite", "scheduled_at": "2026-09-20T10:00:00+00:00", "duration_minutes": 60,
    }
    assert loaded[0]["source_event"]["round"] == 2


@pytest.mark.parametrize("mutation", ["note", "retracted"])
def test_consumption_fails_closed_when_the_confirmed_source_changes(readiness_case, mutation: str) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    _confirm(case, ordered_version_ids=[case["version_id"]])
    binding = repository.load_binding(case["conversation_id"])
    assert binding is not None
    with case["sessions"]() as session:
        if mutation == "note":
            note = session.get(InterviewNote, case["seeded"]["note_id"])
            assert note is not None
            note.content_revision += 1
        session.commit()
    if mutation == "retracted":
        _append_retraction(case)

    with pytest.raises(ProjectionError, match="readiness_source_unavailable"):
        case["loader"].load(
            lambda connection: load_readiness_source(connection, binding),
            lambda value: value,
        )


def test_scope_revision_drift_invalidates_a_frozen_binding(readiness_case) -> None:
    case = readiness_case
    repository = ReadinessContextRepository(case["sessions"])
    _confirm(case, ordered_version_ids=[case["version_id"]])
    binding = repository.load_binding(case["conversation_id"])
    assert binding is not None

    repository_scope = ChatRepository(case["sessions"])
    changed = repository_scope.patch_conversation_with_scope(
        case["conversation_id"],
        {},
        ConversationScopeMutationSnapshot(
            context_type="application",
            context_ref=str(case["seeded"]["application_id"]),
            mode="interview_coach",
        ),
        expected_scope_revision=0,
    )
    assert changed is not None and changed.scope_revision == 1

    with pytest.raises(ReadinessContextUnavailable, match="readiness_context_scope_changed"):
        repository.load_binding(case["conversation_id"])
    with pytest.raises(ProjectionError, match="readiness_context_scope_changed"):
        case["loader"].load(
            lambda connection: load_readiness_source(connection, binding),
            lambda value: value,
        )


def test_non_application_conversations_have_no_readiness_owner(readiness_case) -> None:
    case = readiness_case
    with case["sessions"]() as session:
        conversation = Conversation(title="工作台")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    repository = ReadinessContextRepository(case["sessions"])
    assert repository.load_binding(conversation_id) is None
    with pytest.raises(ReadinessContextUnavailable, match="readiness_requires_application_conversation"):
        repository.read(conversation_id)
