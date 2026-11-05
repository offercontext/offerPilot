"""Real SQLite/lease regressions for bounded companion fact result commits."""
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update

from offerpilot.companion.fact_read import CompanionFactReadService, SOURCE_KEY, freeze_fact_read
from offerpilot.companion.models import CompanionFactReadReceipt, CompanionState
from offerpilot.companion.repository import CompanionConflict
from offerpilot.models import ChatMessage, Conversation, PilotTurnMessage, PilotTurnRecord
from offerpilot.pilot_control import TurnExecutionFenced
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import life as life, command
from tests.test_companion_continuity import choose, next_activity
from tests.test_companion_master_switch import set_enabled
from offerpilot.companion.contracts import CompanionResetCommand


def admit(life, message='我们的小船叫什么？', conversation_id=0):
    request = SimpleNamespace(message=message, attachments=(), images=(), image_attachment_ids=(),
                              page_context=None, pilot_action=None)
    leases = []
    admission = life[3].admit(str(uuid4()), {'message': message, 'conversation_id': conversation_id},
        freeze_sources=lambda session, conversation: freeze_fact_read(session, conversation, request),
        on_admitted=lambda session, turn: leases.append(life[4].claim_start_in_session(session, turn.id)))
    return admission, leases[0]


def execute(life, admission, lease, message='我们的小船叫什么？'):
    with life[4].execution_scope(lease):
        return CompanionFactReadService(life[0]).execute(admission.user_message_id, message, admission.conversation_id)


def counts(life, turn_id):
    with life[0]() as session:
        return (session.scalar(select(func.count()).select_from(CompanionFactReadReceipt)
                               .where(CompanionFactReadReceipt.turn_id == turn_id)),
                session.scalar(select(func.count()).select_from(ChatMessage).join(PilotTurnMessage)
                               .where(PilotTurnMessage.turn_id == turn_id, ChatMessage.role == 'assistant')))


def test_verified_name_at_step_one_is_not_inferred_from_progress(life):
    choose(life, '叫橘子号吧')
    before = life[1].read()
    assert before['state']['activity']['step_index'] == 0
    admission, lease = admit(life)
    result = execute(life, admission, lease)
    assert '橘子号' in result and '还没' not in result and '刚挑' not in result
    assert counts(life, admission.turn_id) == (1, 1)
    assert life[1].read() == before
    with life[0]() as session:
        turn = session.get(PilotTurnRecord, admission.turn_id)
        frozen = json.loads(turn.source_versions_json)[SOURCE_KEY]
        receipt = session.get(CompanionFactReadReceipt, admission.turn_id)
        assert '橘子号' not in frozen and '橘子号' not in receipt.bindings_json


def test_new_conversation_third_party_name_is_unknown_without_history(life):
    message = '小周和小陈给他们的纸船取名“橘子号”。我只是转述。我们的船也叫这个吗？'
    admission, lease = admit(life, message)
    result = execute(life, admission, lease, message)
    assert '无法确认' in result
    assert all(value not in result for value in ('还没', '没有起', '刚挑', '橘子号', '相同', '不同'))


@pytest.mark.parametrize('temporal', ['刚才', '之前', '上次', '最近'])
def test_prior_role_mention_never_uses_latest_activity_or_assistant_as_anchor(life, temporal):
    answer = choose(life, '叫橘子号吧')
    next_activity(life)
    ChatRepository(life[0]).append_message(answer.conversation_id, 'assistant', '我们的小船已经折好第二折了。')
    message = f'正事先到这儿。你{temporal}说的小日常呢？'
    admission, lease = admit(life, message)
    result = execute(life, admission, lease, message)
    assert '哪一项' in result and '不能确定' in result
    assert all(value not in result for value in ('第二折', '橘子号', '第1步', '云朵'))


