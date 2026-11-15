"""One-target shadow contract adapter. No transport, provider or gold access."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from .common import sha, public_row, exact
from .mechanics import validate
from .annotation_checks import validate_annotations

VERSION = "single-target-shadow-v0.2-1"
# Local resource limits; not a model token budget or inherited provider profile.
MAX_INPUT_BYTES = 512 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "OUTPUT_SCHEMA.json").read_text())
CONTRACT = (ROOT / "SINGLE_TARGET_CONTRACT.txt").read_text()


class AdapterError(ValueError):
    def __init__(self, code, raw):
        self.code = code
        self.raw = raw
        self.sha256 = sha(raw) if type(raw) is bytes else None
        super().__init__(code)


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def decode(raw, limit):
    if type(raw) is not bytes:
        raise AdapterError("bytes_required", raw)
    if len(raw) > limit:
        raise AdapterError("size_limit", raw)

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate_key")
            result[k] = v
        return result

    def constant(_):
        raise ValueError("nonfinite_number")

    def walk(v, depth=0):
        if depth > 64:
            raise ValueError("nesting_limit")
        if type(v) is str and any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise ValueError("surrogate")
        if type(v) is dict:
            for k, x in v.items():
                walk(k, depth + 1)
                walk(x, depth + 1)
        if type(v) is list:
            for x in v:
                walk(x, depth + 1)
        if type(v) is float:
            # There are no floating-point fields in this protocol.
            raise ValueError("floating_point_not_allowed")

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=constant
        )
        walk(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        code = (
            str(exc)
            if str(exc)
            in {
                "duplicate_key",
                "nonfinite_number",
                "nesting_limit",
                "surrogate",
                "floating_point_not_allowed",
            }
            else "invalid_utf8_or_json"
        )
        raise AdapterError(code, raw) from None


def input_row(raw):
    row = decode(raw, MAX_INPUT_BYTES)
    try:
        public_row(row)
        # Invalid frozen candidate citations remain input data: blocked output is
        # representable. Invalid sources/binding are input contract failures.
        checks = validate(row)["checks"]
        if any(v != "pass" for v in checks.values()):
            raise ValueError("input_mechanics")
        return row
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise AdapterError("input_contract:" + str(exc), raw) from None


@dataclass(frozen=True)
class Request:
    original_input: bytes
    body: bytes

    @property
    def input_sha256(self):
        return sha(self.original_input)

    @property
    def body_sha256(self):
        return sha(self.body)

    @property
    def request_id(self):
        return correlation_id(self.original_input)


def correlation_id(raw):
    # Opaque correlation token, locally computed, never a semantic proof.
    return "req_" + sha(VERSION.encode() + b"\0" + raw)[:32]


def build_request(original_input: bytes) -> Request:
    row = input_row(original_input)
    body = encode(
        {
            "version": VERSION,
            "request_id": correlation_id(original_input),
            "mode": "single_response_no_tools",
            "logical_stages": ["fact_gate", "relation_if_eligible"],
            "response_count": 1,
            "contract": CONTRACT,
            "input": row,
            "response_contract": {
                "envelope_fields": ["version", "request_id", "assessment"],
                "version": VERSION,
                "assessment_schema": SCHEMA["items"],
                "binding": "Echo request_id exactly (do not calculate it). assessment.id equals input.id. No hash or target metadata echo; local context binds the original target.",
            },
        }
    )
    if len(body) > MAX_REQUEST_BYTES:
        raise AdapterError("request_size_limit", original_input)
    return Request(original_input, body)


def parse_response(raw: bytes, request: Request) -> dict[str, Any]:
    # Rebuild from immutable original bytes; never trust a caller-supplied body.
    rebuilt = build_request(request.original_input)
    if rebuilt.body != request.body:
        raise AdapterError("request_binding_mismatch", raw)
    row = input_row(request.original_input)
    result = decode(raw, MAX_RESPONSE_BYTES)
    try:
        exact(result, {"version", "request_id", "assessment"})
        if result["version"] != VERSION:
            raise ValueError("response_version")
        if result["request_id"] != request.request_id:
            raise ValueError("response_correlation")
        validate_annotations([result["assessment"]], [row], SCHEMA)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as exc:
        raise AdapterError("response_contract:" + str(exc), raw) from None
    return {
        "status": "structurally_valid",
        "semantic_review_required": True,
        "semantic_quality_established": False,
        "input_sha256": request.input_sha256,
        "request_sha256": request.body_sha256,
        "response_sha256": sha(raw),
        "trusted_target_context": {
            "id": row["id"],
            "target": row["target"],
            "target_binding": row["target_binding"],
        },
        # These are untrusted reviewer claims, NOT approved product output.
        "reviewer_claims": result["assessment"],
        "mechanical_report": validate(row),
    }


class FakeRecorder:
    """In-memory fixed response, not an actual Broker integration or network API."""

    def __init__(self, response, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def exchange(self, body):
        self.calls.append(body)
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return self.response


def offline_roundtrip(original_input, recorder):
    # Intentionally reject generic transports: this entry point is fake-only.
    if type(recorder) is not FakeRecorder:
        raise TypeError("fake_recorder_required")
    evidence = {
        "input_bytes": original_input,
        "input_sha256": sha(original_input) if type(original_input) is bytes else None,
        "request_bytes": None,
        "response_bytes": None,
        "attempted": 0,
    }
    try:
        request = build_request(original_input)
        evidence.update(request_bytes=request.body, request_sha256=request.body_sha256)
        evidence["attempted"] = 1
        try:
            response = recorder.exchange(request.body)
        except RuntimeError:
            return {"status": "failed", "error_code": "fake_exchange_failure", "evidence": evidence}
        evidence.update(
            response_bytes=response,
            response_sha256=sha(response) if type(response) is bytes else None,
        )
        parsed = parse_response(response, request)
        return {**parsed, "evidence": evidence}
    except AdapterError as exc:
        return {"status": "failed", "error_code": exc.code, "evidence": evidence}
