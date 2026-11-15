"""Pure physical-request validation/estimate; supplies no dispatch authority.

The real CLI still needs an owned process, authenticated parent reservation ACK,
per-logical-call send fuse, scope-complete API evidence and conservative closeout.
Importing or validating this component never reads credentials or sends traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_CEILING, localcontext
import hashlib
import json
import re
from typing import Any

import httpx


class WirePolicyDenied(ValueError):
    pass


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WirePolicyDenied("Duplicate JSON member refused")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise WirePolicyDenied("Nonfinite JSON value refused")


@dataclass(frozen=True)
class WireEstimate:
    input_bytes: int
    output_cap: int
    request_sha256: str
    retained_bound_cny: Decimal
    body_sha256: str
    parent_reservation_required: bool = True
    physical_dispatch_permitted: bool = False


@dataclass(frozen=True)
class WirePolicy:
    max_input_bytes: int
    max_output_tokens: int
    input_cny_per_million: str
    output_cny_per_million: str
    price_valid_until: datetime
    model: str = "deepseek-flash"

    def validate(self, request: httpx.Request, *, expected_key: str, now: datetime) -> WireEstimate:
        try:
            aware_time = (
                isinstance(self.price_valid_until, datetime)
                and isinstance(now, datetime)
                and self.price_valid_until.utcoffset() is not None
                and now.utcoffset() is not None
                and now.astimezone(timezone.utc) < self.price_valid_until.astimezone(timezone.utc)
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise WirePolicyDenied("Globally defined unexpired quote time required") from exc
        if (
            self.model != "deepseek-flash"
            or type(expected_key) is not str
            or not expected_key
            or type(self.max_input_bytes) is not int
            or not 0 < self.max_input_bytes <= 64000
            or type(self.max_output_tokens) is not int
            or not 0 < self.max_output_tokens <= 8192
            or not aware_time
        ):
            raise WirePolicyDenied("Frozen bounds or unexpired aware price required")
        url = request.url
        if (
            request.method != "POST"
            or url.scheme != "https"
            or url.host != "api.deepseek.com"
            or (url.port if url.port is not None else 443) != 443
            or url.raw_path != b"/v1/chat/completions"
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise WirePolicyDenied("Physical destination outside frozen Provider")
        if (
            request.headers.get_list("authorization") != ["Bearer " + expected_key]
            or request.headers.get("host") not in {"api.deepseek.com", "api.deepseek.com:443"}
            or "proxy-authorization" in request.headers
            or "content-encoding" in request.headers
            or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
        ):
            raise WirePolicyDenied("Physical credentials or representation differ")
        try:
            raw = request.content  # Refuse unread iterators; do not consume/mutate them.
        except httpx.RequestNotRead as exc:
            raise WirePolicyDenied("Serialized request body required") from exc
        if not raw or len(raw) > self.max_input_bytes:
            raise WirePolicyDenied("Physical input exceeds frozen byte bound")
        allowed_headers = {
            "authorization",
            "host",
            "content-type",
            "content-length",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
            "x-stainless-lang",
            "x-stainless-package-version",
            "x-stainless-os",
            "x-stainless-arch",
            "x-stainless-runtime",
            "x-stainless-runtime-version",
            "x-stainless-async",
            "x-stainless-retry-count",
            "x-stainless-read-timeout",
            "x-stainless-helper-method",
            "x-stainless-raw-response",
        }
        header_pairs = request.headers.multi_items()
        names = [key.lower() for key, _ in header_pairs]
        if (
            set(names) - allowed_headers
            or len(names) != len(set(names))
            or request.headers.get_list("content-length") != [str(len(raw))]
            or any(
                len(value.encode("utf8")) > 2048 or "\r" in value or "\n" in value
                for _, value in header_pairs
            )
        ):
            raise WirePolicyDenied("Closed credential and exact body framing envelope required")
        if (
            "x-stainless-retry-count" in request.headers
            and request.headers["x-stainless-retry-count"] != "0"
        ):
            raise WirePolicyDenied("An SDK-reported retry is outside a first-send request")
        if "x-stainless-raw-response" in request.headers and request.headers[
            "x-stainless-raw-response"
        ] not in {"true", "stream", "raw"}:
            raise WirePolicyDenied("Unknown SDK response representation flag")
        read_timeout = request.headers.get("x-stainless-read-timeout")
        if read_timeout is not None and (
            re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,6})?", read_timeout) is None
            or not 0 < Decimal(read_timeout) <= 45
        ):
            raise WirePolicyDenied("SDK-reported read timeout exceeds original Provider bound")
        try:
            payload = json.loads(
                raw.decode("utf8"),
                object_pairs_hook=_unique_pairs,
                parse_constant=_invalid_constant,
            )
        except (ValueError, UnicodeError) as exc:
            raise WirePolicyDenied("Canonical bounded JSON request required") from exc
        allowed = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "stream",
            "stream_options",
            "max_tokens",
            "temperature",
            "thinking",
            "n",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) - allowed
            or payload.get("model") != self.model
            or not isinstance(payload.get("messages"), list)
            or not payload["messages"]
            or type(payload.get("max_tokens")) is not int
            or not 0 < payload["max_tokens"] <= self.max_output_tokens
            or type(payload.get("n", 1)) is not int
            or payload.get("n", 1) != 1
            or type(payload.get("temperature")) not in {int, float}
            or payload["temperature"] != 0
            or payload.get("thinking") != {"type": "disabled"}
            or type(payload.get("stream", False)) is not bool
        ):
            raise WirePolicyDenied("Physical model, payload or output bounds differ")
        if (
            payload.get("stream") is True
            and payload.get("stream_options") != {"include_usage": True}
        ) or (payload.get("stream", False) is False and "stream_options" in payload):
            raise WirePolicyDenied("Frozen stream usage contract required")
        raw_prices = (self.input_cny_per_million, self.output_cny_per_million)
        if any(
            type(value) is not str
            or re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,12})?", value) is None
            for value in raw_prices
        ):
            raise WirePolicyDenied("Bounded fixed-decimal quote strings required")
        try:
            prices = [Decimal(value) for value in raw_prices]
        except ArithmeticError as exc:
            raise WirePolicyDenied("Finite positive price required") from exc
        if any(not value.is_finite() or value <= 0 for value in prices):
            raise WirePolicyDenied("Finite positive price required")
        # Bounded operands need fewer than 32 significant digits. Never inherit
        # caller/SDK precision, rounding, traps, Emin or Emax settings.
        with localcontext(Context(prec=40, rounding=ROUND_CEILING)):
            retained = (
                (Decimal(len(raw)) * prices[0] + Decimal(payload["max_tokens"]) * prices[1])
                / Decimal(1000000)
            ).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
        body_hash = hashlib.sha256(raw).hexdigest()
        envelope = dict(
            method=request.method,
            url=str(url),
            body_sha256=body_hash,
            header_value_hashes=sorted(
                (key.lower(), hashlib.sha256(value.encode("utf8")).hexdigest())
                for key, value in header_pairs
            ),
        )
        envelope_hash = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf8")
        ).hexdigest()
        return WireEstimate(len(raw), payload["max_tokens"], envelope_hash, retained, body_hash)
