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
