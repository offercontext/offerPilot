from __future__ import annotations

import json

import pytest

from offerpilot.ai.types import Assistant
from offerpilot.ai.interview_review_proposals import (
    INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1,
    INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1,
    InterviewReviewModelError,
    build_interview_review_snapshot,
    generate_interview_review_proposal,
    validate_interview_review,
)
from offerpilot.ai.workflows import parse_json_reply


def _snapshot(*, empty: bool = False) -> dict[str, object]:
    value = "" if empty else "I struggled to explain the cache invalidation tradeoff."
    return {
        "note": {
            "company": "Acme",
            "position": "Backend Engineer",
            "round": "technical",
            "date": "2026-07-20",
            "questions": "How would you design a cache?",
            "self_reflection": value,
            "difficulty_points": "Explaining tradeoffs",
            "mood": "nervous",
        },
        "event": {
            "id": 4,
            "application_id": 7,
            "event_type": "interview",
            "subtype": "technical",
            "round": 2,
            "scheduled_at": "2026-07-20T10:00:00Z",
            "duration_minutes": 45,
            "status": "done",
        },
    }


def _ref(path: str, excerpt: str) -> dict[str, str]:
    return {"source": "interview_note", "path": path, "excerpt": excerpt}


def test_snapshot_contains_only_minimal_note_and_event_fields() -> None:
    class Note:
        company = "Acme"
        position = "Backend"
        round = "technical"
        date = "2026-07-20"
        questions = "Question"
        self_reflection = "Reflection"
        difficulty_points = "Difficulty"
        mood = "calm"
        application_id = 7

    class Event:
        id = 4
        application_id = 7
        event_type = "interview"
        subtype = "technical"
        round = 2
        scheduled_at = "2026-07-20T10:00:00Z"
        duration_minutes = 45
        status = "done"
        location = "secret meeting link"
        notes = "private interviewer notes"

    snapshot = build_interview_review_snapshot(Note(), Event())

    assert set(snapshot) == {"note", "event"}
    assert set(snapshot["note"]) == {
        "company",
        "position",
        "round",
        "date",
        "questions",
        "self_reflection",
        "difficulty_points",
        "mood",
    }
    assert set(snapshot["event"]) == {
        "id",
        "application_id",
        "event_type",
        "subtype",
        "round",
        "scheduled_at",
        "duration_minutes",
        "status",
    }


def _proposal() -> dict[str, object]:
    return {
        "summary": {
            "text": "The reflection highlights a cache design tradeoff to revisit.",
            "evidence_refs": [_ref("/self_reflection", "I struggled to explain the cache invalidation tradeoff.")],
        },
        "observations": [
            {
                "id": "observation-1",
                "text": "The cache design tradeoff was difficult to explain.",
                "evidence_refs": [_ref("/difficulty_points", "Explaining tradeoffs")],
            }
        ],
        "clarifications": [
            {
                "id": "clarification-1",
                "question": "What part of the cache tradeoff was hardest to explain?",
                "evidence_refs": [_ref("/self_reflection", "cache invalidation tradeoff")],
            }
        ],
        "practice_focuses": [
            {
                "id": "practice-1",
                "text": "Practice explaining cache invalidation tradeoffs.",
                "evidence_refs": [_ref("/difficulty_points", "Explaining tradeoffs")],
            }
        ],
        "next_questions": [
            {
                "id": "next-1",
                "question": "What cache invalidation strategy would you choose and why?",
                "evidence_refs": [_ref("/questions", "How would you design a cache?")],
            }
        ],
    }


def test_validator_accepts_strict_evidence_backed_proposal() -> None:
    result = validate_interview_review(_proposal(), _snapshot())

    assert result == _proposal()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["summary"].update({"extra": "no"}),
        lambda payload: payload["observations"][0].update({"text": 7}),
        lambda payload: payload["practice_focuses"][0].update({"evidence_refs": []}),
        lambda payload: payload["next_questions"][0].update(
            {"evidence_refs": [{"source": "interview_note", "path": "/mood", "excerpt": "wrong"}]}
        ),
    ],
)
def test_validator_rejects_invalid_shapes_and_citations(mutate) -> None:  # type: ignore[no-untyped-def]
    payload = json.loads(json.dumps(_proposal()))
    mutate(payload)

    with pytest.raises(InterviewReviewModelError):
        validate_interview_review(payload, _snapshot())


def test_validator_rejects_item_and_evidence_limits() -> None:
    payload = _proposal()
    payload["observations"] = [
        {
            "id": f"observation-{index}",
            "text": "The tradeoff was difficult to explain.",
            "evidence_refs": [_ref("/difficulty_points", "Explaining tradeoffs")],
        }
        for index in range(11)
    ]
    with pytest.raises(InterviewReviewModelError):
        validate_interview_review(payload, _snapshot())

    payload = _proposal()
    payload["summary"]["evidence_refs"] = [  # type: ignore[index]
        _ref("/difficulty_points", "Explaining tradeoffs") for _ in range(6)
    ]
    with pytest.raises(InterviewReviewModelError):
        validate_interview_review(payload, _snapshot())


def test_strict_parser_rejects_fences_duplicate_keys_and_non_finite_values() -> None:
    invalid_replies = [
        "```json\n{}\n```",
        '{"summary": {}, "summary": {}}',
        '{"summary": NaN}',
        '{"summary": Infinity}',
        '{"summary": -Infinity}',
    ]
    for reply in invalid_replies:
        with pytest.raises(Exception):
            parse_json_reply(
                reply,
                allow_fenced=False,
                reject_non_finite=True,
                reject_duplicate_keys=True,
            )


