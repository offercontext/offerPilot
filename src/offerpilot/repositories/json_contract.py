from __future__ import annotations

import hashlib
import json
from typing import Any


class JsonContractError(ValueError):
    """Raised when persisted JSON does not satisfy a strict object contract."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise JsonContractError("value must be valid JSON") from exc


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_json_object(name: str, value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value, parse_constant=_reject_non_finite_json_constant)
    except (TypeError, ValueError) as exc:
        raise JsonContractError(f"{name} content_json must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise JsonContractError(f"{name} content_json must be a JSON object")
    return parsed


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")
