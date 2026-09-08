import json

import pytest

from offerpilot.ai.deterministic_actions import (
    build_pilot_pending_action,
    decide_pilot_action,
    is_pilot_cancel_message,
    match_application_jd_command,
    parse_pilot_action,
)


def _text_with_exact_utf8_bytes(size: int, unit: str) -> str:
    unit_bytes = unit.encode("utf-8")
    repetitions, remainder = divmod(size, len(unit_bytes))
    return unit * repetitions + "a" * remainder


def test_parse_public_action_preserves_jd_text_and_does_not_fetch_url():
    action = parse_pilot_action(
        {
            "type": "application_jd_save",
            "jdText": "  负责后端服务\n😀  ",
            "sourceUrl": "https://jobs.example.test/backend",
        }
    )

    assert action.jd_text == "  负责后端服务\n😀  "
    assert action.source_url == "https://jobs.example.test/backend"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "other"},
        {"type": "application_jd_save", "jdText": 3},
        {"type": "application_jd_save", "jdText": "JD", "application_id": 7},
        {"type": "application_jd_save", "jdText": "JD", "sourceUrl": 3},
        "not-an-object",
    ],
)
def test_parse_public_action_rejects_invalid_shape_without_side_effects(payload):
    with pytest.raises(ValueError):
        parse_pilot_action(payload)


@pytest.mark.parametrize("unit", ["a", "中", "😀"])
@pytest.mark.parametrize(
    ("size", "accepted"),
    [
        (59_999, True),
        (60_000, True),
        (60_001, False),
        (60 * 1024 - 1, False),
        (60 * 1024, False),
        (60 * 1024 + 1, False),
    ],
)
def test_parse_public_action_uses_utf8_byte_limit(size, accepted, unit):
    payload = {
        "type": "application_jd_save",
        "jdText": _text_with_exact_utf8_bytes(size, unit),
        "sourceUrl": None,
    }

    if accepted:
        assert parse_pilot_action(payload).jd_text == payload["jdText"]
    else:
        with pytest.raises(ValueError, match="too large"):
            parse_pilot_action(payload)


@pytest.mark.parametrize("payload", [{"type": "application_jd_save"}, {"type": "application_jd_save", "jdText": "  "}])
def test_parse_public_action_without_jd_is_valid_for_clarification(payload):
    action = parse_pilot_action(payload)

    assert action.jd_text is None
    assert action.source_url is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("保存 JD：职位：后端工程师\n负责 API 设计", "职位：后端工程师\n负责 API 设计"),
        ("更新当前岗位描述:熟悉 Python 和 SQL", "熟悉 Python 和 SQL"),
        ("给当前投递补充岗位资料\n需要轮班支持", "需要轮班支持"),
        ("保存 JD", None),
        ("不要保存 JD", None),
        ("如何保存 JD？", None),
        ("查看 JD", None),
        ("保存 JD 需要注意什么", None),
    ],
)
def test_match_application_jd_command_is_strict_and_preserves_body(message, expected):
    assert match_application_jd_command(message) == expected


@pytest.mark.parametrize("message", ["取消", " 取消 ", "算了", "先不用", "不用了", "不保存", "不要保存"])
def test_cancel_message_requires_exact_trimmed_allowlist(message):
    assert is_pilot_cancel_message(message) is True


@pytest.mark.parametrize("message", ["取消本次", "不要保存 JD", "先不用了", "不用保存"])
def test_cancel_message_does_not_use_prefix_or_substring_matching(message):
    assert is_pilot_cancel_message(message) is False


def test_decide_pilot_action_routes_command_without_current_jd_to_collection():
    decision = decide_pilot_action("保存 JD", has_current_jd=False)

    assert decision.kind == "collecting_jd"
    assert decision.question == "请粘贴完整岗位描述"


def test_decide_pilot_action_turns_collected_text_into_confirmation():
    decision = decide_pilot_action(
        "职位：数据工程师\n负责数据平台",
        has_current_jd=False,
        collecting_jd=True,
    )

    assert decision.kind == "pending_confirmation"
    assert decision.jd_text == "职位：数据工程师\n负责数据平台"


def test_decide_pilot_action_routes_non_command_to_normal_agent():
    decision = decide_pilot_action("请总结一下这份 JD", has_current_jd=True)

    assert decision.kind == "normal_agent"


def test_build_pilot_pending_action_controls_server_fields_and_factories():
    pending = build_pilot_pending_action(
        application_id=7,
        current_version_id=3,
        jd_text="职位：后端工程师",
        source_url=None,
        id_factory=lambda: "call-deterministic-jd-1",
        key_factory=lambda: "key-deterministic-jd-1",
    )

    assert pending.tool_name == "save_application_jd_version"
    assert pending.tool_call_id == "call-deterministic-jd-1"
    assert json.loads(pending.args) == {
        "application_id": 7,
        "jd_text": "职位：后端工程师",
        "source_url": None,
        "expected_current_version_id": 3,
        "idempotency_key": "key-deterministic-jd-1",
    }
