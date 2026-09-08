from __future__ import annotations

import json

import pytest

from offerpilot.ai.types import Assistant
from offerpilot.ai.material_proposals import (
    MaterialProposalModelError,
    generate_material_proposal,
    validate_material_proposal,
)
from offerpilot.ai.workflows import parse_json_reply


def _snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "application": {"id": 7, "company_name": "Acme", "position_name": "Backend"},
        "material_kit": {"id": 3, "jd_snapshot": "FastAPI backend", "content_json": {}},
        "resume": {
            "id": 11,
            "title": "Backend Resume",
            "parsed_data": "Built APIs",
            "content_json": {
                "experience": [{"company": "Acme Labs", "highlights": ["Built APIs", "Reviewed code"]}],
                "skills": ["Python"],
                "raw_text": "Built APIs",
            },
        },
        "latest_evidence_bundle": None,
        "user_assertions": [{"id": "assertion-1", "text": "I led the migration."}],
    }


def _payload() -> dict[str, object]:
    return {
        "summary": "Tailor the backend experience.",
        "changes": [
            {
                "id": "change-fastapi",
                "path": "/experience/0/highlights/0",
                "before": "Built APIs",
                "after": "Built FastAPI APIs for internal workflow automation",
                "rationale": "Make the existing API experience specific.",
                "evidence_refs": [
                    {
                        "source": "resume",
                        "path": "/experience/0/highlights/0",
                        "excerpt": "Built APIs",
                    },
                    {
                        "source": "resume",
                        "path": "/experience/0/company",
                        "excerpt": "Acme Labs",
                    }
                ],
            }
        ],
    }


def test_validator_derives_content_from_only_cited_resume_changes() -> None:
    validated = validate_material_proposal(_payload(), _snapshot())

    assert validated.proposal["summary"] == "Tailor the backend experience."
    assert validated.content["experience"][0]["highlights"][0] == (
        "Built FastAPI APIs for internal workflow automation"
    )
    assert validated.content["experience"][0]["highlights"][1] == "Reviewed code"


@pytest.mark.parametrize(
    "change_patch",
    [
        {"path": "/experience/0/company"},
        {"path": "/experience/0/highlights/-"},
        {"path": "/experience/00/highlights/0"},
        {"path": "/experience/١/highlights/0"},
        {"path": "/experience/0/highlights/0", "before": "Other"},
        {"path": "/experience/0/highlights/0", "evidence_refs": []},
    ],
)
def test_validator_rejects_unauthorized_or_unverifiable_changes(change_patch: dict[str, object]) -> None:
    payload = _payload()
    change = dict(payload["changes"][0])  # type: ignore[index]
    change.update(change_patch)
    payload["changes"] = [change]

    with pytest.raises(MaterialProposalModelError):
        validate_material_proposal(payload, _snapshot())


def test_validator_rejects_duplicate_ids_and_overlapping_paths() -> None:
    payload = _payload()
    second = dict(payload["changes"][0])  # type: ignore[index]
    second["id"] = "change-second"
    second["path"] = "/experience/0/highlights/0"
    payload["changes"] = [payload["changes"][0], second]  # type: ignore[list-item]

    with pytest.raises(MaterialProposalModelError):
        validate_material_proposal(payload, _snapshot())


def test_validator_accepts_empty_changes_without_inventing_content() -> None:
    validated = validate_material_proposal({"summary": "No safe changes.", "changes": []}, _snapshot())

    assert validated.content == _snapshot()["resume"]["content_json"]  # type: ignore[index]


def test_validator_rejects_non_object_model_output() -> None:
    with pytest.raises(MaterialProposalModelError):
        validate_material_proposal(json.loads("[]"), _snapshot())