def test_validator_rejects_event_as_evidence_and_extra_top_level_fields() -> None:
    payload = _proposal()
    payload["unexpected"] = True
    with pytest.raises(InterviewReviewModelError):
        validate_interview_review(payload, _snapshot())

    payload = _proposal()
    payload["summary"]["evidence_refs"] = [  # type: ignore[index]
        {"source": "application_event", "path": "/status", "excerpt": "done"}
    ]
    with pytest.raises(InterviewReviewModelError) as exc_info:
        validate_interview_review(payload, _snapshot())
    assert exc_info.value.validation_category == "unknown_evidence_ref"


def test_validator_allows_only_versioned_ungrounded_summary_and_questions() -> None:
    payload = {
        "summary": {"text": INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1, "evidence_refs": []},
        "observations": [],
        "clarifications": [
            {
                "id": "clarification-1",
                "question": INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1[0],
                "evidence_refs": [],
            }
        ],
        "practice_focuses": [],
        "next_questions": [
            {
                "id": "next-1",
                "question": INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1[1],
                "evidence_refs": [],
            }
        ],
    }

    assert validate_interview_review(payload, _snapshot())["summary"] == payload["summary"]

    payload["summary"]["text"] = "No evidence is needed."
    with pytest.raises(InterviewReviewModelError):
        validate_interview_review(payload, _snapshot())


def test_empty_snapshot_can_only_return_safe_summary_and_questions() -> None:
    payload = {
        "summary": {"text": INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1, "evidence_refs": []},
        "observations": [],
        "clarifications": [],
        "practice_focuses": [],
        "next_questions": [
            {"id": "next-1", "question": INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1[0], "evidence_refs": []}
        ],
    }

    empty = _snapshot(empty=True)
    empty["note"] = {key: "" for key in empty["note"]}  # type: ignore[index]
    assert validate_interview_review(payload, empty)


def test_generate_retries_once_for_invalid_shape() -> None:
    invalid = _proposal()
    invalid["observations"][0]["text"] = {"not": "a string"}  # type: ignore[index]
    valid = _proposal()

    class RepairingModel:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.prompts.append(messages[-1].content)
            return Assistant(content=json.dumps(invalid if self.calls == 1 else valid))

    model = RepairingModel()
    result = generate_interview_review_proposal(model, _snapshot())

    assert model.calls == 2
    assert result == valid
    assert "invalid_structure" in model.prompts[1]
    assert "not a string" not in model.prompts[1]


def test_generate_does_not_retry_provider_errors() -> None:
    class ProviderModel:
        calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise TimeoutError("provider detail must not escape")

    model = ProviderModel()
    with pytest.raises(InterviewReviewModelError) as exc_info:
        generate_interview_review_proposal(model, _snapshot())

    assert model.calls == 1
    assert exc_info.value.failure_category == "provider_error"


def test_generate_returns_safe_empty_after_one_invalid_retry() -> None:
    class InvalidModel:
        calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            return Assistant(content='{"summary": {"text": "bad", "evidence_refs": []}}')

    model = InvalidModel()
    result = generate_interview_review_proposal(model, _snapshot())

    assert model.calls == 2
    assert result["summary"] == {
        "text": INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1,
        "evidence_refs": [],
    }
    assert result["observations"] == []
    assert result["practice_focuses"] == []
    assert result["clarifications"][0]["question"] == INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1[0]
    assert result["clarifications"][0]["evidence_refs"] == []


def test_generate_emits_redacted_diagnostic_for_safe_empty_fallback() -> None:
    diagnostics: list[dict[str, object]] = []

    class InvalidModel:
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            return Assistant(content='{"summary": {"text": "candidate secret", "evidence_refs": []}}')

    generate_interview_review_proposal(
        InvalidModel(),
        _snapshot(),
        on_diagnostic=diagnostics.append,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["failure_category"] == "unexpected_field"
    assert diagnostic["repair_attempted"] is True
    assert diagnostic["retry_count"] == 1
    assert "candidate secret" not in str(diagnostic)
    assert "cache invalidation" not in str(diagnostic)


def test_prompt_lists_exact_evidence_candidates_and_safe_output_limits() -> None:
    from offerpilot.ai.interview_review_proposals import _interview_review_prompt, _interview_review_system

    prompt = _interview_review_prompt(_snapshot())
    system = _interview_review_system()
    assert "Verified evidence candidates" in prompt
    assert "/questions" in prompt
    assert "How would you design a cache?" in prompt
    assert "at most 10" in system
    assert "at most 5" in system
    assert "exact safe JSON shape" in system


def test_generate_uses_native_schema_when_model_explicitly_supports_it() -> None:
    class StructuredModel:
        supports_json_schema = True

        def __init__(self) -> None:
            self.response_formats = []

        def complete(self, messages, tools, response_format=None):  # type: ignore[no-untyped-def]
            self.response_formats.append(response_format)
            return Assistant(content=json.dumps(_proposal()))

    model = StructuredModel()
    assert generate_interview_review_proposal(model, _snapshot()) == _proposal()
    assert model.response_formats[0]["type"] == "json_schema"


def test_generate_repair_prompt_classifies_unexpected_field() -> None:
    invalid = _proposal()
    invalid["unexpected"] = True

    class RepairingModel:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.prompts.append(messages[-1].content)
            return Assistant(content=json.dumps(invalid if self.calls == 1 else _proposal()))

    model = RepairingModel()
    assert generate_interview_review_proposal(model, _snapshot()) == _proposal()
    assert "unexpected_field" in model.prompts[1]