@pytest.mark.parametrize('change', ['revision', 'reset', 'scope_roundtrip', 'pending', 'clarification', 'delete_source', 'edit_source'])
def test_change_after_freeze_rejects_without_partial_result(life, change):
    answer = choose(life, '叫橘子号吧')
    admission, lease = admit(life)
    if change == 'revision':
        life[1].apply_event(command(life[1], 'pause'))
    elif change == 'disable':
        set_enabled(life[1], False)
    elif change == 'reset':
        state = life[1].read()
        life[1].reset(CompanionResetCommand(identity=state['identity'], expected_revision=state['revision'],
                                          command_id=uuid4(), confirmed=True))
    elif change == 'delete_source':
        ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    else:
        with life[0]() as session:
            if change == 'edit_source':
                session.execute(update(ChatMessage).where(ChatMessage.id == answer.user_message_id).values(content='叫不同号吧'))
            else:
                if change == 'scope_roundtrip':
                    session.execute(update(Conversation).where(Conversation.id == admission.conversation_id)
                                    .values(context_ref='changed', scope_revision=1))
                values = {'context_ref': '', 'scope_revision': 2} if change == 'scope_roundtrip' else (
                    {'pending_human': 'pending'} if change == 'pending' else {'clarification_question': 'clarify'})
                session.execute(update(Conversation).where(Conversation.id == admission.conversation_id).values(**values))
            session.commit()
    with pytest.raises((CompanionConflict, ValueError)):
        execute(life, admission, lease)
    assert counts(life, admission.turn_id) == (0, 0)


@pytest.mark.parametrize('bad_lease', ['generation', 'owner', 'turn'])
def test_wrong_execution_identity_cannot_commit(life, bad_lease):
    admission, lease = admit(life)
    invalid = replace(lease, **{'generation': {'generation': lease.generation + 1},
                               'owner': {'owner_token': 'not-the-owner'},
                               'turn': {'turn_id': str(uuid4())}}[bad_lease])
    with pytest.raises((CompanionConflict, TurnExecutionFenced)):
        execute(life, admission, invalid)
    assert counts(life, admission.turn_id) == (0, 0)


def test_commit_event_cancellation_rolls_back_both_rows_and_pool_is_clean(life):
    admission, lease = admit(life)
    engine = life[0].kw['bind']
    # Actual commit event, before the installed lease fence, rather than a mocked
    # service/repository. Cancellation must roll back flushed INSERTs too.
    with life[4].execution_scope(lease) as scope:
        checks = []
        def cancel(connection, cursor, statement, parameters, context, many):
            if statement.startswith('UPDATE pilot_executions SET lease_until_ms='):
                checks.append(statement)
                if len(checks) == 2:  # real commit-event fence after handler-entry fence
                    scope.revoked.set()
        event.listen(engine, 'after_cursor_execute', cancel)
        try:
            with pytest.raises(TurnExecutionFenced):
                CompanionFactReadService(life[0]).execute(admission.user_message_id,
                    '我们的小船叫什么？', admission.conversation_id)
        finally:
            event.remove(engine, 'after_cursor_execute', cancel)
    assert counts(life, admission.turn_id) == (0, 0)
    # Unrelated subsequent commit cannot accidentally publish rejected rows.
    with life[0]() as session:
        session.execute(update(Conversation).where(Conversation.id == admission.conversation_id).values(title='clean'))
        session.commit()
    assert counts(life, admission.turn_id) == (0, 0)


def test_commit_before_event_retry_returns_saved_result_not_current_state(life):
    choose(life, '叫橘子号吧')
    admission, lease = admit(life)
    result = execute(life, admission, lease)
    # Simulate lost publication by executing again under the still-live owner.
    with life[0]() as session:
        row = session.get(CompanionState, 1)
        row.revision += 1
        session.commit()
    assert execute(life, admission, lease) == result
    assert counts(life, admission.turn_id) == (1, 1)


def test_source_deletion_new_query_unknown_but_committed_transcript_is_history(life):
    answer = choose(life, '叫橘子号吧')
    admission, lease = admit(life)
    result = execute(life, admission, lease)
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    assert execute(life, admission, lease) == result
    other, other_lease = admit(life)
    assert '无法确认' in execute(life, other, other_lease)
    ChatRepository(life[0]).delete_conversation(admission.conversation_id)
    assert counts(life, admission.turn_id) == (0, 0)


