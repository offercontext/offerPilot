from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError

from offerpilot.db import init_database
from offerpilot.models import Conversation, PilotExecution
from offerpilot.pilot_control import PilotControlRepository, TurnControlConflict, TurnExecutionFenced
from offerpilot.pilot_timeline import PilotTimelineRepository
from offerpilot.repositories.chat import ChatRepository


@pytest.fixture
def control_store(tmp_path):
    factory = init_database(tmp_path / 'control.db')
    now = [1_000_000]
    controls = PilotControlRepository(factory, now_ms=lambda: now[0], lease_ms=30_000)
    turns = PilotTimelineRepository(factory)
    return factory, controls, turns, now


def admit(turns, conversation_id=0):
    return turns.admit(str(uuid4()), {'message': '测试执行', 'conversation_id': conversation_id})


def test_only_one_generation_can_claim_a_conversation(control_store):
    _, controls, turns, _ = control_store
    first = admit(turns)
    second = admit(turns, first.conversation_id)
    barrier = Barrier(2)

    def claim(turn_id):
        barrier.wait()
        try:
            return controls.claim_start(turn_id)
        except TurnControlConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(claim, [first.turn_id, second.turn_id]))
    assert sum(value is not None for value in winners) == 1
    winner = next(value for value in winners if value is not None)
    assert winner.generation == 1
    with pytest.raises(TurnControlConflict):
        controls.claim_start(winner.turn_id)


def test_stop_is_durable_idempotent_and_content_bound(control_store):
    factory, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    command_id = str(uuid4())
    stopped = controls.interrupt(command_id, turn.turn_id, lease.generation)
    assert stopped['status'] == 'stopped'
    restarted = PilotControlRepository(factory, now_ms=lambda: now[0])
    assert restarted.interrupt(command_id, turn.turn_id, lease.generation) == stopped
    assert restarted.interrupt(str(uuid4()), turn.turn_id, lease.generation)['status'] == 'stopped'
    assert not controls.renew(lease)
    with pytest.raises(TurnControlConflict):
        controls.interrupt(command_id, turn.turn_id, lease.generation + 1)
    other = admit(turns)
    with pytest.raises(TurnControlConflict):
        controls.interrupt(command_id, other.turn_id, lease.generation)
    assert controls.get_execution(turn.turn_id)['state'] == 'stopped'


def test_old_generation_command_cannot_stop_current_owner(control_store):
    _, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    result = controls.interrupt(str(uuid4()), turn.turn_id, lease.generation + 1)
    assert result['status'] == 'generation_changed'
    assert controls.renew(lease)
    assert controls.get_execution(turn.turn_id)['state'] == 'running'


@pytest.mark.parametrize('state, status', [('completed', 'already_ended'), ('waiting_confirmation', 'no_active_execution')])
def test_ended_generation_has_no_interruptible_worker(control_store, state, status):
    _, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    controls.finish(lease, state)
    assert controls.interrupt(str(uuid4()), turn.turn_id, lease.generation)['status'] == status
    assert controls.get_execution(turn.turn_id)['state'] == state
    assert not controls.renew(lease)


@pytest.mark.parametrize('offset', [30_001, -1])
def test_expired_or_backwards_clock_never_renews_old_owner(control_store, offset):
    _, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    now[0] += offset
    assert not controls.renew(lease)
    assert controls.interrupt(str(uuid4()), turn.turn_id, lease.generation)['status'] == 'result_unknown'
    controls.finish(lease, 'completed')
    assert controls.get_execution(turn.turn_id)['state'] == 'result_unknown'


def test_expiry_observed_on_read_cannot_revive_when_wall_clock_moves_back(control_store):
    _, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    now[0] += 31_000
    assert controls.get_execution(turn.turn_id)['state'] == 'result_unknown'
    now[0] -= 31_000
    assert not controls.renew(lease)


def test_deleted_conversation_cannot_be_controlled_by_old_identity(control_store):
    factory, controls, turns, _ = control_store
    old = admit(turns)
    lease = controls.claim_start(old.turn_id)
    ChatRepository(factory).delete_conversation(old.conversation_id)
    new = admit(turns)
    new_lease = controls.claim_start(new.turn_id)
    with pytest.raises(LookupError):
        controls.interrupt(str(uuid4()), old.turn_id, lease.generation)
    assert controls.renew(new_lease)
    assert controls.get_execution(old.turn_id) is None