@pytest.mark.parametrize(
    "path",
    ["/jd/text", "/application/company_name", "/application/position_name"],
)
def test_validator_rejects_jd_and_application_evidence_bundle_paths(path: str) -> None:
    snapshot = _snapshot()
    snapshot["latest_evidence_bundle"] = {
        "id": 4,
        "bundle_sha256": "bundle",
        "snapshot": {
            "jd": {"text": "FastAPI backend"},
            "application": {"company_name": "Acme", "position_name": "Backend"},
        },
    }
    payload = _payload()
    change = dict(payload["changes"][0])  # type: ignore[index]
    change["evidence_refs"] = [{"source": "evidence_bundle", "path": path, "excerpt": "Acme"}]
    if path == "/jd/text":
        change["evidence_refs"][0]["excerpt"] = "FastAPI backend"  # type: ignore[index]
    payload["changes"] = [change]

    with pytest.raises(MaterialProposalModelError):
        validate_material_proposal(payload, snapshot)


def test_validator_rejects_empty_evidence_excerpt() -> None:
    snapshot = _snapshot()
    snapshot["resume"]["content_json"]["skills"] = [""]  # type: ignore[index]
    payload = {
        "summary": "Fill a missing skill.",
        "changes": [
            {
                "id": "change-empty-skill",
                "path": "/skills/0",
                "before": "",
                "after": "Kubernetes",
                "rationale": "Use only an explicit candidate fact.",
                "evidence_refs": [
                    {"source": "resume", "path": "/skills/0", "excerpt": ""}
                ],
            }
        ],
    }

    with pytest.raises(MaterialProposalModelError):
        validate_material_proposal(payload, snapshot)


def test_strict_json_parser_rejects_fenced_markdown() -> None:
    with pytest.raises(RuntimeError):
        parse_json_reply("```json\n{}\n```", allow_fenced=False)


def test_general_json_parser_preserves_legacy_fenced_json_support() -> None:
    assert parse_json_reply("```json\n{}\n```") == {}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_parser_rejects_non_finite_constants(constant: str) -> None:
    with pytest.raises(ValueError):
        parse_json_reply(
            f'{{"summary":"x","changes":[],"ignored":{constant}}}',
            allow_fenced=False,
            reject_non_finite=True,
        )


def test_generate_material_proposal_repairs_one_invalid_change_shape() -> None:
    invalid = _payload()
    invalid["changes"][0] = dict(invalid["changes"][0])  # type: ignore[index]
    invalid["changes"][0]["before"] = {"not": "a string"}  # type: ignore[index]
    valid = _payload()

    class RepairingModel:
        def __init__(self) -> None:
            self.calls = 0
            self.messages: list[str] = []

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.messages.append(messages[-1].content)
            return Assistant(content=json.dumps(invalid if self.calls == 1 else valid))

    model = RepairingModel()
    result = generate_material_proposal(model, _snapshot(), "Highlight API experience")

    assert model.calls == 2
    assert result.proposal == valid
    assert "invalid_change_shape" in model.messages[1]
    assert "not a string" not in model.messages[1]


def test_generate_material_proposal_fails_after_one_invalid_repair() -> None:
    invalid = {"summary": "bad", "changes": [{"before": {"not": "a string"}}]}

    class InvalidModel:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            return Assistant(content=json.dumps(invalid))

    model = InvalidModel()
    with pytest.raises(MaterialProposalModelError):
        generate_material_proposal(model, _snapshot(), "")

    assert model.calls == 2


def test_generate_material_proposal_does_not_retry_provider_errors() -> None:
    class ProviderFailure:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise RuntimeError("provider unavailable")

    model = ProviderFailure()
    with pytest.raises(MaterialProposalModelError):
        generate_material_proposal(model, _snapshot(), "")

    assert model.calls == 1