def test_receipt_cannot_replay_other_turn_assistant(life):
    first, lease = admit(life)
    execute(life, first, lease)
    other, other_lease = admit(life)
    execute(life, other, other_lease)
    with life[0]() as session:
        target = session.get(CompanionFactReadReceipt, first.turn_id)
        victim = session.get(CompanionFactReadReceipt, other.turn_id)
        # Remove other's receipt to avoid the unique message constraint, then
        # corrupt the first reference: turn-message binding must still reject.
        victim_id = victim.assistant_message_id
        session.delete(victim)
        session.flush()
        target.assistant_message_id = victim_id
        session.commit()
    with pytest.raises(CompanionConflict, match='receipt'):
        execute(life, first, lease)


@pytest.mark.parametrize('when', ['before_admission', 'after_admission'])
@pytest.mark.parametrize('damage', ['event_delete', 'event_identity', 'event_kind', 'event_source',
                                    'frozen_identity', 'frozen_participation'])
def test_choice_event_and_frozen_provenance_are_required(life, when, damage):
    from sqlalchemy import delete
    from offerpilot.companion.models import CompanionChatEvidence, CompanionEvent
    from offerpilot.companion.chat import SOURCE_KEY as COMMAND_SOURCE_KEY
    answer = choose(life, '叫橘子号吧')
    if when == 'after_admission':
        admission, lease = admit(life)
    with life[0]() as session:
        evidence = session.scalar(select(CompanionChatEvidence).where(CompanionChatEvidence.message_id == answer.user_message_id))
        if damage == 'event_delete':
            session.execute(delete(CompanionEvent).where(CompanionEvent.id == evidence.event_id))
        elif damage.startswith('event_'):
            target = session.get(CompanionEvent, evidence.event_id)
            setattr(target, {'event_identity': 'identity', 'event_kind': 'event_type', 'event_source': 'source'}[damage],
                    str(uuid4()) if damage == 'event_identity' else 'invalid')
        else:
            turn = session.get(PilotTurnRecord, answer.turn_id)
            sources = json.loads(turn.source_versions_json)
            command_source = json.loads(sources[COMMAND_SOURCE_KEY])
            command_source['identity' if damage == 'frozen_identity' else 'participation_id'] = str(uuid4())
            sources[COMMAND_SOURCE_KEY] = json.dumps(command_source)
            turn.source_versions_json = json.dumps(sources)
        session.commit()
    if when == 'after_admission':
        with pytest.raises(CompanionConflict):
            execute(life, admission, lease)
        assert counts(life, admission.turn_id) == (0, 0)
    else:
        admission, lease = admit(life)
        result = execute(life, admission, lease)
        assert '无法确认' in result and '橘子号' not in result


def test_origami_question_does_not_substitute_cloud_activity(life):
    next_activity(life)
    assert life[1].read()['state']['activity']['template_id'] == 'cloud_catalog'
    message = '你的折纸做到哪一步了？'
    admission, lease = admit(life, message)
    result = execute(life, admission, lease, message)
    assert '不能确定' in result and '云' not in result


@pytest.mark.parametrize('repeat', [False, True])
def test_concurrent_execute_has_one_receipt_and_one_assistant(life, repeat):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    admission, lease = admit(life)
    if repeat:
        execute(life, admission, lease)
    barrier = Barrier(2)
    def run_one():
        barrier.wait()
        return execute(life, admission, lease)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_one) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1]
    assert counts(life, admission.turn_id) == (1, 1)


def test_replay_commit_event_cancel_keeps_old_rows_but_returns_no_result(life):
    admission, lease = admit(life)
    execute(life, admission, lease)
    engine = life[0].kw['bind']
    with life[4].execution_scope(lease) as scope:
        checks = []
        def cancel(connection, cursor, statement, parameters, context, many):
            if statement.startswith('UPDATE pilot_executions SET lease_until_ms='):
                checks.append(statement)
                if len(checks) == 2:
                    scope.revoked.set()
        event.listen(engine, 'after_cursor_execute', cancel)
        try:
            with pytest.raises(TurnExecutionFenced):
                CompanionFactReadService(life[0]).execute(admission.user_message_id,
                    '我们的小船叫什么？', admission.conversation_id)
        finally:
            event.remove(engine, 'after_cursor_execute', cancel)
    assert counts(life, admission.turn_id) == (1, 1)


