from __future__ import annotations

import copy
import json

import pytest

from offerpilot.ai.interview_stories import (
    INTERVIEW_STORY_JSON_SCHEMA,
    StoryProposalError,
    generate_interview_story_proposal,
    safe_empty_interview_story_proposal,
    validate_interview_story_proposal,
)
from offerpilot.ai.types import Assistant
from offerpilot.repositories.interview_stories import StorySourceSnapshot


class QueuedModel:
    def __init__(self, responses: list[object], *, supports_json_schema: bool = False) -> None:
        self.responses = list(responses)
        self.supports_json_schema = supports_json_schema
        self.calls = 0
        self.messages: list[list[object]] = []
        self.response_formats: list[object] = []

    def complete(self, messages, tools, response_format=None):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1
        self.messages.append(messages)
        self.response_formats.append(response_format)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Assistant(
            content=response if isinstance(response, str) else json.dumps(response, ensure_ascii=False),
            provider_blocks={"request_id": "provider-request-123"},
        )


def _snapshot() -> StorySourceSnapshot:
    sources = [
        {
            "source_kind": "interview_note",
            "source_stable_id": "8",
            "source_version_or_snapshot": "note-v1",
            "path": "/questions",
            "excerpt": "How did you isolate the latency bottleneck?",
            "source_fingerprint": "note-fingerprint",
        },
        {
            "source_kind": "user_assertion",
            "source_stable_id": "assertion_001",
            "source_version_or_snapshot": "pending_confirmation",
            "path": "/statement",
            "excerpt": "I should communicate risk earlier.",
            "source_fingerprint": "assertion-fingerprint",
        },
    ]
    return StorySourceSnapshot(sources=sources, source_fingerprint="snapshot-fingerprint")


def _ref(source: dict[str, str]) -> dict[str, str]:
    return {
        "source_kind": source["source_kind"],
        "source_stable_id": source["source_stable_id"],
        "source_version_or_snapshot": source["source_version_or_snapshot"],
        "source_path": source["path"],
        "excerpt": source["excerpt"],
    }


def _proposal() -> dict[str, object]:
    snapshot = _snapshot()
    note = snapshot.sources[0]
    assertion = snapshot.sources[1]
    return {
        "title": {"text": "Latency incident recovery", "evidence_refs": [_ref(note)]},
        "blocks": [
            {
                "kind": "situation",
                "text": "A latency bottleneck affected a service.",
                "fact_mode": "evidence_backed",
                "evidence_refs": [_ref(note)],
            },
            {
                "kind": "reflection",
                "text": "I would communicate risk earlier next time.",
                "fact_mode": "user_view",
                "evidence_refs": [_ref(assertion)],
            },
        ],
        "capability_labels": [{"text": "incident response", "evidence_refs": [_ref(note)]}],
        "applicable_questions": [
            {"text": "Tell me about an incident.", "evidence_refs": [_ref(note)]}
        ],
        "fact_gap_codes": ["missing_result"],
    }


def test_story_schema_and_validator_allocate_target_ids_and_gate_exact_evidence() -> None:
    result = validate_interview_story_proposal(_proposal(), _snapshot())

    assert result["proposal_status"] == "normal"
    assert result["content"]["title"]["id"] == "title"
    assert result["content"]["blocks"][0]["id"] == "situation_001"
    assert result["evidence_links"][0]["target_kind"] == "applicable_question"
    assert INTERVIEW_STORY_JSON_SCHEMA["additionalProperties"] is False

    forged = copy.deepcopy(_proposal())
    forged["title"]["evidence_refs"][0]["source_path"] = "/mood"  # type: ignore[index]
    with pytest.raises(StoryProposalError) as error:
        validate_interview_story_proposal(forged, _snapshot())
    assert error.value.category == "unknown_evidence_ref"


def test_story_validator_rejects_semantic_evidence_failure_without_repair() -> None:
    forged = copy.deepcopy(_proposal())
    forged["title"]["evidence_refs"][0]["excerpt"] = "made up"  # type: ignore[index]
    model = QueuedModel([forged, _proposal()])

    with pytest.raises(StoryProposalError) as error:
        generate_interview_story_proposal(model, _snapshot())

    assert error.value.category == "excerpt_mismatch"
    assert model.calls == 1


