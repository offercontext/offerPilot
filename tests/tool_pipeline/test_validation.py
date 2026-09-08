from __future__ import annotations

import socket
import urllib.request
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from offerpilot.ai.tool_runtime.validation import (
    ArgumentValidationError,
    SchemaContractError,
    canonical_json,
    compile_tool_schema,
    lossless_typed_copy,
    parse_arguments,
    validate_arguments,
)


@pytest.mark.parametrize(
    ("raw", "code"),
    (
        ('{"a":1,"a":2}', "duplicate_argument_key"),
        ("[]", "arguments_not_object"),
        ('{"x":NaN}', "non_finite_number"),
        ('{"x":Infinity}', "non_finite_number"),
        ('{"x":-Infinity}', "non_finite_number"),
        ('{"x":1e999}', "non_finite_number"),
        ("{", "invalid_json"),
    ),
)
def test_parse_arguments_rejects_ambiguous_or_non_object_json(raw: str, code: str) -> None:
    with pytest.raises(ArgumentValidationError) as error:
        parse_arguments(raw)

    assert error.value.code == code


def test_parse_arguments_and_lossless_copy_preserve_json_semantics() -> None:
    parsed = parse_arguments('{"a":1,"extra":{"values":[true,null,1.5]},"text":"é"}')
    copied = lossless_typed_copy(parsed)

    assert copied == parsed
    assert copied is not parsed
    assert copied["extra"] is not parsed["extra"]
    assert canonical_json({"text": "é"}) != canonical_json({"text": "é"})


@pytest.mark.parametrize("keyword", ("$ref", "$dynamicRef"))
def test_compile_tool_schema_rejects_external_references_without_network(
    keyword: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    def reject_network(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        network_calls.append("called")
        raise AssertionError("schema compilation must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(urllib.request, "urlopen", reject_network)

    with pytest.raises(SchemaContractError, match="external_schema_reference"):
        compile_tool_schema(
            {
                "$defs": {"id": {"type": "integer"}},
                "properties": {"id": {keyword: "https://example.invalid/id.json"}},
                "type": "object",
            }
        )

    assert network_calls == []


def test_compile_tool_schema_uses_draft_2020_12_local_refs_and_no_format_checker() -> None:
    validator = compile_tool_schema(
        {
            "$defs": {"email": {"format": "email", "type": "string"}},
            "additionalProperties": True,
            "properties": {"email": {"$ref": "#/$defs/email"}},
            "type": "object",
        }
    )

    assert isinstance(validator, Draft202012Validator)
    assert validate_arguments(validator, {"email": "not-an-email", "extra": 1}) == {
        "email": "not-an-email",
        "extra": 1,
    }


def test_compile_tool_schema_rejects_malformed_internal_schema() -> None:
    with pytest.raises(SchemaContractError, match="invalid_tool_schema"):
        compile_tool_schema({"type": "definitely-not-a-json-schema-type"})


def test_validate_arguments_reports_stable_schema_code() -> None:
    validator = compile_tool_schema(
        {
            "additionalProperties": False,
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "type": "object",
        }
    )

    with pytest.raises(ArgumentValidationError) as error:
        validate_arguments(validator, {"id": "1"})

    assert error.value.code == "schema_validation_failed"