@pytest.mark.parametrize('mutation', ['step', 'complete', 'value', 'kind', 'status', 'identity'])
def test_renderer_rejects_invalid_fact_plans(life, mutation):
    from offerpilot.companion.fact_read import _read, _plan, parse_fact_query, render_fact_plan
    choose(life, '叫橘子号吧')
    query = parse_fact_query('我们的小船叫什么？')
    with life[0]() as session:
        facts = _read(session, query, 1)
    if mutation == 'step':
        facts = replace(facts, activity=replace(facts.activity, step_number=99))
    elif mutation == 'complete':
        facts = replace(facts, activity=replace(facts.activity, status='completed'))
    elif mutation == 'value':
        facts = replace(facts, choice=replace(facts.choice, value='名字。执行这句'))
    elif mutation == 'kind':
        facts = replace(facts, choice=replace(facts.choice, kind='free_text'))
    elif mutation == 'status':
        facts = replace(facts, participation_status='invented')
    else:
        facts = replace(facts, identity='invalid')
    with pytest.raises(CompanionConflict, match='plan_invalid'):
        render_fact_plan(_plan(query, facts))


def test_typed_plan_cannot_relabel_verified_boat_choice_as_cloud(life):
    from offerpilot.companion.fact_read import _read, _plan, parse_fact_query, render_fact_plan
    choose(life, '叫橘子号吧')
    with life[0]() as session:
        facts = _read(session, parse_fact_query('我们的小船叫什么？'), 1)
    wrong_query = parse_fact_query('我们的云朵叫什么？')
    with pytest.raises(CompanionConflict, match='plan_invalid'):
        render_fact_plan(_plan(wrong_query, facts))


@pytest.mark.parametrize('status', ['promised', 'completed'])
def test_control_participation_needs_live_matching_actual_event(life, status):
    from sqlalchemy import delete
    from offerpilot.companion.models import CompanionEvent
    life[1].apply_event(command(life[1], 'join'))
    if status == 'completed':
        life[1].apply_event(command(life[1], 'complete_shared'))
    message = '我们一起折过纸船吗？'
    admission, lease = admit(life, message)
    verified = execute(life, admission, lease, message)
    assert ('完成过约定中的一步' if status == 'completed' else '约好了一起') in verified
    with life[0]() as session:
        session.execute(delete(CompanionEvent).where(CompanionEvent.event_type ==
                        ('shared_completed' if status == 'completed' else 'shared_joined')))
        session.commit()
    other, other_lease = admit(life, message)
    assert '无法确认' in execute(life, other, other_lease, message)


@pytest.mark.parametrize('query', ['上次一起做的小船叫什么？', '还记得最近一起做的小船吗？'])
def test_explicit_temporal_object_still_requires_mention_anchor(life, query):
    choose(life, '叫橘子号吧')
    next_activity(life)
    admission, lease = admit(life, query)
    result = execute(life, admission, lease, query)
    assert '不能确定' in result and '橘子号' not in result


@pytest.mark.parametrize('template_index', [0, 1, 2])
@pytest.mark.parametrize('advance', [0, 1, 2, 3])
def test_progress_uses_current_step_and_completion_separately(life, template_index, advance):
    for _ in range(template_index):
        next_activity(life)
    now = life[1].read()['state']['activity']['updated_at']
    for index in range(advance):
        life[1].apply_event(command(life[1], 'app_open'), now=now + 300 * (index + 1))
    before = life[1].read()
    message = '你的小日常怎么样了？'
    admission, lease = admit(life, message)
    result = execute(life, admission, lease, message)
    assert f'第{min(advance + 1, 3)}步' in result
    assert ('已完成' in result) is (advance == 3)
    assert '第4步' not in result and '第二折' not in result
    assert life[1].read() == before


@pytest.mark.parametrize('quoted,expected', [('橘子号', '相同'), ('柠檬号', '不同')])
def test_verified_name_comparison_does_not_adopt_third_party_report(life, quoted, expected):
    choose(life, '叫橘子号吧')
    message = f'小周和小陈给他们的纸船取名“{quoted}”。我只是转述。我们的船也叫这个吗？'
    admission, lease = admit(life, message)
    result = execute(life, admission, lease, message)
    assert '橘子号' in result and expected in result and '转述' in result
    assert '一步' not in result


def test_explicit_old_object_does_not_borrow_current_cloud(life):
    choose(life, '叫橘子号吧')
    next_activity(life)
    admission, lease = admit(life)
    result = execute(life, admission, lease)
    assert '橘子号' in result and '云朵' not in result


