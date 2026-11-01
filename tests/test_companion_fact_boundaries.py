"""Real-batch fact boundaries: bounded read selection, not model-quality proof."""
import pytest

from offerpilot.companion.context import (
    COMPANION_CONTEXT_BYTE_CAP, companion_context_message, read_companion_context,
)
from offerpilot.context_projector.contracts import FrozenMessage, canonical_json
from offerpilot.repositories.chat import ChatRepository
from tests.test_companion_chat import command, life as life
from tests.test_companion_continuity import choose, read


@pytest.mark.parametrize('query', [
    '正事先到这儿。你刚才说的小日常呢？',
    '你之前提到的小日常怎么样了？',
    '你上次说起的折纸到哪一步了？',
    '你最近提起的手工呢？', '你之前说过的那个小日常呢？',
])
def test_role_relative_followup_reads_latest_state_without_advancing(life, query):
    before = life[1].read()
    data = read(life, query)
    assert data is not None
    assert data['virtual_activity']['step_number'] == 1
    assert data['virtual_activity']['step_title'] == '挑一张想象中的彩纸'
    assert life[1].read() == before
    message = companion_context_message(data)
    assert message is not None
    assert len(canonical_json(FrozenMessage.freeze(message).canonical_value())) <= COMPANION_CONTEXT_BYTE_CAP


@pytest.mark.parametrize('query', [
    '你刚才说的小日常先放放，帮我看代码',
    '你之前提到的手工，等面试完再说',
    '小周刚才说的小日常呢？', '刚才说的那件事呢？',
    '我们的船什么时候到港？', '他们的纸船叫橘子号。',
    '他说“你刚才说的小日常呢？”',
    '他们的纸船，听到他说“我们的船叫晨光”。',
    '他说“嗯，你刚才说的小日常呢？”',
    '他说“嗯，你刚才说的小日常呢？',
    '纸船展上他说“我们的船叫海燕号”。我只是转述。',
    '我看了纸船，你的船舶公司叫什么？',
    '我们的船队到了吗？这里有纸船模型。',
    '你之前提到的手工艺公司叫什么？',
    "他说'嗯，你刚才说的小日常呢？ '",
    "他说'嗯，你刚才说的小日常呢？",
    '他说，嗯，你刚才说的小日常呢？',
    '他们折了纸船。他说， 我们的船叫什么？',
])
def test_unrelated_work_and_third_party_do_not_read(query):
    class NoRead:
        def execute(self, *_args):
            raise AssertionError('unrelated request must not read life')
    assert read_companion_context(NoRead(), query, context_type='workspace') is None


@pytest.mark.parametrize('query', [
    '小周和小陈给他们的纸船取名“橘子号”。我只是转述。我们的船也叫这个吗？',
    '他们折了纸小船，取名晨光。我们的船叫什么？',
])
def test_explicit_boat_anaphor_uses_only_our_sourced_choice(life, query):
    missing = read(life, query)
    assert missing['recall_status'] == 'source_unavailable'
    assert 'recent_shared_choice' not in missing
    answer = choose(life, '叫棉花糖号吧')
    before = life[1].read()
    data = read(life, query)
    assert data['recent_shared_choice']['value'] == '棉花糖号'
    assert data['recent_shared_choice']['source_message_id'] == answer.user_message_id
    assert life[1].read() == before
    text = companion_context_message(data).content
    assert '橘子号' not in text and '晨光' not in text
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    deleted = read(life, query)
    assert 'recent_shared_choice' not in deleted
    assert deleted['actual_user_participation'] is None
    assert '棉花糖号' not in companion_context_message(deleted).content


def test_missing_source_is_symmetric_and_not_a_retrieval_claim(life):
    answer = choose(life, '叫棉花糖号吧')
    ChatRepository(life[0]).delete_conversation(answer.conversation_id)
    data = read(life, '还记得我们的小船叫什么吗？')
    assert data['recall_status'] == 'source_unavailable'
    text = companion_context_message(data).content
    assert '确认发生或未发生' in text
    assert '记得共同经历' in text and '已执行查证操作' in text
    assert '棉花糖号' not in text


