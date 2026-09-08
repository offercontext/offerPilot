from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import NoReturn, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from referencing import Registry

from offerpilot.ai.tool_runtime.contracts import JSONValue


class ArgumentValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SchemaContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _object_pairs(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise ArgumentValidationError("duplicate_argument_key")
        result[key] = value
    return result


def _deny_constant(value: str) -> NoReturn:
    del value
    raise ArgumentValidationError("non_finite_number")


def _require_finite(value: JSONValue) -> None:
    if type(value) is float and not math.isfinite(value):
        raise ArgumentValidationError("non_finite_number")
    if type(value) is list:
        for item in value:
            _require_finite(item)
    elif type(value) is dict:
        for item in value.values():
            _require_finite(item)


def parse_arguments(raw: str) -> dict[str, JSONValue]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_pairs,
            parse_constant=_deny_constant,
        )
    except ArgumentValidationError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise ArgumentValidationError("invalid_json") from exc
    if type(value) is not dict:
        raise ArgumentValidationError("arguments_not_object")
    typed = cast(dict[str, JSONValue], value)
    _require_finite(typed)
    return typed


def canonical_json(value: JSONValue) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArgumentValidationError("invalid_json_value") from exc


def lossless_typed_copy(value: JSONValue) -> JSONValue:
    if type(value) is dict:
        return {
            key: lossless_typed_copy(item)
            for key, item in value.items()
        }
    if type(value) is list:
        return [lossless_typed_copy(item) for item in value]
    if value is None or type(value) in {bool, int, float, str}:
        return value
    raise ArgumentValidationError("invalid_json_value")


def _scan_references(value: JSONValue) -> None:
    if type(value) is dict:
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                type(item) is not str or not item.startswith("#")
            ):
                raise SchemaContractError("external_schema_reference")
            _scan_references(item)
    elif type(value) is list:
        for item in value:
            _scan_references(item)


def _deny_retrieval(uri: str) -> NoReturn:
    del uri
    raise SchemaContractError("schema_retrieval_disabled")


def _mapping_copy(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return {key: lossless_typed_copy(item) for key, item in value.items()}


def compile_tool_schema(schema: Mapping[str, JSONValue]) -> Draft202012Validator:
    copied = _mapping_copy(schema)
    _scan_references(copied)
    try:
        Draft202012Validator.check_schema(copied)
    except SchemaError as exc:
        raise SchemaContractError("invalid_tool_schema") from exc
    registry = Registry(retrieve=_deny_retrieval)  # type: ignore[call-arg]
    return Draft202012Validator(copied, registry=registry)


def validate_arguments(
    validator: Draft202012Validator,
    arguments: Mapping[str, JSONValue],
) -> dict[str, JSONValue]:
    copied = _mapping_copy(arguments)
    if next(validator.iter_errors(copied), None) is not None:
        raise ArgumentValidationError("schema_validation_failed")
    return copied