def test_multiple_boats_require_clarification_without_any_arbitrary_name(life):
    choose(life, '叫橘子号吧')
    for _ in range(3):
        next_activity(life)
    choose(life, '叫柠檬号吧')
    admission, lease = admit(life)
    result = execute(life, admission, lease)
    assert '哪一只' in result and '橘子号' not in result and '柠檬号' not in result


@pytest.mark.parametrize('change', ['cancel', 'expire'])
def test_cancelled_or_expired_lease_never_publishes(life, change):
    from offerpilot.models import PilotExecution
    admission, lease = admit(life)
    if change == 'cancel':
        life[4].interrupt(str(uuid4()), admission.turn_id, lease.generation)
    else:
        with life[0]() as session:
            session.execute(update(PilotExecution).where(PilotExecution.turn_id == admission.turn_id)
                            .values(lease_until_ms=0))
            session.commit()
    with pytest.raises(TurnExecutionFenced):
        execute(life, admission, lease)
    assert counts(life, admission.turn_id) == (0, 0)


def test_remember_name_composition_known_then_deleted_is_unknown(life):
    answer = choose(life, '叫橘子号吧')
    query = '还记得我们的小船叫什么吗？'
    admission, lease = admit(life, query)
    assert '橘子号' in execute(life, admission, lease, query)
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    other, other_lease = admit(life, query)
    result = execute(life, other, other_lease, query)
    assert '无法确认' in result and '橘子号' not in result


def test_receipt_migration_is_additive_and_reinitialization_preserves_data(life):
    from offerpilot.db import init_database
    admission, lease = admit(life)
    saved = execute(life, admission, lease)
    before = life[1].read()
    engine = life[0].kw['bind']
    from pathlib import Path
    again = init_database(Path(engine.url.database))
    with again() as session:
        receipt = session.get(CompanionFactReadReceipt, admission.turn_id)
        assert session.get(ChatMessage, receipt.assistant_message_id).content == saved
        assert session.get(PilotTurnMessage, receipt.assistant_message_id).turn_id == admission.turn_id
    assert life[1].read() == before


@pytest.mark.parametrize('toggles', [[False], [False, True, False]])
@pytest.mark.parametrize('delete_source', [False, True])
def test_currently_disabled_returns_only_mode_notice_without_fact_reads(life, toggles, delete_source):
    from offerpilot.companion.fact_read import DISABLED_NOTICE
    answer = choose(life, '叫橘子号吧')
    admission, lease = admit(life)
    for enabled in toggles:
        set_enabled(life[1], enabled)
    if delete_source:
        ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    statements = []
    engine = life[0].kw['bind']
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        result = execute(life, admission, lease)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert result == DISABLED_NOTICE and '橘子号' not in result
    forbidden = ('life_json', 'companion_chat_evidence', 'companion_participation', 'companion_events', 'persona_')
    assert not any(word in sql for word in forbidden for sql in statements)
    assert counts(life, admission.turn_id) == (1, 1)
    with life[0]() as session:
        receipt = session.get(CompanionFactReadReceipt, admission.turn_id)
        assert json.loads(receipt.bindings_json)['result_kind'] == 'disabled_notice'


def test_aba_final_enabled_does_not_reuse_original_facts(life):
    admission, lease = admit(life)
    set_enabled(life[1], False)
    set_enabled(life[1], True)
    with pytest.raises(CompanionConflict, match='source_changed'):
        execute(life, admission, lease)
    assert counts(life, admission.turn_id) == (0, 0)


def test_reset_while_disabled_still_rejects_identity_change(life):
    admission, lease = admit(life)
    set_enabled(life[1], False)
    view = life[1].read()
    life[1].reset(CompanionResetCommand(identity=view['identity'], expected_revision=view['revision'],
                                      command_id=uuid4(), confirmed=True))
    with pytest.raises(CompanionConflict, match='source_changed'):
        execute(life, admission, lease)
    assert counts(life, admission.turn_id) == (0, 0)


