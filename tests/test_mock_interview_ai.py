import json

import pytest

from offerpilot.ai.mock_interview import (
    MOCK_INTERVIEW_FEEDBACK_SCHEMA,
    SAFE_EMPTY_FEEDBACK,
    MockInterviewContractError,
    MockInterviewUnverifiableError,
    _provider_evidence_catalog,
    build_mock_interview_evidence_catalog,
    generate_feedback,
    generate_question,
    parse_mock_interview_json,
    should_retry_mock_interview_format,
    validate_feedback,
)
from offerpilot.ai.types import Assistant


def _snapshot():
    return {
        "jd": {"text": "需要 Python"},
        "resume": {"content_json": {"skills": ["Python"]}},
    }


def _turns():
    return [{"turn_no": 1, "question": "介绍项目", "answer": "我做过 Python 服务"}]


def _catalog():
    return _provider_evidence_catalog(_snapshot(), _turns())


class _QuestionRepairModel:
    supports_json_schema = False

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0
        self.messages = []

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        return Assistant(content=next(self.outputs))


class _SchemaCaptureModel:
    supports_json_schema = True

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0
        self.response_formats = []

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        self.response_formats.append(kwargs.get("response_format"))
        return Assistant(content=next(self.outputs))


class _ProviderBlockModel:
    supports_json_schema = False

    def __init__(self, content, request_id):
        self.content = content
        self.request_id = request_id
        self.messages = []

    def complete(self, messages, tools, **kwargs):
        self.messages.append(messages)
        return Assistant(content=self.content, provider_blocks={"request_id": self.request_id})


def _valid_question():
    return (
        '{"question":"请分享一次经历？",'
        '"evidence_ids":["ev_003"]}'
    )


def test_structural_evidence_error_is_repaired_once():
    model = _QuestionRepairModel([
        '{"question":"请分享一次经历？","evidence_ids":[null]}',
        _valid_question(),
    ])

    question, diagnostic = generate_question(model, _snapshot(), _turns())

    assert question == {
        "question": "请分享一次经历？",
        "evidence_refs": [{
            "source": "turn",
            "path": "/turns/001/answer",
            "excerpt": "我做过 Python 服务",
        }],
    }
    assert diagnostic["repair_count"] == 1
    assert model.calls == 2
    repair_prompt = model.messages[1][0].content
    assert "evidence_id_not_string" in repair_prompt
    assert "evidence_ids" in repair_prompt
    assert "raw model output" not in repair_prompt