def test_execution_identity_cannot_bind_a_turn_to_another_conversation(control_store):
    factory, _, turns, now = control_store
    first = admit(turns)
    other = admit(turns)
    with factory() as session, pytest.raises(IntegrityError):
        session.add(PilotExecution(turn_id=first.turn_id, generation=1,
                                  conversation_id=other.conversation_id, owner_token='forged',
                                  state='running', renewed_at_ms=now[0], lease_until_ms=now[0] + 30_000))
        session.commit()


@pytest.mark.parametrize('raw_connection', [False, True])
def test_stop_before_commit_rolls_back_late_output_in_actual_transaction(control_store, raw_connection):
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    with controls.execution_scope(lease):
        if raw_connection:
            with factory.kw['bind'].connect() as connection:
                connection.execute(select(Conversation.id))
                controls.interrupt(str(uuid4()), turn.turn_id, lease.generation)
                connection.execute(update(Conversation).where(Conversation.id == turn.conversation_id).values(title='late'))
                with pytest.raises(TurnExecutionFenced):
                    connection.commit()
        else:
            with factory() as session:
                conversation = session.get(Conversation, turn.conversation_id)
                controls.interrupt(str(uuid4()), turn.turn_id, lease.generation)
                conversation.title = 'late'
                with pytest.raises(TurnExecutionFenced):
                    session.commit()
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title != 'late'
    # Reusing a pooled connection for an independent user action is allowed.
    with factory() as session:
        session.get(Conversation, turn.conversation_id).title = 'user action'
        session.commit()


