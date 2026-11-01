"""Input-domain regressions: no generated-output filtering or model-quality claims."""
from types import SimpleNamespace, MappingProxyType
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant, Message
from offerpilot.api import create_app
from offerpilot.pilot_runtime.composition import _ContextAdapter
from offerpilot.pilot_runtime.contracts import (
    AttachmentReference, ConfirmationRequest, PilotActionDescriptor, StartTurnRequest,
)


class Persistence:
    def __init__(self, clarification=None):
        self.clarification = clarification

    def get_pending_clarification(self, conversation_id):
        return self.clarification


def assemble(*, source=None, request=None, conversation=None, clarification=None):
    return _ContextAdapter(
        Persistence(clarification),
        system_message=lambda: Message(role="system", content="work"),
        general_system_message=lambda: Message(role="system", content="general"),
        clarification_message=lambda *_: None, page_messages=lambda _: (),
    ).assemble(
        source or SimpleNamespace(
            history=(), context_type="workspace", mode="general",
            context_message=Message(role="user", content="角色事实", surface_contributor="current_scope",
                                    surface_revision="companion:identity:1:source-hash")),
        conversation or SimpleNamespace(id=1, pending_tool_call_id=""),
        request or StartTurnRequest(message="你的小日常怎么样？"),
    )


def test_known_companion_scope_uses_dialogue_without_changing_source_or_history():
    history = Message(role="assistant", content="历史工作回复：## 结论\n原话保留")
    source = SimpleNamespace(history=(history,), context_type="workspace", mode="general",
        context_message=Message(role="user", content="角色事实", surface_contributor="current_scope",
                                surface_revision="companion:identity:1:source-hash"))
    result = assemble(source=source)
    assert result[0].content == "general"
    assert result[1] is source.context_message and result[2] is history
    assert result[-1].content == "你的小日常怎么样？"


@pytest.mark.parametrize("changes", [
    {"context_type": "application"}, {"mode": "mock_interview"}, {"mode": "nego_coach"},
])
def test_explicit_non_general_source_retains_work_policy(changes):
    source = SimpleNamespace(history=(), context_type="workspace", mode="general", context_message=None)
    source.__dict__.update(changes)
    assert assemble(source=source)[0].content == "work"


@pytest.mark.parametrize("turn_request", [
    ConfirmationRequest(conversation_id=1, approved=True),
    ConfirmationRequest(conversation_id=1, approved=False),
    StartTurnRequest(message="好的", pilot_action=PilotActionDescriptor("explicit_task")),
])
def test_confirmation_and_typed_action_keep_work_policy(turn_request):
    assert assemble(request=turn_request)[0].content == "work"


def test_page_attachment_and_history_are_evidence_not_presentation_intent():
    attachment = Message(role="user", content="虚构简历原文", surface_contributor="attachments")
    history = Message(role="assistant", content="## 结论\n旧工作回答。")
    source = SimpleNamespace(history=(history,), context_type="workspace", mode="general",
                             context_message=None, attachment_messages=(attachment,))
    result = assemble(source=source, request=StartTurnRequest(message="先不谈简历，今天有点累。",
        attachments=(AttachmentReference("resume", "1"),),
        page_context=MappingProxyType({"view": "dashboard", "label": "今日"})))
    assert result[0].content == "general"
    assert attachment in result and history in result
    assert result[-1].content == "先不谈简历，今天有点累。"


def test_pending_approval_and_persisted_clarification_keep_work_policy():
    assert assemble(conversation=SimpleNamespace(id=1, pending_tool_call_id="pending"))[0].content == "work"
    pending = SimpleNamespace(pending=SimpleNamespace(tool_call_id="pending", tool_name="write",
        args="{}", human="", operation_id="op"), question="Which role?")
    assert assemble(clarification=pending)[0].content == "work"


@pytest.mark.parametrize("text", [
    "早呀，今天阳光不错。", "Morning. The sky looks like crumpled tissue.", "嗯，刚睡醒。",
    "你好，帮我改简历", "引用：‘你好’", "继续", "不要暂停工作", "面试像过山车。",
    "请按结论和下一步列出这份分析。", "帮我看看这个", "先放空一会，再分析这份简历。",
])
def test_unknown_mixed_quoted_and_format_requests_share_neutral_default(text):
    source = SimpleNamespace(history=(), context_type="workspace", mode="general", context_message=None)
    assert assemble(source=source, request=StartTurnRequest(message=text))[0].content == "general"


class RecordingModel:
    def __init__(self):
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((list(messages), list(tools)))
        # Deliberately leave work-looking output untouched: this is not a filter.
        return Assistant(content="## 结论\n合成原文。")


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("page_context", [None, {"view": "dashboard", "label": "今日"},
                                          {"view": "settings", "label": "设置"}])
def test_actual_api_selects_policy_per_turn_without_rewriting_reply(tmp_path, endpoint, page_context):
    model = RecordingModel()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        conversation = 0
        for text in ("早呀，今天阳光不错。", "你的小日常怎么样？",
                     "你的小日常先放放，帮我检查这条简历的事实缺口，不要调用工具。",
                     "刚才的事先放下，你的小日常现在怎么样？"):
            extra = {"page_context": page_context} if page_context else {}
            response = client.post(endpoint, json={"request_id": str(uuid4()), "message": text,
                                                   "conversation_id": conversation, **extra})
            assert response.status_code == 200, response.text
            messages, _ = model.requests[-1]
            policy = messages[0].content
            assert "## 下一步" not in policy and "## 结论" not in policy
            assert "这一轮是日常对话" not in policy and "不写工作报告或行动清单" not in policy
            if page_context:
                assert any('"view":"' + page_context["view"] + '"' in m.content for m in messages)
            if "简历的事实缺口" in text:
                assert not any(m.content.startswith("以下是带来源的角色日常参考数据") for m in messages)
            if conversation:
                assert any(m.role == "assistant" and m.content == "## 结论\n合成原文。" for m in messages)
            assert "用户当前请求与求职工作优先" in policy
            assert messages[-1].content == text
            # API's canonical persisted content proves no output string deletion.
            rows = client.get("/api/chat/conversations").json()
            conversation = rows[0]["id"]
            saved = client.get(f"/api/chat/conversations/{conversation}").json()
            assert saved[-1]["content"] == "## 结论\n合成原文。"