def test_repeated_structural_evidence_error_is_terminal():
    model = _QuestionRepairModel([
        '{"question":"Q","evidence_ids":[null]}',
        '{"question":"Q2","evidence_ids":[null]}',
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "evidence_id_not_string"
    assert error.value.diagnostic["failure_categories"] == [
        "evidence_id_not_string", "evidence_id_not_string"
    ]
    assert model.calls == 2


def test_semantic_evidence_failures_never_enter_format_repair():
    assert not should_retry_mock_interview_format("unknown_evidence_ref")
    assert not should_retry_mock_interview_format("duplicate_evidence_ref")
    assert not should_retry_mock_interview_format("limit_exceeded")
    assert not should_retry_mock_interview_format("missing_evidence_ref")


def test_repaired_shape_is_revalidated_for_forged_reference():
    model = _QuestionRepairModel([
        '{"question":"Q","evidence_ids":[null]}',
        '{"question":"Q2","evidence_ids":["ev_999"]}',
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "unknown_evidence_ref"
    assert model.calls == 2


def test_feedback_structural_evidence_error_is_repaired_once():
    invalid = _feedback(strengths=[{
        "id": "s1",
        "text": "回答引用了实际项目",
        "evidence_refs": [{"source": "turn", "path": "/turns/001/answer"}],
    }])
    model = _QuestionRepairModel([json.dumps(invalid, ensure_ascii=False), json.dumps(_feedback(), ensure_ascii=False)])

    proposal, diagnostic = generate_feedback(model, _snapshot(), _turns())

    assert proposal == _persisted_feedback()
    assert diagnostic["repair_count"] == 1
    assert model.calls == 2


def test_feedback_blank_value_is_repaired_once():
    blank = _feedback(strengths=[{
        "id": "strength-1",
        "text": "",
        "evidence_ids": ["ev_003"],
    }])
    model = _QuestionRepairModel([
        json.dumps(blank, ensure_ascii=False),
        json.dumps(_feedback(), ensure_ascii=False),
    ])

    proposal, diagnostic = generate_feedback(model, _snapshot(), _turns())

    assert proposal == _persisted_feedback()
    assert diagnostic["repair_attempted"] is True
    assert diagnostic["repair_count"] == 1
    assert model.calls == 2
    assert "blank_value" in "\n".join(
        message.content for message in model.messages[1]
    )


def test_feedback_repeated_blank_value_is_terminal_after_one_repair():
    blank = _feedback(strengths=[{
        "id": "strength-1",
        "text": "",
        "evidence_ids": ["ev_003"],
    }])
    model = _QuestionRepairModel([
        json.dumps(blank, ensure_ascii=False),
        json.dumps(blank, ensure_ascii=False),
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == "blank_value"
    assert error.value.diagnostic["failure_categories"] == [
        "blank_value",
        "blank_value",
    ]
    assert model.calls == 2


def test_feedback_text_prompt_declares_complete_contract():
    model = _QuestionRepairModel([json.dumps(_feedback(), ensure_ascii=False)])

    generate_feedback(model, _snapshot(), _turns())

    prompt = model.messages[0][0].content
    for field in ("schema_version", "proposal_status", "strengths", "practice_points", "follow_up_questions", "next_practice_steps"):
        assert field in prompt
    assert "evidence_ids" in prompt
    assert "evidence_catalog" in prompt
    assert "safe_empty" in prompt
    assert "normal" in prompt


def test_feedback_prompt_requires_turn_evidence_for_observed_fields():
    model = _QuestionRepairModel([json.dumps(_feedback(), ensure_ascii=False)])

    generate_feedback(model, _snapshot(), _turns())

    prompt = model.messages[0][0].content
    for field in ("strengths", "practice_points", "next_practice_steps"):
        assert field in prompt
    assert 'source="turn"' in prompt
    assert "at least one completed-turn evidence reference" in prompt
    assert "safe_empty" in prompt


def test_feedback_native_schema_declares_complete_contract():
    model = _SchemaCaptureModel([json.dumps(_feedback(), ensure_ascii=False)])

    generate_feedback(model, _snapshot(), _turns())

    response_format = model.response_formats[0]
    assert response_format["json_schema"]["schema"] == MOCK_INTERVIEW_FEEDBACK_SCHEMA
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version", "proposal_status", "strengths", "practice_points",
        "follow_up_questions", "next_practice_steps",
    }
    item_schema = schema["properties"]["strengths"]["items"]
    assert item_schema["additionalProperties"] is False
    assert set(item_schema["required"]) == {"id", "text", "evidence_ids"}
    id_schema = item_schema["properties"]["evidence_ids"]
    assert id_schema["type"] == "array"
    assert id_schema["maxItems"] == 4


def test_feedback_provider_payload_exposes_id_catalog_only():
    model = _QuestionRepairModel([json.dumps(_feedback(), ensure_ascii=False)])

    generate_feedback(model, _snapshot(), _turns())

    payload = json.loads(model.messages[0][1].content)
    assert [entry["id"] for entry in payload["evidence_catalog"]] == [
        "ev_001", "ev_002", "ev_003"
    ]
    assert "snapshot" not in payload


def test_feedback_repeated_structural_failure_is_terminal():
    invalid = json.dumps({**_feedback(), "extra": "raw model output"}, ensure_ascii=False)
    model = _QuestionRepairModel([invalid, invalid])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == "unexpected_field"
    assert model.calls == 2


def test_missing_turn_evidence_is_repaired_once_when_second_response_adds_turn_ref():
    marker = "model-only-feedback-marker"
    first = _feedback(
        strengths=[{
            "id": "s1",
            "text": marker,
            "evidence_ids": ["ev_001"],
        }]
    )
    model = _QuestionRepairModel([
        json.dumps(first, ensure_ascii=False),
        json.dumps(_feedback(), ensure_ascii=False),
    ])

    proposal, diagnostic = generate_feedback(model, _snapshot(), _turns())

    assert proposal == _persisted_feedback()
    assert diagnostic["repair_count"] == 1
    assert model.calls == 2
    repair_messages = model.messages[1]
    repair_prompt = "\n".join(message.content for message in repair_messages)
    assert "missing_turn_evidence" in repair_prompt
    assert "at least one completed-turn evidence reference" in repair_prompt
    assert marker not in repair_prompt


def test_repaired_missing_turn_evidence_with_forged_turn_reference_fails():
    first = _feedback(
        strengths=[{
            "id": "s1",
            "text": "JD-only claim",
            "evidence_ids": ["ev_001"],
        }]
    )
    second = _feedback(
        strengths=[{
            "id": "s1",
            "text": "forged turn claim",
            "evidence_ids": ["ev_999"],
        }]
    )
    model = _QuestionRepairModel([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == "unknown_evidence_ref"
    assert model.calls == 2


def test_semantic_feedback_reference_failure_is_not_repaired():
    forged = _feedback(
        strengths=[{
            "id": "s1",
            "text": "forged turn claim",
            "evidence_ids": ["ev_999"],
        }]
    )
    model = _QuestionRepairModel([json.dumps(forged, ensure_ascii=False)])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == "unknown_evidence_ref"
    assert model.calls == 1


@pytest.mark.parametrize(
    ("evidence_ids", "category"),
    [
        (["ev_999"], "unknown_evidence_ref"),
        (["ev_003", "ev_003"], "duplicate_evidence_ref"),
        (["ev_001"] * 5, "limit_exceeded"),
    ],
)
def test_invalid_feedback_evidence_id_without_turn_is_not_repaired(evidence_ids, category):
    model = _QuestionRepairModel(
        [
            json.dumps(
                _feedback(
                    strengths=[
                        {
                            "id": "s1",
                            "text": "invalid reference",
                            "evidence_ids": evidence_ids,
                        }
                    ]
                ),
                ensure_ascii=False,
            )
        ]
    )

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == category
    assert model.calls == 1


@pytest.mark.parametrize("evidence_id", [None, 1, {"id": "ev_001"}])
def test_question_evidence_id_shape_failures_have_stable_category(evidence_id):
    model = _QuestionRepairModel([
        json.dumps({"question": "Q", "evidence_ids": [evidence_id]}),
        json.dumps({"question": "Q2", "evidence_ids": [evidence_id]}),
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "evidence_id_not_string"
    assert model.calls == 2


def test_evidence_catalog_uses_stable_escaped_paths_and_completed_turns_only():
    snapshot = {
        "jd": {"text": "需要 Python"},
        "resume": {
            "content_json": {
                "z": "Z",
                "a/b~c": "路径内容",
                "empty": "",
                "nested": ["第一项", {"answer": "第二项 🚀 e\u0301"}],
            }
        },
    }
    turns = [
        {"turn_no": 2, "question": "Q2", "answer": "完成回答"},
        {"turn_no": 3, "question": "Q3", "answer": "   "},
    ]

    catalog = build_mock_interview_evidence_catalog(snapshot, turns)

    assert catalog == [
        {"source": "jd", "path": "/jd/text", "value": "需要 Python"},
        {"source": "resume", "path": "/resume/content_json/a~1b~0c", "value": "路径内容"},
        {"source": "resume", "path": "/resume/content_json/nested/0", "value": "第一项"},
        {"source": "resume", "path": "/resume/content_json/nested/1/answer", "value": "第二项 🚀 e\u0301"},
        {"source": "resume", "path": "/resume/content_json/z", "value": "Z"},
        {"source": "turn", "path": "/turns/002/answer", "value": "完成回答"},
    ]


def test_question_provider_uses_opaque_evidence_ids_and_server_expands_exact_reference():
    model = _SchemaCaptureModel([
        '{"question":"请说明你如何验证 Python 服务？","evidence_ids":["ev_003"]}'
    ])

    question, _ = generate_question(model, _snapshot(), _turns())

    assert question == {
        "question": "请说明你如何验证 Python 服务？",
        "evidence_refs": [{
            "source": "turn",
            "path": "/turns/001/answer",
            "excerpt": "我做过 Python 服务",
        }],
    }
    schema = model.response_formats[0]["json_schema"]["schema"]
    assert set(schema["required"]) == {"question", "evidence_ids"}
    assert schema["properties"]["evidence_ids"]["items"]["enum"] == [
        "ev_001", "ev_002", "ev_003"
    ]


def test_question_provider_payload_exposes_stable_ids_without_requiring_excerpt_copying():
    model = _QuestionRepairModel([
        '{"question":"请说明 Python 项目的验证方式？","evidence_ids":["ev_003"]}'
    ])

    generate_question(model, _snapshot(), _turns())

    payload = json.loads(model.messages[0][1].content)
    assert payload["evidence_catalog"] == [
        {"id": "ev_001", "source": "jd", "path": "/jd/text", "value": "需要 Python"},
        {"id": "ev_002", "source": "resume", "path": "/resume/content_json/skills/0", "value": "Python"},
        {"id": "ev_003", "source": "turn", "path": "/turns/001/answer", "value": "我做过 Python 服务"},
    ]
    assert "excerpt" not in model.messages[0][0].content


def test_question_provider_unknown_evidence_id_is_terminal_without_format_repair():
    model = _QuestionRepairModel([
        '{"question":"请说明项目？","evidence_ids":["ev_999"]}'
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "unknown_evidence_ref"
    assert model.calls == 1


def test_follow_up_rejects_a_normalized_duplicate_question_without_repair():
    model = _QuestionRepairModel([
        '{"question":"  介绍项目  ","evidence_ids":["ev_003"]}'
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "duplicate_question"
    assert model.calls == 1


def test_follow_up_requires_evidence_from_the_latest_answer_without_repair():
    model = _QuestionRepairModel([
        '{"question":"你如何验证 Python 服务？","evidence_ids":["ev_001"]}'
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.category == "missing_latest_turn_evidence"
    assert model.calls == 1


def test_follow_up_prompt_and_result_are_grounded_in_the_latest_answer():
    model = _QuestionRepairModel([
        '{"question":"你刚才提到 Python 服务，如何处理超时与部分失败？","evidence_ids":["ev_003"]}'
    ])

    result, _ = generate_question(model, _snapshot(), _turns())

    assert result["question"] == "你刚才提到 Python 服务，如何处理超时与部分失败？"
    prompt = model.messages[0][0].content
    assert "latest answered turn is 1" in prompt
    assert "ev_003" in prompt
    assert "must not repeat or paraphrase any previous question" in prompt


def test_opening_question_does_not_require_turn_evidence():
    model = _QuestionRepairModel([
        '{"question":"请介绍最相关的 Python 项目。","evidence_ids":["ev_001"]}'
    ])

    result, _ = generate_question(model, _snapshot(), [])

    assert result["evidence_refs"] == [{
        "source": "jd",
        "path": "/jd/text",
        "excerpt": "需要 Python",
    }]


def test_provider_payload_contains_request_scoped_catalog_without_domain_ids_or_unfinished_turn():
    model = _QuestionRepairModel([_valid_question()])
    snapshot = {
        **_snapshot(),
        "application": {"id": 42, "company_name": "secret"},
    }
    turns = [
        {"turn_no": 1, "question": "Q1", "answer": "我做过 Python 服务"},
        {"turn_no": 2, "question": "Q2", "answer": ""},
    ]

    generate_question(model, snapshot, turns)

    payload = json.loads(model.messages[0][1].content)
    assert payload["evidence_catalog"][-1] == {
        "id": "ev_003", "source": "turn", "path": "/turns/001/answer", "value": "我做过 Python 服务"
    }
    assert [entry["id"] for entry in payload["evidence_catalog"]] == ["ev_001", "ev_002", "ev_003"]
    assert "secret" not in json.dumps(payload["evidence_catalog"], ensure_ascii=False)
    assert "Q2" not in json.dumps(payload["evidence_catalog"], ensure_ascii=False)


def test_contract_failure_redacts_provider_request_id():
    model = _ProviderBlockModel('{"unexpected":true}', "provider-request-123")

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_question(model, _snapshot(), _turns())

    assert error.value.diagnostic["provider_request_id"] == "request-redacted-488ab4c1c10b"
    assert "provider-request-123" not in str(error.value.diagnostic)


def test_feedback_contract_failure_redacts_provider_request_id():
    model = _ProviderBlockModel('{"unexpected":true}', "provider-request-123")

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.diagnostic["provider_request_id"] == "request-redacted-488ab4c1c10b"
    assert "provider-request-123" not in str(error.value.diagnostic)


def _feedback(**overrides):
    value = {
        "schema_version": "mock-interview-feedback-v1",
        "proposal_status": "normal",
        "strengths": [
            {
                "id": "s1",
                "text": "回答引用了实际项目",
                "evidence_ids": ["ev_003"],
            }
        ],
        "practice_points": [],
        "follow_up_questions": [],
        "next_practice_steps": [],
    }
    value.update(overrides)
    return value


def _persisted_feedback(**overrides):
    value = {
        "schema_version": "mock-interview-feedback-v1",
        "proposal_status": "normal",
        "strengths": [
            {
                "id": "s1",
                "text": "回答引用了实际项目",
                "evidence_refs": [{
                    "source": "turn",
                    "path": "/turns/001/answer",
                    "excerpt": "我做过 Python 服务",
                }],
            }
        ],
        "practice_points": [],
        "follow_up_questions": [],
        "next_practice_steps": [],
    }
    value.update(overrides)
    return value


def test_question_contract_rejects_duplicate_keys_and_fenced_json():
    with pytest.raises(MockInterviewContractError, match="duplicate_key"):
        parse_mock_interview_json('{"text":"a","text":"b"}')
    with pytest.raises(MockInterviewContractError, match="invalid_json"):
        parse_mock_interview_json("```json\n{}\n```")


def test_feedback_contract_rejects_nonfinite_extra_blank_and_over_limit_values():
    with pytest.raises(MockInterviewContractError, match="invalid_json"):
        parse_mock_interview_json('{"value":NaN}')
    with pytest.raises(MockInterviewContractError, match="unexpected_field"):
        validate_feedback({**_feedback(), "extra": 1}, _catalog())
    with pytest.raises(MockInterviewContractError, match="blank_value"):
        validate_feedback({**_feedback(), "strengths": [{"id": "s1", "text": " ", "evidence_ids": ["ev_003"]}]}, _catalog())
    with pytest.raises(MockInterviewContractError, match="limit_exceeded"):
        validate_feedback({**_feedback(), "practice_points": [_feedback()["strengths"][0]] * 9}, _catalog())


def test_validate_feedback_expands_ids_into_persisted_references():
    persisted = validate_feedback(_feedback(), _catalog())
    assert persisted == _persisted_feedback()


def test_strengths_and_practice_points_require_turn_answer_evidence():
    item = {"id": "s1", "text": "岗位要求 Python", "evidence_ids": ["ev_001"]}
    with pytest.raises(MockInterviewContractError, match="missing_turn_evidence"):
        validate_feedback({**_feedback(), "strengths": [item]}, _catalog())


def test_follow_up_fixed_question_requires_versioned_id_and_exact_text():
    fixed = {"id": "free", "text": "您希望进一步澄清哪一部分？", "evidence_ids": []}
    with pytest.raises(MockInterviewContractError, match="fixed_question"):
        validate_feedback({**_feedback(), "follow_up_questions": [fixed]}, _catalog())


def test_follow_up_context_question_requires_evidence():
    item = {"id": "q1", "text": "你在 Python 项目中做了什么？", "evidence_ids": []}
    with pytest.raises(MockInterviewContractError, match="evidence"):
        validate_feedback({**_feedback(), "follow_up_questions": [item]}, _catalog())


def test_next_practice_step_requires_turn_and_optional_source_refs():
    item = {"id": "n1", "text": "复习 Python", "evidence_ids": ["ev_001"]}
    with pytest.raises(MockInterviewContractError, match="missing_turn_evidence"):
        validate_feedback({**_feedback(), "next_practice_steps": [item]}, _catalog())


def test_feedback_evidence_must_resolve_within_the_frozen_catalog():
    with pytest.raises(MockInterviewContractError, match="duplicate_evidence_ref"):
        validate_feedback(
            {**_feedback(), "strengths": [{"id": "s1", "text": "回答引用了实际项目", "evidence_ids": ["ev_003", "ev_003"]}]},
            _catalog(),
        )
    with pytest.raises(MockInterviewContractError, match="unknown_evidence_ref"):
        validate_feedback(
            {**_feedback(), "strengths": [{"id": "s1", "text": "回答引用了实际项目", "evidence_ids": ["ev_004"]}]},
            _catalog(),
        )


def test_feedback_rejects_legacy_copied_reference_shape():
    legacy = {
        "id": "s1",
        "text": "回答引用了实际项目",
        "evidence_refs": [{"source": "turn", "path": "/turns/001/answer", "excerpt": "我做过 Python 服务"}],
    }
    with pytest.raises(MockInterviewContractError, match="item_shape"):
        validate_feedback({**_feedback(), "strengths": [legacy]}, _catalog())


def test_safe_empty_has_exactly_four_empty_arrays():
    assert set(SAFE_EMPTY_FEEDBACK) == {
        "schema_version", "proposal_status", "strengths", "practice_points",
        "follow_up_questions", "next_practice_steps",
    }
    assert SAFE_EMPTY_FEEDBACK["proposal_status"] == "safe_empty"
    assert all(not SAFE_EMPTY_FEEDBACK[field] for field in (
        "strengths", "practice_points", "follow_up_questions", "next_practice_steps"
    ))
