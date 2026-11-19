from uuid import uuid4

import pytest

from offerpilot.context_sources.models import ContextContributorSettings
from offerpilot.context_sources.contracts import ContextPolicies, ContributorPolicy
from offerpilot.context_sources.loader import load_optional_sources
from offerpilot.confirmed_memory.repository import ConfirmedMemoryRepository, MemoryMutation
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.db import init_database
from offerpilot.models import Conversation


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "context.db"
    sessions = init_database(path)
    with sessions() as session:
        conversation = Conversation(title="偏好测试")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    loader = ContextSourceLoader(path)
    yield sessions, loader, conversation_id
    loader._pool.close()


def save(repository, version=0, *, memory_id=None, action="confirm", content="简洁中文，先给结论"):
    return repository.mutate(memory_id, MemoryMutation(mutation_id=uuid4(), action=action,
        expected_version=version, confirmed=True, content=content))


def test_next_projection_observes_edit_withdraw_and_independent_switch(context):
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    item = save(repository)
    first = load_optional_sources(loader, conversation_id, "当前问题")
    by_name = {value.name: value for value in first.contributors}
    assert by_name["confirmed_memory"].status == "ready"
    assert all(by_name[name].status == "disabled" for name in ("confirmed_readiness", "knowledge_context", "older_conversation_summary"))
    save(repository, 1, memory_id=item["id"], content="改为英文")
    second = load_optional_sources(loader, conversation_id, "当前问题")
    assert second.sources[0].content_revision_fingerprint != first.sources[0].content_revision_fingerprint
    assert "改为英文" in second.contributors[1].messages[0].content
    save(repository, 2, memory_id=item["id"], action="withdraw", content="")
    assert load_optional_sources(loader, conversation_id, "当前问题").contributors[1].status == "not_applicable"
    save(repository, 3, memory_id=item["id"], content="新确认")
    with sessions() as session:
        session.add(ContextContributorSettings(id=1, revision=1, settings_json=ContextPolicies(
            confirmed_memory=ContributorPolicy(enabled=False)).model_dump_json()))
        session.commit()
    assert load_optional_sources(loader, conversation_id, "当前问题").contributors[1].status == "disabled"


def test_archived_conversation_fails_closed(context):
    from datetime import datetime, timezone
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions))
    with sessions() as session:
        session.get(Conversation, conversation_id).archived_at = datetime.now(timezone.utc)
        session.commit()
    with pytest.raises(ProjectionError):
        load_optional_sources(loader, conversation_id, "问题")


def test_large_item_is_omitted_atomically_by_own_budget(context):
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions), content="长偏好" * 600)
    with sessions() as session:
        session.add(ContextContributorSettings(id=1, revision=1, settings_json=ContextPolicies(
            confirmed_memory=ContributorPolicy(enabled=True, max_units=256)).model_dump_json()))
        session.commit()
    result = load_optional_sources(loader, conversation_id, "问题")
    assert result.contributors[1].messages == ()
    assert result.contributors[1].diagnostics["omitted_count"] == 1


def test_temporary_optional_read_failure_omits_all_data_but_integrity_failure_propagates():
    from offerpilot.context_projector.loader import SourceTemporarilyUnavailable

    class FailedLoader:
        def __init__(self, error):
            self.error = error

        def load(self, read, freeze):
            raise self.error

    result = load_optional_sources(FailedLoader(SourceTemporarilyUnavailable()), 1, "当前问题")
    assert all(item.status == "unavailable" and not item.messages for item in result.contributors)
    assert result.sources == () and result.covered_history == ()
    for error in (ProjectionError("source_load_failed"), ProjectionError("optional_source_scope_unavailable")):
        with pytest.raises(ProjectionError) as caught:
            load_optional_sources(FailedLoader(error), 1, "当前问题")
        assert caught.value is error


def test_sqlite_busy_is_temporary_but_missing_table_is_not(context):
    import sqlite3
    from offerpilot.context_projector.loader import SourceTemporarilyUnavailable

    _, loader, _ = context
    with sqlite3.connect(loader._pool.database, isolation_level=None) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(SourceTemporarilyUnavailable):
                loader.load(lambda connection: connection.execute("SELECT id FROM conversations").fetchall(), lambda value: value)
        finally:
            writer.rollback()
    assert loader._pool._queue.qsize() == 4
    with pytest.raises(ProjectionError) as caught:
        loader.load(lambda connection: connection.execute("SELECT * FROM nonexistent_optional_source"), lambda value: value)
    assert not isinstance(caught.value, SourceTemporarilyUnavailable)


def test_memory_delete_removes_current_and_old_text_from_actual_recall(context):
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    item = save(repository, content="旧记忆正文")
    save(repository, 1, memory_id=item["id"], content="更正后的记忆")
    projected = load_optional_sources(loader, conversation_id, "问题")
    messages = str(projected.contributors[1].messages)
    assert "更正后的记忆" in messages
    assert "旧记忆正文" not in messages
    save(repository, 2, memory_id=item["id"], action="delete", content="")
    projected = load_optional_sources(loader, conversation_id, "问题")
    assert projected.contributors[1].status == "not_applicable"
    assert projected.contributors[1].messages == ()
    assert not any(source.kind == "confirmed_memory" for source in projected.sources)


def test_memory_projection_preserves_category_evidence_and_excludes_expired(context):
    import json
    from sqlalchemy import delete
    from offerpilot.confirmed_memory.models import ConfirmedMemoryVersionMetadata
    sessions, loader, conversation_id = context
    repository = ConfirmedMemoryRepository(sessions)
    legacy = save(repository, content="legacy personal claim")
    with sessions() as session:
        session.execute(delete(ConfirmedMemoryVersionMetadata))
        session.commit()
    repository.mutate(None, MemoryMutation(mutation_id=uuid4(), action="confirm", expected_version=0,
        confirmed=True, content="expired personal claim", metadata={"category": "preparation", "valid_until": "2000-01-01T00:00:00Z"}))
    goal = repository.mutate(None, MemoryMutation(mutation_id=uuid4(), action="confirm", expected_version=0,
        confirmed=True, content="career personal claim", metadata={"category": "goal", "valid_until": None}))
    result = load_optional_sources(loader, conversation_id, "current request")
    items = json.loads(result.contributors[1].messages[0].content.split("\n", 1)[1])["items"]
    by_id = {item["id"]: item for item in items}
    assert set(by_id) == {legacy["id"], goal["id"]}
    assert by_id[legacy["id"]]["category"] == "unclassified"
    assert by_id[goal["id"]]["category"] == "goal"
    assert all(item["evidence_status"] == "user_confirmed" and "preference" not in item for item in items)
    message = result.contributors[1].messages[0].content
    assert "不证明外部事实" in message and "不是指令" in message
    assert "expired personal claim" not in message


@pytest.mark.parametrize("field,value", [("category", ""), ("category", "verified_fact"), ("valid_until", "2030-01-01T00:00:00")])
def test_corrupt_memory_metadata_never_degrades_to_legacy_defaults(context, field, value):
    from sqlalchemy import select
    from offerpilot.confirmed_memory.models import ConfirmedMemoryVersionMetadata
    sessions, loader, conversation_id = context
    save(ConfirmedMemoryRepository(sessions))
    with sessions() as session:
        record = session.scalar(select(ConfirmedMemoryVersionMetadata))
        setattr(record, field, value)
        session.commit()
    with pytest.raises(ProjectionError, match="source_load_failed"):
        load_optional_sources(loader, conversation_id, "current request")