def test_committed_write_is_kept_when_stop_waits_for_its_transaction(control_store):
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    writer_has_lock = Event()
    stop_started = Event()

    def write():
        with controls.execution_scope(lease), factory() as session:
            session.get(Conversation, turn.conversation_id).title = 'committed'
            session.flush()
            writer_has_lock.set()
            assert stop_started.wait(5)
            session.commit()

    def stop():
        assert writer_has_lock.wait(5)
        stop_started.set()
        return controls.interrupt(str(uuid4()), turn.turn_id, lease.generation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        stopper = pool.submit(stop)
        writer.result(timeout=10)
        assert stopper.result(timeout=10)['status'] == 'stopped'
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title == 'committed'


def test_fence_rejects_a_lease_that_expired_before_commit(control_store):
    factory, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    with controls.execution_scope(lease), factory() as session:
        session.get(Conversation, turn.conversation_id).title = 'expired'
        now[0] += 30_001
        with pytest.raises(TurnExecutionFenced):
            session.commit()
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title != 'expired'


def test_monotonic_expiry_cannot_be_extended_by_a_late_heartbeat(control_store, monkeypatch):
    from offerpilot.pilot_runtime.turn_control import DurableRuntimeInvocationControl
    _, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    clock = [10.0]
    control = DurableRuntimeInvocationControl(controls, lease, monotonic_clock=lambda: clock[0])
    renew = controls.renew

    def slow_renew(value):
        now[0] += 10_000
        renewed = renew(value)
        clock[0] += 31.0
        return renewed

    monkeypatch.setattr(controls, 'renew', slow_renew)
    try:
        assert not control._renew_once()
        assert not control.is_active()
        assert controls.get_execution(turn.turn_id)['state'] == 'result_unknown'
        control.finish('completed')
        assert controls.get_execution(turn.turn_id)['state'] == 'result_unknown'
    finally:
        control.finish('interrupted')


def test_registry_shutdown_fences_owners_but_keeps_committed_facts(control_store):
    from offerpilot.pilot_runtime.turn_control import TurnControlRegistry
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    registry = TurnControlRegistry(controls)
    control = registry.create(lease)
    registry.close()
    assert not control.is_active()
    assert controls.get_execution(turn.turn_id)['state'] == 'interrupted'
    assert ChatRepository(factory).list_messages(turn.conversation_id)
    with pytest.raises(RuntimeError):
        registry.create()


def test_fence_rechecks_expiry_after_waiting_for_the_database_lock(control_store):
    factory, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    engine = factory.kw['bind']

    def delayed_update(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('UPDATE pilot_executions'):
            now[0] += 31_000

    event.listen(engine, 'after_cursor_execute', delayed_update)
    try:
        with controls.execution_scope(lease), factory() as session:
            session.get(Conversation, turn.conversation_id).title = 'late-lock'
            with pytest.raises(TurnExecutionFenced):
                session.commit()
    finally:
        event.remove(engine, 'after_cursor_execute', delayed_update)
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title != 'late-lock'


def test_renew_reads_time_after_acquiring_the_database_lock(control_store):
    factory, controls, turns, now = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    engine = factory.kw['bind']

    def delayed_begin(_connection, _cursor, statement, _parameters, _context, _many):
        if statement == 'BEGIN IMMEDIATE':
            now[0] += 31_000

    event.listen(engine, 'after_cursor_execute', delayed_begin)
    try:
        assert not controls.renew(lease)
    finally:
        event.remove(engine, 'after_cursor_execute', delayed_begin)
    assert controls.get_execution(turn.turn_id)['state'] == 'result_unknown'


@pytest.mark.parametrize('superseded', ['stopped', 'new_execution'])
def test_timeout_recovery_cannot_overwrite_a_stop_or_a_new_execution(control_store, superseded):
    from offerpilot.pilot_runtime.turn_control import DurableRuntimeInvocationControl
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    control = DurableRuntimeInvocationControl(controls, controls.claim_start(turn.turn_id))
    if superseded == 'stopped':
        controls.interrupt(str(uuid4()), turn.turn_id, 1)
    control.request_timeout()
    if superseded == 'new_execution':
        newer = admit(turns, turn.conversation_id)
        controls.claim_start(newer.turn_id)
    try:
        with pytest.raises(TurnExecutionFenced):
            control.run_if_active(lambda: ChatRepository(factory).append_message(
                turn.conversation_id, 'assistant', content='stale timeout',
            ), allow_timeout=True)
    finally:
        control.finish('interrupted')
    assert all(message.content != 'stale timeout' for message in ChatRepository(factory).list_messages(turn.conversation_id))


def test_title_generation_does_not_start_after_stop_and_cannot_commit_after_new_execution(control_store):
    from offerpilot.pilot_runtime.turn_control import DurableRuntimeInvocationControl
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    control = DurableRuntimeInvocationControl(controls, controls.claim_start(turn.turn_id))
    controls.interrupt(str(uuid4()), turn.turn_id, 1)
    calls = []
    assert control.run_title_if_successful(lambda: calls.append('called')) is None
    assert not calls
    control.finish('interrupted')
    second = admit(turns, turn.conversation_id)
    completed = DurableRuntimeInvocationControl(controls, controls.claim_start(second.turn_id))
    completed.finish('completed')

    def late_title():
        newer = admit(turns, turn.conversation_id)
        controls.claim_start(newer.turn_id)
        ChatRepository(factory).apply_generated_title(turn.conversation_id, 'stale generated title')

    with pytest.raises(TurnExecutionFenced):
        completed.run_title_if_successful(late_title)
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title != 'stale generated title'


@pytest.mark.parametrize('field, value', [('owner_token', 'forged'), ('generation', 2), ('conversation_id', 999)])
def test_forged_worker_identity_cannot_renew_finish_or_commit(control_store, field, value):
    factory, controls, turns, _ = control_store
    turn = admit(turns)
    lease = controls.claim_start(turn.turn_id)
    forged = replace(lease, **{field: value})
    assert not controls.renew(forged)
    controls.finish(forged, 'completed')
    assert controls.get_execution(turn.turn_id)['state'] == 'running'
    with controls.execution_scope(forged), factory() as session:
        session.get(Conversation, turn.conversation_id).title = 'forged'
        with pytest.raises(TurnExecutionFenced):
            session.commit()
    assert ChatRepository(factory).get_conversation(turn.conversation_id).title != 'forged'
