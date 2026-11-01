"""Entity-bound recall must not substitute the newest unrelated life choice."""
from uuid import uuid4

from sqlalchemy import select

from offerpilot.companion.context import companion_context_message
from offerpilot.companion.contracts import CompanionResetCommand
from offerpilot.companion.models import CompanionEvent
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import life as life, run
from tests.test_companion_continuity import choose, next_activity, read


def test_natural_object_questions_recall_the_boat_not_the_newer_cloud(life):
    boat = choose(life, '叫棉花糖号吧')
    boat_id = life[1].read()['state']['activity']['id']
    next_activity(life)
    choose(life, '叫软糖云吧')
    for question in ('还记得我们的小船吗', '我们的小船叫什么', '你的小船叫什么？'):
        data = read(life, question)
        assert data is not None
        choice = data['recent_shared_choice']
        assert choice['activity_id'] == boat_id
        assert choice['value'] == '棉花糖号'
        assert choice['source_message_id'] == boat.user_message_id
        assert choice['virtual_activity_ref']['template_id'] == 'paper_boat'
        assert choice['activity_relation'] == 'prior_activity'
        assert data['virtual_activity'] is None  # Unrelated cloud state is not the answer.
        assert data['actual_user_participation'] is None
        assert '软糖云' not in companion_context_message(data).content
    assert read(life, '我们的小船叫什么，先帮我整理简历') is None


def test_repeated_objects_require_clarification_unless_explicitly_latest(life):
    choose(life, '叫棉花糖号吧')
    for _ in range(3):
        next_activity(life)
    choose(life, '叫小月亮号吧')
    ambiguous = read(life, '我们的小船叫什么？')
    assert ambiguous['recall_status'] == 'ambiguous_activity'
    assert 'recent_shared_choice' not in ambiguous
    assert ambiguous['actual_user_participation'] is None
    latest = read(life, '上次一起做的小船叫什么？')
    assert latest['recent_shared_choice']['value'] == '小月亮号'


def test_unknown_legacy_or_conflicting_activity_binding_never_guesses_from_prose(life):
    choose(life, '叫棉花糖号吧')
    next_activity(life)
    with life[0]() as session:
        boat_events = session.scalars(select(CompanionEvent).where(CompanionEvent.template_id == 'paper_boat')).all()
        for event in boat_events:
            event.template_id = None
        session.commit()
    legacy = read(life, '我们的小船叫什么？')
    assert legacy['recall_status'] == 'source_unavailable'
    assert 'recent_shared_choice' not in legacy
    with life[0]() as session:
        first = session.get(CompanionEvent, boat_events[0].id)
        second = session.get(CompanionEvent, boat_events[-1].id)
        first.template_id = 'paper_boat'
        second.template_id = 'cloud_catalog'
        session.commit()
    assert 'recent_shared_choice' not in read(life, '我们的小船叫什么？')


def test_object_recall_disappears_with_source_deletion_and_reset(life):
    boat = choose(life, '叫棉花糖号吧')
    next_activity(life)
    choose(life, '叫软糖云吧')
    ChatRepository(life[0]).delete_conversation(boat.conversation_id)
    deleted = read(life, '我们的小船叫什么？')
    assert deleted['recall_status'] == 'source_unavailable'
    assert 'recent_shared_choice' not in deleted
    snapshot = life[1].read()
    life[1].reset(CompanionResetCommand(identity=snapshot['identity'], expected_revision=snapshot['revision'],
        command_id=uuid4(), confirmed=True))
    assert 'recent_shared_choice' not in read(life, '我们的小船叫什么？')


def test_object_question_is_read_only_and_does_not_rearm_an_answer_slot(life):
    with run(life, '等我回来一起选颜色') as (first, execute):
        execute()
    before = life[1].read()['revision']
    with run(life, '我们的小船怎么样了？', first.conversation_id):
        assert read(life, '我们的小船怎么样了？', first.conversation_id)['virtual_activity']['status'] == 'waiting_shared'
    with run(life, '蓝色', first.conversation_id) as (answer, _):
        assert not life[2].matches(answer.user_message_id, '蓝色', answer.conversation_id)
    assert life[1].read()['revision'] == before


def test_object_targeting_is_not_lost_when_question_wording_varies(life):
    choose(life, '叫棉花糖号吧')
    next_activity(life)
    choose(life, '叫软糖云吧')
    for question in ('你还记得我们一起折的小船叫什么名字吗？', '能告诉我你的小船后来叫什么了吗？', '小船的名字？'):
        assert read(life, question)['recent_shared_choice']['value'] == '棉花糖号'
    for question in ('我们的小船和云朵叫什么？', '我们的小船和你的云朵呢？'):
        data = read(life, question)
        assert data['recall_status'] == 'ambiguous_activity'
        assert 'recent_shared_choice' not in data
        assert data['actual_user_participation'] is None
    assert read(life, '故事里的小船和星星真漂亮') is None


def test_question_kind_never_substitutes_a_name_for_a_color(life):
    choose(life, '叫棉花糖号吧')
    next_activity(life)
    data = read(life, '我们的小船用了什么颜色？')
    assert data['recall_status'] == 'source_unavailable'
    assert 'recent_shared_choice' not in data


def test_long_object_name_is_still_available_in_bounded_envelope(life):
    choose(life, '叫' + '棉' * 24 + '吧')
    data = read(life, '我们的小船叫什么？')
    message = companion_context_message(data)
    assert message is not None
    assert '选择内容是' in message.content
    assert '棉' * 24 in message.content


def test_latest_shared_object_does_not_choose_a_new_unshared_object(life):
    answer = choose(life, '叫棉花糖号吧')
    boat_id = life[1].read()['state']['activity']['id']
    for _ in range(3):
        next_activity(life)
    current_id = life[1].read()['state']['activity']['id']
    assert current_id != boat_id
    for question in ('上次一起做的小船叫什么？', '最近一起折的小船叫什么？'):
        data = read(life, question)
        assert data['recent_shared_choice']['activity_id'] == boat_id
        assert data['recent_shared_choice']['source_message_id'] == answer.user_message_id
        assert data['recent_shared_choice']['value'] == '棉花糖号'
        assert data['virtual_activity'] is None


def test_current_state_type_and_event_binding_disagreement_invalidates_entity(life):
    choose(life, '叫棉花糖号吧')
    with life[0]() as session:
        for event in session.scalars(select(CompanionEvent)):
            event.template_id = 'cloud_catalog'
        session.commit()
    for question in ('我们的小船叫什么？', '我们的云朵叫什么？'):
        data = read(life, question)
        assert data['recall_status'] == 'source_unavailable'
        assert 'recent_shared_choice' not in data
        assert data['virtual_activity'] is None
