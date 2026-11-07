"""Pure run-control parameter preparation; no dispatch, account, or authority.

An actual child bridge still needs durable parent permission at every physical
HTTP send. These parameters are an explicitly bounded verification configuration,
not evidence about the product's unbounded ordinary defaults.
"""

from __future__ import annotations

from decimal import Decimal
import math
from typing import Any
from urllib.parse import urlsplit


def endpoint(value: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Credential-free approved HTTPS endpoint required")
    port = parsed.port if parsed.port is not None else 443
    if port <= 0:
        raise ValueError("Positive HTTPS port required")
    return parsed.scheme, parsed.hostname, port, parsed.path.rstrip("/")


def bounded_payload(
    payload: dict[str, Any], profile: dict[str, Any], *, key: str, sdk_client: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    if any(
        name in payload
        for name in (
            "fallbacks",
            "context_window_fallbacks",
            "content_policy_fallbacks",
            "model_list",
        )
    ):
        raise ValueError("Caller fallback/routing arguments are outside the frozen run")
    model = profile["model"]
    base = profile["endpoint"]
    if (
        model != "deepseek-flash"
        or endpoint(base) != endpoint("https://api.deepseek.com/v1")
        or profile.get("fallback") != []
        or payload.get("model") not in {model, "openai/" + model}
        or payload.get("api_key") != key
        or not key
        or endpoint(str(payload.get("api_base") or "")) != endpoint(base)
    ):
        raise ValueError("Frozen Provider identity or fallback differs")
    limit = profile["max_output_tokens"]
    timeout = profile["timeout_seconds"]
    if (
        type(limit) is not int
        or not 0 < limit <= 8192
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 45
    ):
        raise ValueError("Finite output and original Provider timeout bounds required")
    if (
        type(getattr(sdk_client, "max_retries", None)) is not int
        or sdk_client.max_retries != 0
        or getattr(sdk_client, "api_key", None) != key
        or endpoint(str(getattr(sdk_client, "base_url", ""))) != endpoint(base)
    ):
        raise ValueError("Explicit matching zero-retry SDK client required")
    output = limit
    for name in ("max_tokens", "max_completion_tokens"):
        value = payload.get(name)
        if value is not None:
            if type(value) is not int or value <= 0:
                raise ValueError("Explicit caller output bound is invalid")
            output = min(output, value)
    caller_timeout = payload.get("timeout")
    if caller_timeout is not None:
        if (
            type(caller_timeout) not in {int, float}
            or not math.isfinite(caller_timeout)
            or caller_timeout <= 0
        ):
            raise ValueError("Caller timeout cannot be represented safely")
        timeout = min(timeout, caller_timeout)
    if type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1:
        raise ValueError("Multiple completions are outside the single-send reserve")
    stream = payload.get("stream", False)
    if type(stream) is not bool:
        raise ValueError("Explicit stream shape required")
    body = payload.get("extra_body")
    if body is None:
        body = {}
    if not isinstance(body, dict) or set(body) - {"thinking"}:
        raise ValueError("Unfrozen extra body could override wire limits")
    result = dict(payload)
    result.pop("max_completion_tokens", None)
    result.update(
        client=sdk_client,
        max_tokens=output,
        timeout=timeout,
        num_retries=0,
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    if stream:
        result["stream_options"] = {"include_usage": True}
    else:
        result.pop("stream_options", None)
    differences = dict(
        max_tokens=dict(before=payload.get("max_tokens"), after=output),
        max_completion_tokens_removed="max_completion_tokens" in payload,
        timeout=dict(before=caller_timeout, after=timeout),
        temperature=dict(before=payload.get("temperature"), after=0),
        thinking=dict(before=body.get("thinking"), after={"type": "disabled"}),
        automatic_transport_retries=0,
        fallback_allowed=False,
        verification_configuration_only=True,
        physical_send_gate_installed=None,
        this_helper_installs_physical_gate=False,
    )
    return result, differences


def reserve_cny(input_bytes: int, output_cap: int, input_price: str, output_price: str) -> Decimal:
    if (
        type(input_bytes) is not int
        or input_bytes < 0
        or type(output_cap) is not int
        or output_cap <= 0
    ):
        raise ValueError("Actual serialized bytes and positive bounded output required")
    if any(type(value) is not str or not value for value in (input_price, output_price)):
        raise ValueError("Explicit decimal price strings required")
    prices = [Decimal(value) for value in (input_price, output_price)]
    if any(not value.is_finite() or value <= 0 for value in prices):
        raise ValueError("Positive finite CNY prices required")
    return (Decimal(input_bytes) * prices[0] + Decimal(output_cap) * prices[1]) / Decimal(1000000)