def test_story_validator_reports_evidence_limit_without_repair() -> None:
    forged = copy.deepcopy(_proposal())
    forged["title"]["evidence_refs"][0]["excerpt"] = "x" * 801  # type: ignore[index]

    with pytest.raises(StoryProposalError) as error:
        validate_interview_story_proposal(forged, _snapshot())

    assert error.value.category == "limit_exceeded"


def test_story_validator_reports_too_many_evidence_references_as_limit_exceeded() -> None:
    malformed = _proposal()
    reference = malformed["title"]["evidence_refs"][0]  # type: ignore[index]
    malformed["title"]["evidence_refs"] = [reference] * 6  # type: ignore[index]

    with pytest.raises(StoryProposalError) as error:
        validate_interview_story_proposal(malformed, _snapshot())

    assert error.value.category == "limit_exceeded"


def test_malformed_evidence_shape_is_repaired_before_reference_limit_is_applied() -> None:
    malformed = copy.deepcopy(_proposal())
    malformed["title"]["evidence_refs"] = [{"malformed": "reference"}] * 9  # type: ignore[index]
    model = QueuedModel([malformed, _proposal()])

    result = generate_interview_story_proposal(model, _snapshot())

    assert result["proposal_status"] == "normal"
    assert model.calls == 2
    assert "invalid_evidence_shape" in model.messages[1][1].content


def test_story_shape_error_repairs_once_with_the_frozen_catalog_but_without_model_text() -> None:
    malformed = _proposal()
    malformed["title"] = {"text": "missing evidence"}
    model = QueuedModel([malformed, _proposal()], supports_json_schema=True)

    result = generate_interview_story_proposal(model, _snapshot())

    assert result["proposal_status"] == "normal"
    assert model.calls == 2
    assert model.response_formats[0] is not None
    repair_prompt = model.messages[1][1].content
    assert "invalid_shape" in repair_prompt
    # A repair must receive the same frozen catalog, otherwise it cannot choose
    # an exact, legal evidence reference.  It must never receive the malformed
    # model response itself.
    assert "How did you isolate the latency bottleneck?" in repair_prompt
    assert "I should communicate risk earlier." in repair_prompt
    assert "missing evidence" not in repair_prompt


def test_two_shape_failures_become_non_confirmable_safe_empty() -> None:
    model = QueuedModel(["not json", "still not json"])

    assert generate_interview_story_proposal(model, _snapshot()) == safe_empty_interview_story_proposal()
    assert model.calls == 2


def test_empty_evidence_excerpt_is_a_repairable_shape_error() -> None:
    malformed = _proposal()
    malformed["title"]["evidence_refs"][0]["excerpt"] = ""  # type: ignore[index]
    model = QueuedModel([malformed, _proposal()])

    result = generate_interview_story_proposal(model, _snapshot())

    assert result["proposal_status"] == "normal"
    assert model.calls == 2
    assert "invalid_evidence_shape" in model.messages[1][1].content


def test_story_validator_rejects_title_over_the_shared_manual_limit() -> None:
    malformed = _proposal()
    malformed["title"]["text"] = "x" * 201  # type: ignore[index]

    with pytest.raises(StoryProposalError) as error:
        validate_interview_story_proposal(malformed, _snapshot())

    assert error.value.category == "semantic_contract"


def test_safe_empty_is_the_only_empty_proposal_and_reflection_cannot_be_objective() -> None:
    assert validate_interview_story_proposal(
        {"title": {"text": "", "evidence_refs": []}, "blocks": [], "capability_labels": [], "applicable_questions": [], "fact_gap_codes": []},
        _snapshot(),
    ) == safe_empty_interview_story_proposal()

    invalid = _proposal()
    invalid["blocks"][1]["fact_mode"] = "evidence_backed"  # type: ignore[index]
    with pytest.raises(StoryProposalError) as error:
        validate_interview_story_proposal(invalid, _snapshot())
    assert error.value.category == "semantic_contract"
