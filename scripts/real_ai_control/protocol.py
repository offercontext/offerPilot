"""Bounded private-stdio request/permission codec; supplies no send authority.

The owner must authenticate the actual child/process, bind source/quote/account,
persist the reservation, then send a matching reply over its private pipe.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, BinaryIO, cast

import httpx

from .wire import WireEstimate, WirePolicy

PROTOCOL = "official-smoke-parent-permit-v1"
MAX_FRAME_BYTES = 131072
MAX_BODY_BYTES = 64000


class InvalidControlFrame(ValueError):
    pass


class SendPermissionDenied(PermissionError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf8")


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidControlFrame("Duplicate control field")
        result[key] = value
    return result


def reject_constant(value: str) -> Any:
    raise InvalidControlFrame("Nonfinite control number")


def decode(raw: bytes) -> dict[str, Any]:
    if (
        type(raw) is not bytes
        or not raw.endswith(b"\n")
        or len(raw) > MAX_FRAME_BYTES
        or raw.count(b"\n") != 1
    ):
        raise InvalidControlFrame("Bounded single newline-terminated frame required")
    try:
        value = json.loads(
            raw.decode("utf8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        raise InvalidControlFrame("Strict UTF-8 control JSON required") from exc
    if (
        not isinstance(value, dict)
        or value.get("protocol") != PROTOCOL
        or type(value.get("seq")) is not int
        or value["seq"] <= 0
        or not isinstance(value.get("kind"), str)
        or not value["kind"]
    ):
        raise InvalidControlFrame("Control protocol/kind/sequence invalid")
    return value


def read_packet(reader: BinaryIO) -> dict[str, Any]:
    raw = reader.readline(MAX_FRAME_BYTES + 1)
    if raw == b"":
        raise EOFError("Private control pipe ended")
    return decode(raw)


def write_packet(writer: BinaryIO, value: dict[str, Any]) -> None:
    raw = canonical(value) + b"\n"
    decode(raw)
    if writer.write(raw) != len(raw):
        raise OSError("Partial private control write")
    writer.flush()


def hex_value(value: Any, length: int) -> bool:
    return (
        type(value) is str and len(value) == length and all(c in "0123456789abcdef" for c in value)
    )


def reservation_payload(
    request: httpx.Request,
    estimate: WireEstimate,
    *,
    expected_key: str,
    logical_id: str,
    nonce: str,
) -> dict[str, Any]:
    if not logical_id.startswith("completion-") or not hex_value(logical_id[11:], 32):
        raise InvalidControlFrame("Completion scope identity invalid")
    if not hex_value(nonce, 32) or type(expected_key) is not str or not expected_key:
        raise InvalidControlFrame("Private request identity invalid")
    body = request.content
    headers = [
        (key, value)
        for key, value in request.headers.multi_items()
        if key.lower() != "authorization"
    ]
    if (
        len(body) > MAX_BODY_BYTES
        or sha(body) != estimate.body_sha256
        or expected_key.encode() in body
        or any(expected_key in value for _, value in headers)
    ):
        raise InvalidControlFrame("Unbound or secret-bearing model input")
    return dict(
        logical_id=logical_id,
        nonce=nonce,
        request_sha256=estimate.request_sha256,
        body_sha256=estimate.body_sha256,
        method=request.method,
        url=str(request.url),
        headers=headers,
        authorization_sha256=sha(("Bearer " + expected_key).encode()),
        body_base64=base64.b64encode(body).decode("ascii"),
    )


def reconstruct(payload: dict[str, Any], *, expected_key: str) -> httpx.Request:
    expected = {
        "logical_id",
        "nonce",
        "request_sha256",
        "body_sha256",
        "method",
        "url",
        "headers",
        "authorization_sha256",
        "body_base64",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or type(payload["logical_id"]) is not str
        or not payload["logical_id"].startswith("completion-")
        or not hex_value(payload["logical_id"][11:], 32)
        or not hex_value(payload["nonce"], 32)
        or not hex_value(payload["request_sha256"], 64)
        or not hex_value(payload["body_sha256"], 64)
        or payload["authorization_sha256"] != sha(("Bearer " + expected_key).encode())
        or type(payload["body_base64"]) is not str
        or len(payload["body_base64"]) > 4 * ((MAX_BODY_BYTES + 2) // 3)
        or type(payload["method"]) is not str
        or type(payload["url"]) is not str
        or not isinstance(payload["headers"], list)
        or len(payload["headers"]) > 24
    ):
        raise InvalidControlFrame("Physical envelope identity differs")
    pairs = []
    for pair in payload["headers"]:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            or pair[0].lower() == "authorization"
            or len(pair[0]) > 128
            or len(pair[1].encode("utf8")) > 2048
            or expected_key in pair[1]
        ):
            raise InvalidControlFrame("Header envelope malformed")
        pairs.append((pair[0], pair[1]))
    try:
        body = base64.b64decode(payload["body_base64"], validate=True)
    except ValueError as exc:
        raise InvalidControlFrame("Canonical body encoding required") from exc
    if (
        len(body) > MAX_BODY_BYTES
        or sha(body) != payload["body_sha256"]
        or expected_key.encode() in body
    ):
        raise InvalidControlFrame("Body binding differs")
    return httpx.Request(
        payload["method"],
        payload["url"],
        content=body,
        headers=[*pairs, ("Authorization", "Bearer " + expected_key)],
    )


def validate_request(
    payload: dict[str, Any], policy: WirePolicy, *, expected_key: str, now: Any
) -> tuple[httpx.Request, WireEstimate]:
    request = reconstruct(payload, expected_key=expected_key)
    estimate = policy.validate(request, expected_key=expected_key, now=now)
    if estimate.request_sha256 != payload["request_sha256"]:
        raise InvalidControlFrame("Parent reconstructed a different physical envelope")
    return request, estimate


def check_permission(
    reply: dict[str, Any],
    *,
    seq: int,
    payload: dict[str, Any],
    plan_sha256: str,
    estimate: WireEstimate,
) -> str:
    if (
        set(reply)
        != {
            "protocol",
            "kind",
            "seq",
            "nonce",
            "request_sha256",
            "plan_sha256",
            "allowed",
            "reservation_row_hash",
            "reserve_cny",
            "reason",
        }
        or reply.get("protocol") != PROTOCOL
        or reply.get("kind") != "permission"
        or type(reply.get("seq")) is not int
        or reply["seq"] != seq
        or reply.get("nonce") != payload["nonce"]
        or reply.get("request_sha256") != estimate.request_sha256
        or reply.get("plan_sha256") != plan_sha256
        or not hex_value(plan_sha256, 64)
        or type(reply.get("allowed")) is not bool
        or not hex_value(reply.get("reservation_row_hash"), 64)
        or reply.get("reserve_cny") != str(estimate.retained_bound_cny)
        or type(reply.get("reason")) is not str
    ):
        raise InvalidControlFrame("Permission does not bind this exact pending request")
    if not reply["allowed"]:
        raise SendPermissionDenied("Parent refused the physical send")
    return cast(str, reply["reservation_row_hash"])
