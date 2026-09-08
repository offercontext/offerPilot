from __future__ import annotations

import copy
import json

import pytest

from offerpilot.ai.opportunity_fit_reviews import (
    OpportunityFitModelError,
    generate_deep_review,
    generate_triage,
    generate_triage_v2,
    validate_deep_review_v2,
    validate_deep_review,
    validate_triage_v2,
    validate_triage,
)
from offerpilot.ai.types import Assistant


def snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "application": {"id": 42, "company_name": "Acme", "position_name": "Backend Engineer"},
        "resume": {
            "id": 7,
            "title": "Backend Resume",
            "content_json": {
                "experience": [{"highlights": ["Built APIs", "Reviewed code"]}],
                "skills": ["Python"],
                "raw_text": "Built APIs. Reviewed code.",
            },
            "sha256": "resume-hash",
        },
        "jd": {
            "source_label": "Recruiter page copy",
            "text": "Must build reliable APIs. Kubernetes production experience preferred.",
            "sha256": "jd-hash",
        },
        "candidate_assertions": [
            {"index": 0, "text": "I can work full-time in Shanghai."},
        ],
    }


def triage_payload() -> dict[str, object]:
    return {
        "recommendation": "hold",
        "hard_constraints": [
            {
                "id": "location",
                "requirement": "Shanghai office",
                "status": "unknown",
                "explanation": "The JD mentions the location, but the materials do not establish availability.",
                "evidence_refs": [
                    {
                        "source": "jd",
                        "path": "/text",
                        "excerpt": "Must build reliable APIs. Kubernetes production experience preferred.",
                    }
                ],
            }
        ],
        "fit_signals": [
            {
                "id": "api",
                "statement": "Existing API implementation experience is directly relevant.",
                "evidence_refs": [
                    {
                        "source": "resume",
                        "path": "/experience/0/highlights/0",
                        "excerpt": "Built APIs",
                    }
                ],
            }
        ],
        "gaps": [
            {
                "id": "kubernetes",
                "requirement": "Kubernetes production experience",
                "kind": "preferred",
                "candidate_status": "unknown",
                "evidence_refs": [
                    {
                        "source": "jd",
                        "path": "/text",
                        "excerpt": "Must build reliable APIs. Kubernetes production experience preferred.",
                    },
                    {
                        "source": "resume",
                        "path": "/raw_text",
                        "excerpt": "Built APIs. Reviewed code.",
                    }
                ],
            }
        ],
        "deadline": {"status": "not_stated", "text": "", "evidence_refs": []},
        "next_questions": ["Can you accept working from the Shanghai office?"],
    }


def deep_review_payload() -> dict[str, object]:
    return {
        "strengths": [
            {
                "id": "api",
                "statement": "Existing API work is a relevant strength.",
                "evidence_refs": [
                    {
                        "source": "resume",
                        "path": "/experience/0/highlights/0",
                        "excerpt": "Built APIs",
                    }
                ],
            }
        ],
        "gaps_to_address": [
            {
                "id": "kubernetes",
                "statement": "Kubernetes production experience remains unconfirmed.",
                "evidence_refs": [
                    {
                        "source": "resume",
                        "path": "/raw_text",
                        "excerpt": "Built APIs. Reviewed code.",
                    }
                ],
            }
        ],
        "questions_to_clarify": [
            {
                "id": "location",
                "statement": "Confirm whether Shanghai office work is acceptable.",
                "evidence_refs": [],
            }
        ],
        "recommended_path": "clarify_first",
        "next_actions": [
            {
                "id": "assertion",
                "label": "补充事实",
                "kind": "add_assertion",
            }
        ],
    }


def v2_ref(source: str = "resume", path: str = "/raw_text", excerpt: str = "Built APIs") -> dict[str, str]:
    return {"source": source, "path": path, "excerpt": excerpt}


def v2_source() -> dict[str, str]:
    return {
        "kind": "opportunity_fit",
        "contract_version": "opportunity_fit.v2",
        "snapshot_version": "1",
    }