@pytest.mark.parametrize('already_committed', [False, True])
def test_disabled_notice_commit_and_replay_cancel_use_same_atomic_fence(life, already_committed):
    admission, lease = admit(life)
    set_enabled(life[1], False)
    if already_committed:
        execute(life, admission, lease)
    engine = life[0].kw['bind']
    with life[4].execution_scope(lease) as scope:
        checks = []
        def cancel(connection, cursor, statement, parameters, context, many):
            if statement.startswith('UPDATE pilot_executions SET lease_until_ms='):
                checks.append(statement)
                if len(checks) == 2:
                    scope.revoked.set()
        event.listen(engine, 'after_cursor_execute', cancel)
        try:
            with pytest.raises(TurnExecutionFenced):
                CompanionFactReadService(life[0]).execute(admission.user_message_id,
                    '我们的小船叫什么？', admission.conversation_id)
        finally:
            event.remove(engine, 'after_cursor_execute', cancel)
    assert counts(life, admission.turn_id) == ((1, 1) if already_committed else (0, 0))


def test_notice_replay_stays_saved_after_reenable_and_subsequent_close(life):
    from offerpilot.companion.fact_read import DISABLED_NOTICE
    admission, lease = admit(life)
    set_enabled(life[1], False)
    assert execute(life, admission, lease) == DISABLED_NOTICE
    for enabled in (True, False):
        set_enabled(life[1], enabled)
        assert execute(life, admission, lease) == DISABLED_NOTICE
    assert counts(life, admission.turn_id) == (1, 1)


def test_disabled_notice_never_invokes_fact_loader_or_renderer(life, monkeypatch):
    import offerpilot.companion.fact_read as fact_read
    admission, lease = admit(life)
    set_enabled(life[1], False)
    def forbidden(*args, **kwargs):
        pytest.fail('disabled status must not load or render companion facts')
    monkeypatch.setattr(fact_read, '_read', forbidden)
    monkeypatch.setattr(fact_read, 'render_fact_plan', forbidden)
    assert execute(life, admission, lease) == fact_read.DISABLED_NOTICE


def test_close_after_fact_commit_does_not_rewrite_committed_history(life):
    from offerpilot.companion.fact_read import DISABLED_NOTICE
    choose(life, '叫橘子号吧')
    admission, lease = admit(life)
    committed = execute(life, admission, lease)
    set_enabled(life[1], False)
    assert execute(life, admission, lease) == committed
    assert '橘子号' in committed and committed != DISABLED_NOTICE
    assert counts(life, admission.turn_id) == (1, 1)


@pytest.mark.parametrize('query', ['你的小船折到哪了？', '纸船现在做到第几步了？'])
def test_colloquial_object_progress_never_substitutes_other_current_template(life, query):
    choose(life, '叫橘子号吧')
    first, first_lease = admit(life, query)
    assert '第1步' in execute(life, first, first_lease, query)
    next_activity(life)
    assert life[1].read()['state']['activity']['template_id'] == 'cloud_catalog'
    other, lease = admit(life, query)
    result = execute(life, other, lease, query)
    assert '无法确认' in result
    assert all(word not in result for word in ('第1步', '云', '橘子号'))


@pytest.mark.parametrize('query', ['我们的小船叫什么来着？？', '你还记得我们的小船叫什么吗？',
                                   '咱们给小船起的名字是什么？', '我们的小船叫什么呀？', '我们的小船叫什么！'])
def test_colloquial_naming_preserves_verified_name_and_deleted_source_unknown(life, query):
    source = choose(life, '叫橘子号吧')
    first, first_lease = admit(life, query)
    assert '橘子号' in execute(life, first, first_lease, query)
    ChatRepository(life[0]).delete_conversation(source.conversation_id)
    second, lease = admit(life, query)
    result = execute(life, second, lease, query)
    assert '无法确认' in result and '橘子号' not in result and '还没' not in result


@pytest.mark.parametrize('query', ['你刚才说的纸船怎么样了？', '还记得我们上次一起折的纸船吗？',
                                   '正事先到这儿\n你刚才说的小日常呢？', '我们那只小船叫啥来着？'])
def test_new_past_or_demonstrative_queries_do_not_forge_reference_anchor(life, query):
    choose(life, '叫橘子号吧')
    next_activity(life)
    first, lease = admit(life, query)
    result = execute(life, first, lease, query)
    assert '不能确定' in result and '哪一项' in result
    assert all(word not in result for word in ('橘子号', '云朵', '第1步'))
