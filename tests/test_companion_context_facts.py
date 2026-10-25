"""Model-facing facts distinguish progress, ambiguous recall and missing sources.

These regressions come from real replies in runs 39736858377 and 39746585328. They
verify the supplied facts, not a stubbed model's ability to follow them.
"""
import pytest

from offerpilot.companion.context import companion_context_message
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import life as life
from tests.test_companion_context import companion as companion, context_data, event
from tests.test_companion_continuity import choose, next_activity, read


@pytest.mark.parametrize('advances', [0, 1, 2, 3])
def test_model_progress_is_one_based_and_does_not_invent_a_fourth_step(companion, advances):
    _, repository, loader = companion
    event(repository, 'app_open', now=1000)
    for index in range(advances):
        event(repository, 'app_open', now=1300 + index * 300)
    before = repository.read(2000)
    data = context_data(loader)
    activity = data['virtual_activity']
    assert activity['step_number'] == min(advances + 1, 3)
    assert activity['step_count'] == 3
    assert activity['is_final_step'] is (advances >= 2)
    assert 'step_index' not in activity  # Storage indexing is not dialogue indexing.
    assert activity['status'] == ('completed' if advances == 3 else 'active')
    assert repository.read(2000) == before
    assert before['state']['activity']['step_index'] == min(advances, 2)
    projected = companion_context_message(data).content
    assert f"共3步，到了第{min(advances + 1, 3)}步" in projected
    assert "第4步" not in projected
    assert ("活动已完成" in projected) is (advances == 3)
    assert "step_number" not in projected and "active" not in projected


@pytest.mark.parametrize('action,status', [('pause', 'paused'), ('join', 'waiting_shared')])
def test_human_progress_facts_do_not_advance_paused_or_shared_waiting(companion, action, status):
    _, repository, loader = companion
    event(repository, 'app_open', now=1000)
    event(repository, action, now=1001)
    event(repository, 'app_open', now=1_000_000)
    data = context_data(loader)
    assert data['virtual_activity']['status'] == status
    assert data['virtual_activity']['step_number'] == 1
    assert data['virtual_activity']['is_final_step'] is False


def test_ambiguous_recall_supplies_explanation_not_absence_or_an_arbitrary_name(life):
    choose(life, '叫棉花糖号吧')
    for _ in range(3):
        next_activity(life)
    choose(life, '叫小月亮号吧')
    before = life[1].read()
    data = read(life, '我们的小船叫什么？')
    assert data['recall_status'] == 'ambiguous_activity'
    assert '多个' in data['recall_explanation'] and '尚未确定' in data['recall_explanation']
    message = companion_context_message(data)
    assert data['recall_explanation'] in message.content
    assert '棉花糖号' not in message.content and '小月亮号' not in message.content
    assert 'recent_shared_choice' not in data
    assert life[1].read() == before


def test_unavailable_source_does_not_assert_the_shared_history_never_happened(life):
    answer = choose(life, '叫棉花糖号吧')
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    data = read(life, '我们的小船叫什么？')
    assert data['recall_status'] == 'source_unavailable'
    assert '无法核实' in data['recall_explanation']
    assert '从未' in data['recall_explanation']
    message = companion_context_message(data)
    assert '棉花糖号' not in message.content
    assert data['actual_user_participation'] is None


def test_exact_source_choice_does_not_receive_ambiguous_or_missing_explanation(life):
    choose(life, '叫棉花糖号吧')
    data = read(life, '我们的小船叫什么？')
    assert 'recall_status' not in data and 'recall_explanation' not in data
    assert data['recent_shared_choice']['value'] == '棉花糖号'
    assert data['recent_shared_choice']['scope'] == 'shared_step_only'
    assert data['recent_shared_choice']['step_kind'] == 'name'


def test_same_type_candidates_are_not_shared_making_evidence(life):
    # Real run 39746585328 case 10: "我们" plus two candidates became
    # "一起叠过" despite an empty actual-participation projection.
    choose(life, '叫棉花糖号吧')
    for _ in range(3):
        next_activity(life)
    choose(life, '叫小月亮号吧')
    before = life[1].read()
    data = read(life, '我们的小船叫什么？')
    assert data['actual_user_participation'] is None
    assert data['virtual_activity'] is None
    assert data['requested_virtual_object'] == 'paper_boat'
    explanation = data['recall_explanation']
    assert '同类虚拟对象' in explanation
    assert '不证明' in explanation and '共同制作' in explanation
    message = companion_context_message(data)
    assert explanation in message.content
    assert '用户称呼' in message.content and '我们' in message.content
    assert '不是现实经历或共同制作证据' in message.content
    assert life[1].read() == before


def test_multiple_object_types_are_not_mislabeled_same_type_or_shared_making(life):
    choose(life, '叫棉花糖号吧')
    before = life[1].read()
    data = read(life, '我们的小船和我们的云朵叫什么？')
    assert data['recall_status'] == 'ambiguous_activity'
    assert 'requested_virtual_object' not in data
    assert data['actual_user_participation'] is None
    assert data['virtual_activity'] is None
    explanation = data['recall_explanation']
    assert '多个对象类型' in explanation and '同类' not in explanation
    assert '不证明' in explanation and '共同制作' in explanation
    assert '棉花糖号' not in companion_context_message(data).content
    assert life[1].read() == before
