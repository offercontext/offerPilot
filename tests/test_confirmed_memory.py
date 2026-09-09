from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from offerpilot.models import Base
from offerpilot.confirmed_memory.models import ConfirmedMemoryVersion
from offerpilot.confirmed_memory.repository import (
    ConfirmedMemoryRepository, MemoryConflict, MemoryGone, MemoryMutation,
)


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memory.db'}")
    Base.metadata.create_all(engine)
    return ConfirmedMemoryRepository(sessionmaker(engine))


def command(version=0, action="confirm", content="回答时先给结论", **changes):
    return MemoryMutation(mutation_id=uuid4(), action=action, expected_version=version,
                          confirmed=True, content=content, **changes)


def test_confirmation_cas_withdraw_and_reconfirm(repository):
    create = command()
    original = repository.mutate(None, create)
    assert repository.mutate(None, create) == original
    edited = repository.mutate(original["id"], command(1, content="优先用中文"))
    with pytest.raises(MemoryConflict):
        repository.mutate(original["id"], command(1, content="陈旧页面"))
    assert repository.get(original["id"])["content"] == "优先用中文"
    assert len(repository.get(original["id"])["versions"]) == 2
    repository.mutate(original["id"], command(2, "withdraw", ""))
    assert repository.list(active_only=True) == []
    assert repository.list()[0]["state"] == "withdrawn"
    repository.mutate(original["id"], command(3, content="再次明确确认"))
    assert repository.list(active_only=True)[0]["content"] == "再次明确确认"
    assert edited["current_version"] == 2


def test_delete_erases_all_text_and_tombstone_rejects_old_create(repository):
    create = command()
    item = repository.mutate(None, create)
    repository.mutate(item["id"], command(1, content="旧版本私人正文"))
    delete = command(2, "delete", "")
    result = repository.mutate(item["id"], delete)
    assert result["state"] == "deleted"
    assert repository.mutate(item["id"], delete)["state"] == "deleted"
    with pytest.raises(MemoryGone):
        repository.mutate(None, create)
    assert repository.list() == []
    with repository.sessions() as session:
        assert all(value == "" for value in session.scalars(select(ConfirmedMemoryVersion.content)))


def test_mutation_id_cannot_be_reused_for_other_content(repository):
    create = command()
    repository.mutate(None, create)
    with pytest.raises(MemoryConflict):
        repository.mutate(None, create.model_copy(update={"content": "不同内容"}))


@pytest.mark.parametrize("changes", [{"confirmed": False}, {"confirmed": "true"},
                                    {"owner": "someone-else"}, {"expected_version": True},
                                    {"content": "   "}])
def test_unconfirmed_and_scope_spoofing_rejected(changes):
    payload = dict(mutation_id=str(uuid4()), action="confirm", expected_version=0,
                   confirmed=True, content="内容")
    with pytest.raises(ValidationError):
        MemoryMutation.model_validate(payload | changes)


def test_repeated_withdraw_does_not_consume_confirmed_version_capacity(repository):
    item = repository.mutate(None, command())
    repository.mutate(item["id"], command(1, "withdraw", ""))
    for _ in range(105):
        result = repository.mutate(item["id"], command(2, "withdraw", ""))
        assert result["current_version"] == 2
    result = repository.mutate(item["id"], command(2, content="重新确认"))
    assert result["state"] == "active"
    assert result["current_version"] == 3
