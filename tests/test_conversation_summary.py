from sqlalchemy import select
import pytest

from offerpilot.context_sources.summary import ConversationSummary, ConversationSummaryRepository, SummaryRequest, SummaryUnavailable, load_summary
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.db import init_database
from offerpilot.models import ChatMessage, Conversation


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "summary.db"
    sessions = init_database(path)
    with sessions() as session:
        conversation = Conversation(title="摘要")
        session.add(conversation)
        session.flush()
        for index in range(8):
            session.add(ChatMessage(conversation_id=conversation.id, role="user" if index % 2 == 0 else "assistant", content=f"较早记录 {index}"))
        session.commit()
        conversation_id = conversation.id
    loader = ContextSourceLoader(path)
    yield sessions, loader, conversation_id
    loader._pool.close()


def read(loader, conversation_id):
    return loader.load(lambda connection: load_summary(connection, conversation_id), lambda value: value)


def test_explicit_cached_summary_keeps_originals_and_categories(history):
    sessions, loader, conversation_id = history
    repository = ConversationSummaryRepository(sessions)
    with pytest.raises(SummaryUnavailable):
        repository.generate(conversation_id, SummaryRequest(confirmed=False))
    first = repository.generate(conversation_id, SummaryRequest(confirmed=True))
    assert not first["cached"] and first["model_calls"] == 0
    second = repository.generate(conversation_id, SummaryRequest(confirmed=True))
    assert second["cached"] and second["revision"] == first["revision"]
    items, covered = read(loader, conversation_id)
    assert len(covered) == 4
    assert {item["kind"] for item in items[0]["summary"]["items"]} == {"user_statement", "model_inference"}
    assert items[0]["summary"]["supported_facts"] == []
    with sessions() as session:
        assert len(session.scalars(select(ChatMessage)).all()) == 8
        assert session.get(ConversationSummary, conversation_id).generations_today == 1


@pytest.mark.parametrize("change", ["edit", "delete", "withdraw", "tamper"])
def test_source_change_and_withdrawal_invalidate_before_injection(history, change):
    sessions, loader, conversation_id = history
    repository = ConversationSummaryRepository(sessions)
    repository.generate(conversation_id, SummaryRequest(confirmed=True))
    if change == "withdraw":
        repository.withdraw(conversation_id)
    else:
        with sessions() as session:
            message = session.scalar(select(ChatMessage).order_by(ChatMessage.id))
            if change == "edit":
                message.content = "编辑后的来源"
            elif change == "delete":
                session.delete(message)
            else:
                session.get(ConversationSummary, conversation_id).summary_json = '{"items":[]}'
            session.commit()
    assert read(loader, conversation_id) == ([], ())


def test_generation_budget_is_independent_from_cache(history):
    sessions, _, conversation_id = history
    repository = ConversationSummaryRepository(sessions)
    for index in range(4):
        repository.generate(conversation_id, SummaryRequest(confirmed=True))
        with sessions() as session:
            session.scalar(select(ChatMessage).order_by(ChatMessage.id)).content = f"明确更新 {index}"
            session.commit()
    with pytest.raises(SummaryUnavailable, match="daily_budget"):
        repository.generate(conversation_id, SummaryRequest(confirmed=True))


def test_conversation_deletion_cascades_summary(history):
    sessions, _, conversation_id = history
    ConversationSummaryRepository(sessions).generate(conversation_id, SummaryRequest(confirmed=True))
    with sessions() as session:
        session.delete(session.get(Conversation, conversation_id))
        session.commit()
        assert session.get(ConversationSummary, conversation_id) is None


def test_large_source_is_rejected_before_content_materialization(history):
    sessions, loader, conversation_id = history
    repository = ConversationSummaryRepository(sessions)
    repository.generate(conversation_id, SummaryRequest(confirmed=True))
    with sessions() as session:
        session.scalar(select(ChatMessage).order_by(ChatMessage.id)).content = "大" * 100000
        session.commit()
    assert read(loader, conversation_id) == ([], ())
    with pytest.raises(SummaryUnavailable, match="no_plain_older_history"):
        repository.generate(conversation_id, SummaryRequest(confirmed=True))