def triage_v2_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "stage": "triage",
        "source": v2_source(),
        "summary": {
            "text": "现有 API 经验可作为继续核对的条件依据。",
            "rationale": "该判断直接来自冻结简历。",
            "evidence_refs": [v2_ref()],
        },
        "conditions": [
            {
                "id": "api-experience",
                "text": "可继续核对 API 相关经历。",
                "rationale": "简历记录了 API 构建经历。",
                "evidence_refs": [v2_ref()],
            }
        ],
        "risks": [
            {
                "id": "kubernetes-evidence",
                "text": "Kubernetes 经验仍需核对。",
                "rationale": "岗位描述要求 Kubernetes，而简历未提供对应事实。",
                "evidence_refs": [
                    v2_ref("jd", "/jd_text", snapshot()["jd"]["text"]),
                ],
            }
        ],
        "questions": [
            {
                "question_id": "opportunity_fit.question.v1.jd_success_criteria",
                "text": "请确认该岗位最重要的成功标准是什么？",
                "evidence_refs": [],
            }
        ],
        "next_steps": [
            {
                "id": "confirm-kubernetes",
                "text": "补充 Kubernetes 经验的原始事实。",
                "rationale": "当前资料不足以验证该要求。",
                "evidence_refs": [
                    v2_ref("jd", "/jd_text", snapshot()["jd"]["text"]),
                ],
            }
        ],
    }


def deep_v2_payload() -> dict[str, object]:
    payload = triage_v2_payload()
    payload["stage"] = "deep_review"
    return payload


class ScriptedModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.prompts.append(messages[-1].content)
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return Assistant(content=response)
        return Assistant(content=json.dumps(response, ensure_ascii=False))


def test_valid_triage_is_strictly_validated() -> None:
    result = validate_triage(triage_payload(), snapshot())
    assert result.payload["recommendation"] == "hold"
    assert result.payload["summary"]["evidence_refs"]


def test_derived_summary_does_not_claim_candidate_evidence_from_jd_only() -> None:
    payload = triage_payload()
    payload["fit_signals"] = []
    payload["gaps"] = []
    result = validate_triage(payload, snapshot())
    assert "Evidence-backed" not in result.payload["summary"]["text"]


def test_valid_deep_review_is_strictly_validated() -> None:
    result = validate_deep_review(deep_review_payload(), snapshot())
    assert result.payload["recommended_path"] == "clarify_first"


def test_valid_v2_triage_and_deep_review_are_strictly_validated() -> None:
    triage = validate_triage_v2(triage_v2_payload(), snapshot())
    deep = validate_deep_review_v2(deep_v2_payload(), snapshot())

    assert triage.payload["stage"] == "triage"
    assert deep.payload["stage"] == "deep_review"


def test_v2_rejects_legacy_decision_and_uncited_summary() -> None:
    payload = triage_v2_payload()
    payload["recommendation"] = "advance"
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())

    payload = triage_v2_payload()
    payload["summary"] = {
        "text": "候选人保证满足全部岗位要求。",
        "rationale": "模型判断。",
        "evidence_refs": [],
    }
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())


def test_v2_requires_evidence_for_conditions_and_risks() -> None:
    payload = triage_v2_payload()
    payload["conditions"][0]["evidence_refs"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())

    payload = triage_v2_payload()
    payload["risks"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())


def test_v2_rejects_whitespace_text_and_rationale() -> None:
    payload = triage_v2_payload()
    payload["conditions"][0]["text"] = " \t"
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())

    payload = triage_v2_payload()
    payload["conditions"][0]["rationale"] = " \t"
    with pytest.raises(OpportunityFitModelError):
        validate_triage_v2(payload, snapshot())


def test_v2_rejects_duplicate_json_keys() -> None:
    duplicate = '{"schema_version":2,"schema_version":2}'
    model = ScriptedModel([duplicate, duplicate])
    with pytest.raises(OpportunityFitModelError) as error:
        generate_triage_v2(model, snapshot())
    assert model.calls == 2
    assert error.value.failure_category == "duplicate_json_key"
    assert "duplicate_json_key" in model.prompts[1]


@pytest.mark.parametrize(
    ("reply", "expected_category"),
    [
        ("[]", "root_object_invalid"),
        ("not json", "invalid_json"),
    ],
)
def test_v2_classifies_json_shape_failures(reply: str, expected_category: str) -> None:
    model = ScriptedModel([reply, reply])

    with pytest.raises(OpportunityFitModelError) as error:
        generate_triage_v2(model, snapshot())

    assert error.value.failure_category == expected_category
    assert expected_category in model.prompts[1]


