from uuid import uuid4

import pytest
from sqlalchemy import event, text

from offerpilot.db import init_database
from offerpilot.pilot_timeline import PilotTimelineRepository, TimelineResyncRequired, TimelineSource
from offerpilot.presentation_contracts import PilotTurnItemV1


def setup_timeline(tmp_path):
    factory = init_database(tmp_path / 'timeline.db')
    repository = PilotTimelineRepository(factory)
    turn = repository.admit(str(uuid4()), {'message': '输入'})
    return factory, repository, turn


def message(turn, number, content):
    return TimelineSource(
        turn_id=turn.turn_id,
        item=PilotTurnItemV1(item_id=f'message:{number}', kind='assistant_message', message_id=number, content=content),
        source_refs=(f'message:{number}',),
    )


def test_old_item_update_is_returned_after_later_ordinals(tmp_path):
    _, repository, turn = setup_timeline(tmp_path)
    sources = [message(turn, n, f'回复 {n}') for n in range(1, 22)]
    first = repository.read_timeline(turn.conversation_id, lambda _session: sources)
    sources[4] = message(turn, 5, '已撤销')
    changes = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=first['cursor'])
    assert len(changes['items']) == 1
    changed = changes['items'][0]
    assert changed['item_id'] == first['items'][4]['item_id']
    assert changed['ordinal'] == 5
    assert changed['change_seq'] > first['high_watermark']
    assert changed['revision'] == 2
    assert changed['payload']['content'] == '已撤销'
    again = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=changes['cursor'])
    assert again['items'] == []
    assert again['high_watermark'] == changes['high_watermark']


def test_snapshot_pages_share_high_watermark_and_changes_arrive_after_it(tmp_path):
    _, repository, turn = setup_timeline(tmp_path)
    sources = [message(turn, n, f'原文 {n}') for n in range(1, 4)]
    first = repository.read_timeline(turn.conversation_id, lambda _session: sources, limit=1)
    sources[1] = message(turn, 2, '新内容')
    second = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=first['next_cursor'], limit=1)
    assert second['high_watermark'] == first['high_watermark']
    assert second['items'][0]['payload']['content'] == '原文 2'
    third = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=second['next_cursor'], limit=1)
    assert third['next_cursor'] is None
    delta = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=third['cursor'])
    assert [item['payload']['content'] for item in delta['items']] == ['新内容']


def test_cursor_cannot_cross_conversations_or_be_modified(tmp_path):
    _, repository, turn = setup_timeline(tmp_path)
    first = repository.read_timeline(turn.conversation_id, lambda _session: [])
    other = repository.admit(str(uuid4()), {'message': '其他会话'})
    with pytest.raises(TimelineResyncRequired):
        repository.read_timeline(other.conversation_id, lambda _session: [], cursor=first['cursor'])
    with pytest.raises(TimelineResyncRequired):
        repository.read_timeline(turn.conversation_id, lambda _session: [], cursor=first['cursor'] + 'x')


def test_deletion_returns_tombstone_and_scrubs_all_retained_versions(tmp_path):
    factory, repository, turn = setup_timeline(tmp_path)
    sources = [message(turn, 1, '秘密旧版本'), message(turn, 2, '保留')]
    first = repository.read_timeline(turn.conversation_id, lambda _session: sources, limit=1)
    sources[0] = message(turn, 1, '秘密新版本')
    updated = repository.read_timeline(turn.conversation_id, lambda _session: sources)
    sources.pop(0)
    deleted = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=updated['cursor'])
    assert len(deleted['items']) == 1
    assert deleted['items'][0]['deleted'] is True
    assert deleted['items'][0]['payload'] is None
    with factory() as session:
        for table in ('pilot_timeline_items', 'pilot_timeline_changes'):
            rows = session.execute(text(f'SELECT payload_json FROM {table}')).scalars().all()
            assert '秘密' not in ''.join(rows)
    with pytest.raises(TimelineResyncRequired):
        repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=first['next_cursor'])


def test_projection_failure_rolls_back_item_revision_and_change_sequence(tmp_path):
    factory, repository, turn = setup_timeline(tmp_path)
    sources = [message(turn, 1, '原文')]
    first = repository.read_timeline(turn.conversation_id, lambda _session: sources)
    sources[0] = message(turn, 1, '已更新')
    engine = factory.kw['bind']

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('INSERT INTO pilot_timeline_changes'):
            raise RuntimeError('projection unavailable')

    event.listen(engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError):
            repository.read_timeline(turn.conversation_id, lambda _session: sources)
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    recovered = repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=first['cursor'])
    assert recovered['items'][0]['revision'] == 2
    assert recovered['high_watermark'] == first['high_watermark'] + 1


def test_invalidated_snapshot_commits_privacy_purge_before_resync_error(tmp_path):
    factory, repository, turn = setup_timeline(tmp_path)
    sources = [message(turn, 1, '已删除的秘密'), message(turn, 2, '保留消息')]
    first = repository.read_timeline(turn.conversation_id, lambda _session: sources, limit=1)
    sources.pop(0)
    with pytest.raises(TimelineResyncRequired):
        repository.read_timeline(turn.conversation_id, lambda _session: sources, cursor=first['next_cursor'])
    with factory() as session:
        for table in ('pilot_timeline_items', 'pilot_timeline_changes'):
            rows = session.execute(text(f'SELECT payload_json FROM {table}')).scalars().all()
            assert '已删除的秘密' not in ''.join(rows)
