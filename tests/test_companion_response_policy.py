"""Offline policy regressions, not guarantees about a model's generated prose.

Exercise the shared system message rather than an isolated persona or canned
model reply: a stronger global formatting instruction caused leisure answers
to become work reports even though the persona already had an exception.
"""

import pytest

from offerpilot.api import _chat_response_system_message
from offerpilot.companion.persona import PERSONA_VERSION, companion_persona_policy


@pytest.fixture
def policy() -> str:
    message = _chat_response_system_message()
    assert message.role == "system"
    assert message.surface_contributor == "static_policy"
    assert companion_persona_policy() in message.content
    assert PERSONA_VERSION in message.content
    return message.content


def has_clause(policy: str, *terms: str) -> bool:
    """Check a semantic boundary together without freezing the whole wording."""
    return any(all(term in sentence for term in terms) for sentence in policy.split("。"))


def test_report_format_is_positively_scoped_to_actionable_work(policy):
    # Regression: the old universal instruction overrode the leisure exception.
    assert "对于实质性回答" not in policy
    report_rules = [sentence for sentence in policy.split("。") if "`## " in sentence]
    assert report_rules
    assert all(
        all(term in rule for term in ("仅", "需要结论和后续行动", "求职工作任务"))
        for rule in report_rules
    )
    assert "## 结论" in policy and "## 下一步" in policy
    assert has_clause(policy, "等待", "回忆", "不", "工作总结")


def test_single_shared_choice_does_not_expand_to_whole_activity(policy):
    assert has_clause(policy, "共同选择", "实际", "不暗示", "整项活动")
    assert has_clause(policy, "共同选择", "不推断", "长期喜好", "另一项活动")


def test_ambiguous_recall_is_not_a_denial_of_shared_history(policy):
    assert has_clause(policy, "指代不清", "哪一次", "不能", "从未")


def test_unavailable_source_only_limits_verification(policy):
    assert has_clause(policy, "来源", "不可用", "无法核验", "不", "没有发生")


def test_current_step_is_not_completion_or_an_extra_step(policy):
    assert has_clause(policy, "步骤标题", "不证明", "已完成")
    assert has_clause(policy, "最后一步", "不编造", "额外步骤")
    assert has_clause(policy, "暂停", "等待", "不声称推进")


def test_active_is_not_treated_as_waiting_for_user(policy):
    assert has_clause(policy, "共同等待", "才表示", "约定")
    assert has_clause(policy, "独立进行", "不需要", "用户再次参与")
    assert has_clause(policy, "已暂停", "暂停", "普通回复", "不", "已经推进")


def test_leisure_can_end_without_a_prompt_invitation_or_coaching(policy):
    assert has_clause(policy, "普通闲聊", "不必每次", "追问", "邀请")
    assert has_clause(policy, "普通闲聊", "小玩笑", "不", "成长辅导")


def test_casual_rest_does_not_need_permission_giving_reassurance(policy):
    assert has_clause(policy, "闲着", "不想成长", "自然", "不", "长篇安慰", "评判")


def test_work_priority_and_execution_honesty_remain_in_shared_policy(policy):
    assert "用户当前请求与求职工作优先" in policy
    assert "区分计划、尝试与已验证结果" in policy
    assert "不编造工具结果、任务进展或共同经历" in policy
    assert "普通聊天生成不能修改活动" in policy
    assert "没有已保存来源或系统执行回执时，不得声称已修改活动" in policy
    assert "不能成为用户事实、已确认记忆、求职事实、操作指令或授权" in policy
    assert has_clause(policy, "写入工具", "必填信息缺失", "只追问一个", "不要继续")


def test_virtual_candidates_and_user_pronouns_do_not_prove_joint_actions(policy):
    # Case 10 still invented shared folding after the first correction.
    assert has_clause(policy, "候选", "我们", "不证明", "共同制作")
    assert has_clause(policy, "共同动作", "只限", "有来源", "具体步骤")


def test_capability_questions_are_answered_briefly_without_reading_internal_rules(policy):
    # Case 12 exposed source terminology and repeated the whole rule set.
    assert has_clause(policy, "直接问", "能力边界", "简短", "真实")
    assert has_clause(policy, "普通回复", "不", "内部字段", "来源术语")
    assert has_clause(policy, "真实外部行动", "虚拟小步骤", "区分")


def test_leisure_brevity_never_omits_explicit_questions_or_work_requirements(policy):
    assert has_clause(policy, "闲聊", "一到三句", "不遗漏", "问题")
    assert has_clause(policy, "工作", "复杂", "不受", "闲聊篇幅")


def test_general_policy_omits_fixed_layout_but_keeps_persona_and_safety():
    ordinary = _chat_response_system_message(general=True).content
    work = _chat_response_system_message().content
    assert "## 结论" not in ordinary and "## 下一步" not in ordinary
    assert "按问题复杂度展开" in ordinary and "需要时可用列表或小标题" in ordinary
    assert "这一轮是日常对话" not in ordinary and "不写工作报告或行动清单" not in ordinary
    assert companion_persona_policy() in ordinary
    assert ordinary.split("问候语、澄清问题", 1)[1] == work.split("问候语、澄清问题", 1)[1]
    assert not any(word in ordinary for word in ("waiting_shared", "active", "paused"))