def test_v2_classifies_field_shape_failures_without_model_content() -> None:
    invalid = copy.deepcopy(triage_v2_payload())
    invalid["conditions"][0]["text"] = 123
    model = ScriptedModel([invalid, invalid])

    with pytest.raises(OpportunityFitModelError) as error:
        generate_triage_v2(model, snapshot())

    assert error.value.failure_category == "invalid_field_type"
    assert "invalid_field_type" in model.prompts[1]
    assert "123" not in model.prompts[1]


def test_v2_classifies_missing_extra_empty_and_limit_failures() -> None:
    missing = copy.deepcopy(triage_v2_payload())
    del missing["conditions"]
    with pytest.raises(OpportunityFitModelError) as missing_error:
        validate_triage_v2(missing, snapshot())
    assert missing_error.value.failure_category == "missing_field"

    extra = copy.deepcopy(triage_v2_payload())
    extra["unexpected"] = True
    with pytest.raises(OpportunityFitModelError) as extra_error:
        validate_triage_v2(extra, snapshot())
    assert extra_error.value.failure_category == "unexpected_field"

    empty = copy.deepcopy(triage_v2_payload())
    empty["conditions"][0]["rationale"] = " \t"
    with pytest.raises(OpportunityFitModelError) as empty_error:
        validate_triage_v2(empty, snapshot())
    assert empty_error.value.failure_category == "empty_value"

    limited = copy.deepcopy(triage_v2_payload())
    limited["conditions"] = [copy.deepcopy(limited["conditions"][0]) for _ in range(9)]
    with pytest.raises(OpportunityFitModelError) as limit_error:
        validate_triage_v2(limited, snapshot())
    assert limit_error.value.failure_category == "quantity_limit"


@pytest.mark.parametrize(
    ("field", "value", "expected_category"),
    [
        (
            "source",
            {"source": "attacker", "path": "/raw_text", "excerpt": "Built APIs"},
            "evidence_source_invalid",
        ),
        (
            "path",
            {"source": "resume", "path": "/missing", "excerpt": "Built APIs"},
            "evidence_path_invalid",
        ),
        (
            "excerpt",
            {"source": "resume", "path": "/raw_text", "excerpt": "Invented"},
            "evidence_excerpt_invalid",
        ),
        (
            "excerpt",
            {"source": "resume", "path": "/raw_text", "excerpt": " \t"},
            "empty_value",
        ),
    ],
)
def test_v2_classifies_evidence_reference_failures(
    field: str, value: dict[str, str], expected_category: str
) -> None:
    payload = copy.deepcopy(triage_v2_payload())
    payload["conditions"][0]["evidence_refs"] = [value]

    with pytest.raises(OpportunityFitModelError) as error:
        validate_triage_v2(payload, snapshot())

    assert error.value.failure_category == expected_category


def test_v2_deep_rejects_uncited_specific_gap() -> None:
    payload = deep_v2_payload()
    payload["risks"] = [
        {
            "id": "gap",
            "text": "候选人能力不足。",
            "rationale": "模型判断。",
            "evidence_refs": [],
        }
    ]
    with pytest.raises(OpportunityFitModelError):
        validate_deep_review_v2(payload, snapshot())


def test_deep_review_rejects_uncited_gap() -> None:
    payload = deep_review_payload()
    payload["gaps_to_address"][0]["evidence_refs"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_deep_review(payload, snapshot())


def test_triage_rejects_extra_fields_and_invalid_recommendation() -> None:
    payload = triage_payload()
    payload["extra"] = "no"
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["summary"] = "Candidate guarantees all job requirements."
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["recommendation"] = "maybe"
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())


