from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from offerpilot.db import init_database
from offerpilot.models import ChatMessage, Conversation
from offerpilot.pilot_timeline import (
    AdmissionConflict,
    AdmissionGone,
    PilotTimelineRepository,
)
from offerpilot.repositories.chat import ChatRepository


def test_concurrent_admission_has_one_turn_one_user_and_one_execution_claim(tmp_path):
    factory = init_database(tmp_path / 'test.db')
    repository = PilotTimelineRepository(factory)
    barrier = Barrier(8)
    request_id = str(uuid4())

    def admit(_):
        barrier.wait()
        result = repository.admit(request_id, {'message': '你好', 'conversation_id': 0})
        return result, repository.claim(result.turn_id) if result.created else False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admit, range(8)))
    assert len({result.turn_id for result, _ in results}) == 1
    assert sum(result.created for result, _ in results) == 1
    assert sum(claim for _, claim in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 1


def test_retry_uses_first_sources_and_different_request_conflicts(tmp_path):
    factory = init_database(tmp_path / 'test.db')
    repository = PilotTimelineRepository(factory)
    key = str(uuid4())
    payload = {'message': '你好', 'conversation_id': 0}
    first = repository.admit(key, payload, freeze_sources=lambda _session, _conv: {'document:1': 'v1'})
    retried = PilotTimelineRepository(factory).admit(
        key, payload,
        freeze_sources=lambda _session, _conv: pytest.fail('Retry must not reload sources'),
    )
    assert retried.turn_id == first.turn_id
    assert not retried.created
    assert repository.get_turn(first.turn_id)['source_versions'] == {'document:1': 'v1'}
    assert repository.find_by_request(key)['turn_id'] == first.turn_id
    resolved_retry = repository.admit(key, {**payload, 'conversation_id': first.conversation_id})
    assert resolved_retry.turn_id == first.turn_id
    other = repository.admit(str(uuid4()), payload)
    with pytest.raises(AdmissionConflict):
        repository.admit(key, {**payload, 'conversation_id': other.conversation_id})
    with pytest.raises(AdmissionConflict):
        repository.admit(key, {**payload, 'message': '另一条消息'})
    independent = repository.admit(str(uuid4()), payload)
    assert independent.turn_id != first.turn_id


def test_source_failure_rolls_back_conversation_user_and_admission(tmp_path):
    factory = init_database(tmp_path / 'test.db')
    repository = PilotTimelineRepository(factory)
    key = str(uuid4())

    def fail(_session, _conv):
        raise RuntimeError('source unavailable')

    with pytest.raises(RuntimeError):
        repository.admit(key, {'message': '你好'}, freeze_sources=fail)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 0
    assert repository.admit(key, {'message': '你好'}).created


def test_conversation_delete_keeps_only_request_tombstone(tmp_path):
    factory = init_database(tmp_path / 'test.db')
    repository = PilotTimelineRepository(factory)
    key = str(uuid4())
    payload = {'message': 'private content', 'conversation_id': 0}
    first = repository.admit(key, payload)
    ChatRepository(factory).delete_conversation(first.conversation_id)
    with pytest.raises(AdmissionGone):
        repository.admit(key, payload)
    assert repository.get_turn(first.turn_id) is None
    second = repository.admit(str(uuid4()), payload)
    assert second.turn_id != first.turn_id
    with pytest.raises(AdmissionGone):
        repository.admit(key, payload)