def test_generate_material_proposal_emits_redacted_failure_diagnostic() -> None:
    first = {"summary": "bad", "changes": [{"id": "first-secret", "before": 7}]}
    second = {
        "summary": "still bad",
        "changes": [
            {
                "id": "second-secret",
                "path": "/experience/0/highlights/0",
                "before": "Built APIs",
                "after": "Built safer APIs",
                "rationale": "Use the source fact.",
                "evidence_refs": [
                    {
                        "source": "resume",
                        "path": "/experience/0/highlights/0",
                        "excerpt": "not the source",
                    }
                ],
            }
        ],
    }

    class InvalidModel:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            return Assistant(
                content=json.dumps(first if self.calls == 1 else second),
                provider_blocks={"request_id": "provider-secret"},
            )

    diagnostics: list[dict[str, object]] = []
    with pytest.raises(MaterialProposalModelError) as raised:
        generate_material_proposal(
            InvalidModel(), _snapshot(), "Use only verified source facts.",
            on_diagnostic=diagnostics.append,
        )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["failure_category"] == "invalid_evidence_reference"
    assert diagnostic["failure_categories"] == [
        "invalid_change_shape",
        "invalid_evidence_reference",
    ]
    assert diagnostic["repair_attempted"] is True
    assert diagnostic["retry_count"] == 1
    assert diagnostic["structure_summaries"][0]["top_level_keys"] == ["changes", "summary"]  # type: ignore[index]
    assert diagnostic["evidence_counts"]["proposal"]["changes"] == 1  # type: ignore[index]
    assert diagnostic["evidence_counts"]["proposal"]["evidence_refs"] == 1  # type: ignore[index]
    assert diagnostic["provider_request_id_hash"] == __import__("hashlib").sha256(
        b"provider-secret"
    ).hexdigest()[:12]
    assert raised.value.diagnostic == diagnostic
    encoded = json.dumps(diagnostic, ensure_ascii=False)
    assert "first-secret" not in encoded
    assert "second-secret" not in encoded
    assert "not the source" not in encoded
    assert "provider-secret" not in encoded


def test_generate_material_proposal_emits_safe_success_evidence_counts() -> None:
    diagnostics: list[dict[str, object]] = []

    class ValidModel:
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            return Assistant(
                content=json.dumps(_payload()),
                provider_blocks={"request_id": "provider-success"},
            )

    generate_material_proposal(
        ValidModel(), _snapshot(), "Highlight API experience",
        on_diagnostic=diagnostics.append,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["failure_category"] is None
    assert diagnostic["failure_categories"] == []
    assert diagnostic["repair_attempted"] is False
    assert diagnostic["retry_count"] == 0
    assert diagnostic["evidence_counts"]["proposal"]["changes"] == 1  # type: ignore[index]
    assert diagnostic["evidence_counts"]["proposal"]["evidence_refs"] == 2  # type: ignore[index]
    assert "Built APIs" not in json.dumps(diagnostic, ensure_ascii=False)


def test_material_proposal_prompt_lists_contract_and_editable_before_values() -> None:
    from offerpilot.ai.material_proposals import _material_proposal_prompt, _material_proposal_system

    system = _material_proposal_system()
    prompt = _material_proposal_prompt(_snapshot(), "")

    assert '"summary": string' in system
    assert "non-empty evidence_refs array" in system
    assert "/experience/0/highlights/0 -> Built APIs" in prompt
    assert "/skills/0 -> Python" in prompt


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "summary": "No safe changes.",
                "changes": [],
                "source_snapshot": "must not be accepted",
            },
            "top-level fields",
        ),
        (
            {
                "summary": "Tailor the API experience.",
                "changes": [
                    {
                        "id": "change-1",
                        "path": "/experience/0/highlights/0",
                        "before": "Built APIs",
                        "after": "Built FastAPI APIs",
                        "rationale": "Clarify the existing work.",
                        "evidence_refs": [
                            {
                                "source": "resume",
                                "path": "/experience/0/highlights/0",
                                "excerpt": "Built APIs",
                                "raw_snapshot": "must not be accepted",
                            }
                        ],
                    }
                ],
            },
            "evidence reference fields",
        ),
        (
            {
                "summary": "Tailor the API experience.",
                "changes": [
                    {
                        "id": "change-1",
                        "path": "/experience/0/highlights/0",
                        "before": "Built APIs",
                        "after": "Built FastAPI APIs",
                        "rationale": "Clarify the existing work.",
                        "evidence_refs": [],
                        "debug": "must not be accepted",
                    }
                ],
            },
            "change fields",
        ),
    ],
)
def test_validator_rejects_extra_contract_fields(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(MaterialProposalModelError, match=message):
        validate_material_proposal(payload, _snapshot())