def test_triage_rejects_jd_as_candidate_evidence() -> None:
    payload = triage_payload()
    payload["fit_signals"][0]["evidence_refs"][0] = {
        "source": "jd",
        "path": "/text",
        "excerpt": "Must build reliable APIs. Kubernetes production experience preferred.",
    }
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["hard_constraints"][0]["status"] = "met"
    payload["hard_constraints"][0]["evidence_refs"] = [
        {
            "source": "jd",
            "path": "/text",
            "excerpt": "Must build reliable APIs. Kubernetes production experience preferred.",
        }
    ]
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["gaps"][0]["candidate_status"] = "unmet"
    payload["gaps"][0]["evidence_refs"] = [
        {
            "source": "jd",
            "path": "/text",
            "excerpt": "Must build reliable APIs. Kubernetes production experience preferred.",
        }
    ]
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())


def test_triage_requires_jd_evidence_for_role_requirements() -> None:
    payload = triage_payload()
    payload["hard_constraints"][0]["evidence_refs"] = [
        {
            "source": "resume",
            "path": "/raw_text",
            "excerpt": "Built APIs. Reviewed code.",
        }
    ]
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["gaps"][0]["evidence_refs"] = [
        {
            "source": "resume",
            "path": "/raw_text",
            "excerpt": "Built APIs. Reviewed code.",
        }
    ]
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

def test_triage_rejects_invalid_resume_path_and_excerpt() -> None:
    payload = triage_payload()
    payload["fit_signals"][0]["evidence_refs"][0] = {
        "source": "resume",
        "path": "/experience/9/highlights/0",
        "excerpt": "Built APIs",
    }
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["fit_signals"][0]["evidence_refs"][0]["excerpt"] = "Invented"
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())


def test_triage_rejects_missing_evidence_for_asserted_fact() -> None:
    payload = triage_payload()
    payload["fit_signals"][0]["evidence_refs"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())


def test_triage_rejects_invalid_recommendation_semantics() -> None:
    payload = triage_payload()
    payload["recommendation"] = "advance"
    payload["hard_constraints"][0]["status"] = "unmet"
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())

    payload = triage_payload()
    payload["recommendation"] = "hold"
    payload["hard_constraints"] = []
    payload["next_questions"] = []
    with pytest.raises(OpportunityFitModelError):
        validate_triage(payload, snapshot())


def test_triage_rejects_fenced_and_non_finite_json() -> None:
    fence = chr(96) * 3
    model = ScriptedModel([f"{fence}json\n{{}}\n{fence}"])
    with pytest.raises(OpportunityFitModelError):
        generate_triage(model, snapshot())
    assert model.calls == 2

    class NonFiniteModel:
        calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            return Assistant(
                content='{"recommendation":"hold","hard_constraints":[],"fit_signals":[],"gaps":[],"deadline":{"status":"not_stated","text":"","evidence_refs":[]},"next_questions":["x"],"value":NaN}'
            )

    non_finite = NonFiniteModel()
    with pytest.raises(OpportunityFitModelError):
        generate_triage(non_finite, snapshot())
    assert non_finite.calls == 2


def test_format_failure_is_repaired_once() -> None:
    invalid = copy.deepcopy(triage_payload())
    invalid["fit_signals"][0]["statement"] = 17
    model = ScriptedModel([invalid, triage_payload()])

    result = generate_triage(model, snapshot())

    assert result.payload["recommendation"] == "hold"
    assert model.calls == 2
    assert "invalid_change_shape" in model.prompts[1]
    assert "17" not in model.prompts[1]


def test_provider_failure_is_not_retried() -> None:
    model = ScriptedModel([RuntimeError("provider unavailable")])
    with pytest.raises(OpportunityFitModelError) as error:
        generate_triage(model, snapshot())
    assert error.value.failure_category == "provider_error"
    assert model.calls == 1


def test_deep_review_uses_one_repair_retry() -> None:
    invalid = copy.deepcopy(deep_review_payload())
    invalid["recommended_path"] = "send_application"
    model = ScriptedModel([invalid, deep_review_payload()])

    result = generate_deep_review(model, snapshot(), triage_payload())

    assert result.payload["recommended_path"] == "clarify_first"
    assert model.calls == 2


def test_model_prompts_include_snapshot_paths_but_not_unrelated_fields() -> None:
    model = ScriptedModel([triage_payload()])
    generate_triage(model, snapshot())

    prompt = model.prompts[0]
    assert "/experience/0/highlights/0" in prompt
    assert "Must build reliable APIs" in prompt
    assert "普通备注" not in prompt