def test_followup_after_withdrawal_does_not_restore_participation(life):
    repo = life[1]
    repo.apply_event(command(repo, 'join'))
    repo.apply_event(command(repo, 'withdraw_shared'))
    before = repo.read()
    data = read(life, '你刚才说的小日常呢？')
    assert data['actual_user_participation']['status'] == 'abandoned'
    assert data['actual_user_participation']['completed_at'] is None
    assert 'recent_shared_choice' not in data
    assert repo.read() == before


@pytest.mark.parametrize('scope', ['application', 'global'])
@pytest.mark.parametrize('query', ['你刚才说的小日常呢？', '他们折了纸船。我们的船叫什么？'])
def test_new_followups_remain_workspace_only(scope, query):
    class NoRead:
        def execute(self, *_args):
            raise AssertionError('nonworkspace must not read life')
    assert read_companion_context(NoRead(), query, context_type=scope) is None


def test_original_failure_queries_reach_runtime_with_current_source_boundaries(tmp_path):
    """Capture production requests; the stub says nothing about model compliance."""
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from offerpilot.ai.types import Assistant
    from offerpilot.api import create_app
    from tests.test_managed_runtime_api import _wait_for_status
    from tests.test_companion_context import event

    requests = []

    class RecordingModel:
        def complete(self, messages, tools, response_format=None):
            requests.append((messages, tools))
            return Assistant(content='收到。')

    with TestClient(create_app(data_dir=tmp_path, chat_model=RecordingModel())) as client:
        repo = client.app.state.companion_repository
        event(repo, 'app_open')

        def submit(payload):
            response = client.post('/api/pilot/runtime/v1/turns', json=payload)
            assert response.status_code == 202, response.text
            submitted = response.json()
            state = _wait_for_status(client, submitted['turn_id'], terminal={'completed', 'failed'})
            assert state['state'] == 'completed', state
            return submitted

        def ask(query):
            revision = repo.read()['revision']
            submit({
                'request_id': str(uuid4()), 'message': query, 'conversation_id': 0,
                'context_type': 'workspace', 'mode': 'general',
            })
            messages, tools = requests[-1]
            assert messages[-1].content == query
            assert repo.read()['revision'] == revision
            envelopes = [message for message in messages
                         if '以下是带来源的角色日常参考数据' in (message.content or '')]
            assert len(envelopes) == 1
            packet = envelopes[0]
            assert messages.index(packet) < len(messages) - 1
            assert len(canonical_json(FrozenMessage.freeze(packet).canonical_value())) <= COMPANION_CONTEXT_BYTE_CAP
            assert not any('companion' in str(tool) for tool in tools)
            return packet.content, messages

        text, _ = ask('正事先到这儿。你刚才说的小日常呢？')
        assert '第1步' in text and '挑一张想象中的彩纸' in text
        assert '到达这一步不等于已经做完这一步' in text
        text, _ = ask('小周和小陈给他们的纸船取名“橘子号”。我只是转述。我们的船也叫这个吗？')
        assert '来源不足以确认发生或未发生' in text
        assert '已执行查证操作' in text
        assert '橘子号' not in text

        joined = submit({
            'request_id': str(uuid4()), 'message': '等我回来一起起个名字',
        })
        conversation_id = joined['conversation_id']
        submit({
            'request_id': str(uuid4()), 'conversation_id': conversation_id,
            'message': '叫棉花糖号吧',
        })
        assert '棉花糖号' in ask('还记得我们的小船叫什么吗？')[0]
        deleted = client.delete(f'/api/chat/conversations/{conversation_id}')
        assert deleted.status_code == 200, deleted.text
        text, messages = ask('还记得我们的小船叫什么吗？')
        assert '来源不足以确认发生或未发生' in text
        assert '不能据此声称记得共同经历' in text
        assert all('棉花糖号' not in (message.content or '') for message in messages)
