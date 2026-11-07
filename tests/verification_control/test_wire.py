from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext, ROUND_DOWN
from fractions import Fraction
import json

import httpx
import pytest

from scripts.real_ai_control.wire import WirePolicy, WirePolicyDenied

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
KEY = "fake-private-test-key"


def policy():
    return WirePolicy(64000, 8192, "2", "8", NOW + timedelta(hours=1))


def payload():
    return dict(
        model="deepseek-flash",
        messages=[dict(role="user", content="合成资料")],
        max_tokens=1024,
        temperature=0,
        thinking={"type": "disabled"},
    )


def request(value=None, *, url="https://api.deepseek.com/v1/chat/completions", headers=None):
    return httpx.Request(
        "POST",
        url,
        json=payload() if value is None else value,
        headers={"Authorization": "Bearer " + KEY} if headers is None else headers,
    )


def test_estimate_uses_actual_utf8_bytes_and_explicit_output_cap_without_mutation():
    req = request()
    before = (req.content, list(req.headers.multi_items()))
    result = policy().validate(req, expected_key=KEY, now=NOW)
    assert result.input_bytes == len(req.content)
    assert (
        result.retained_bound_cny == (Decimal(len(req.content)) * 2 + Decimal(1024) * 8) / 1000000
    )
    assert result.output_cap == 1024 and result.parent_reservation_required is True
    assert result.physical_dispatch_permitted is False
    assert before == (req.content, list(req.headers.multi_items()))


@pytest.mark.parametrize(
    "url",
    [
        "https://outside.invalid/v1/chat/completions",
        "http://api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com:0/v1/chat/completions",
        "https://api.deepseek.com:444/v1/chat/completions",
        "https://user:pass@api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com/v1/chat/completions?key=1",
        "https://api.deepseek.com/v1/chat/completions#fragment",
        "https://api.deepseek.com/v1/chat%2fcompletions",
        "https://api.deepseek.com/v1/models",
    ],
)
def test_physical_endpoint_aliases_and_wrong_operations_are_denied(url):
    with pytest.raises(WirePolicyDenied):
        policy().validate(request(url=url), expected_key=KEY, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"model": "another-model"},
        {"max_tokens": True},
        {"max_tokens": 0},
        {"max_tokens": 8193},
        {"max_tokens": None},
        {"n": True},
        {"n": 2},
        {"temperature": False},
        {"temperature": 1},
        {"thinking": {"type": "enabled"}},
        {"fallbacks": ["another-model"]},
        {"messages": []},
        {"stream": "true"},
        {"stream": True},
        {"stream_options": {"include_usage": True}},
    ],
)
def test_unfrozen_model_output_retry_and_stream_shapes_are_denied(change):
    value = payload()
    value.update(change)
    with pytest.raises(WirePolicyDenied):
        policy().validate(request(value), expected_key=KEY, now=NOW)


def test_stream_usage_is_explicit_and_preserves_the_same_cost_bound():
    value = payload()
    value.update(stream=True, stream_options={"include_usage": True})
    result = policy().validate(request(value), expected_key=KEY, now=NOW)
    assert result.output_cap == 1024 and result.physical_dispatch_permitted is False


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer another-key"},
        [("Authorization", "Bearer " + KEY), ("Authorization", "Bearer " + KEY)],
        {"Authorization": "Bearer " + KEY, "Host": "outside.invalid"},
        {"Authorization": "Bearer " + KEY, "Proxy-Authorization": "secret"},
        {"Authorization": "Bearer " + KEY, "Content-Encoding": "gzip"},
    ],
)
def test_wire_credentials_and_duplicate_headers_are_not_inferred_from_configuration(headers):
    with pytest.raises(WirePolicyDenied) as caught:
        policy().validate(request(headers=headers), expected_key=KEY, now=NOW)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "field,price",
    [
        ("input_cny_per_million", "NaN"),
        ("output_cny_per_million", "0"),
        ("input_cny_per_million", True),
        ("output_cny_per_million", "Infinity"),
        ("input_cny_per_million", "not-a-price"),
    ],
)
def test_bad_prices_cannot_look_like_free_or_finite_permission(field, price):
    with pytest.raises(WirePolicyDenied):
        replace(policy(), **{field: price}).validate(request(), expected_key=KEY, now=NOW)


def test_expired_or_unscoped_time_never_allows_a_request():
    for p, now in [
        (policy(), NOW + timedelta(hours=1)),
        (policy(), NOW.replace(tzinfo=None)),
        (replace(policy(), price_valid_until=NOW.replace(tzinfo=None)), NOW),
    ]:
        with pytest.raises(WirePolicyDenied):
            p.validate(request(), expected_key=KEY, now=now)


def test_duplicate_json_output_member_and_nonfinite_values_are_denied():
    raw = (
        json.dumps(payload(), separators=(",", ":"))
        .replace('"max_tokens":1024', '"max_tokens":1024,"max_tokens":900000')
        .encode()
    )
    for value in [raw, b'{"temperature":NaN}']:
        req = httpx.Request(
            "POST",
            "https://api.deepseek.com/v1/chat/completions",
            content=value,
            headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        )
        with pytest.raises(WirePolicyDenied):
            policy().validate(req, expected_key=KEY, now=NOW)


def test_input_limit_checks_the_encoded_wire_body():
    req = request()
    with pytest.raises(WirePolicyDenied):
        replace(policy(), max_input_bytes=len(req.content) - 1).validate(
            req, expected_key=KEY, now=NOW
        )


def test_unread_request_stream_is_rejected_without_consumption():
    class Unread(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Validator consumed a stream")

    req = httpx.Request(
        "POST",
        "https://api.deepseek.com/v1/chat/completions",
        stream=Unread(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    with pytest.raises(WirePolicyDenied):
        policy().validate(req, expected_key=KEY, now=NOW)


@pytest.mark.parametrize("price", ["2", "0.123456789012", "999999.999999999999"])
def test_reservation_is_a_positive_upper_bound_independent_of_ambient_decimal_context(price):
    req = request()
    exact = (len(req.content) * Fraction(price) + 1024 * Fraction("8")) / 1000000
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.Emax = 2
        context.Emin = 0
        result = replace(policy(), input_cny_per_million=price).validate(
            req, expected_key=KEY, now=NOW
        )
    assert exact <= Fraction(result.retained_bound_cny) < exact + Fraction(1, 1000000)
    assert result.retained_bound_cny > 0


@pytest.mark.parametrize("price", ["1e-999999", "1e999999", "0.1234567890123", "1000000"])
def test_unbounded_price_precision_and_exponents_are_refused(price):
    with pytest.raises(WirePolicyDenied):
        replace(policy(), input_cny_per_million=price).validate(
            request(), expected_key=KEY, now=NOW
        )


class Offsetless(tzinfo):
    def utcoffset(self, value):
        return None


class BrokenOffset(tzinfo):
    def utcoffset(self, value):
        raise ValueError("invalid test timezone")


@pytest.mark.parametrize("zone", [Offsetless(), BrokenOffset()])
def test_nonempty_but_invalid_timezone_cannot_authorize_price_freshness(zone):
    with pytest.raises(WirePolicyDenied):
        replace(policy(), price_valid_until=NOW.replace(tzinfo=zone) + timedelta(hours=1)).validate(
            request(), expected_key=KEY, now=NOW.replace(tzinfo=zone)
        )


@pytest.mark.parametrize("field", ["now", "expiry"])
def test_non_datetime_clock_values_are_refused(field):
    with pytest.raises(WirePolicyDenied):
        replace(policy(), price_valid_until="2030" if field == "expiry" else NOW).validate(
            request(), expected_key=KEY, now="2030" if field == "now" else NOW
        )


@pytest.mark.parametrize(
    "extra",
    [
        [("X-API-Key", "unexpected")],
        [("Cookie", "session=unexpected")],
        [("Transfer-Encoding", "chunked")],
        [("Content-Length", "1")],
        [("Content-Length", "123"), ("Content-Length", "123")],
        [("Content-Type", "application/json"), ("Content-Type", "application/json")],
        [("X-Stainless-API-Key", "unexpected")],
    ],
)
def test_alternate_credentials_and_ambiguous_framing_are_refused(extra):
    req = request()
    headers = list(req.headers.multi_items())
    if any(name.lower() in {"content-length", "content-type"} for name, _ in extra):
        headers = [
            (key, value)
            for key, value in headers
            if key.lower() not in {name.lower() for name, _ in extra}
        ]
    changed = httpx.Request("POST", str(req.url), content=req.content, headers=headers + extra)
    with pytest.raises(WirePolicyDenied):
        policy().validate(changed, expected_key=KEY, now=NOW)


def test_body_hash_and_validated_request_envelope_have_separate_identities():
    first = request()
    other = request(headers={"Authorization": "Bearer " + KEY, "User-Agent": "offline-fixture"})
    a = policy().validate(first, expected_key=KEY, now=NOW)
    b = policy().validate(other, expected_key=KEY, now=NOW)
    assert a.body_sha256 == b.body_sha256
    assert a.request_sha256 != b.request_sha256


@pytest.mark.parametrize("flag", ["true", "stream", "raw"])
def test_documented_sdk_raw_response_metadata_does_not_change_cost(flag):
    base = policy().validate(request(), expected_key=KEY, now=NOW)
    result = policy().validate(
        request(
            headers={
                "Authorization": "Bearer " + KEY,
                "X-Stainless-Raw-Response": flag,
                "X-Stainless-Retry-Count": "0",
                "X-Stainless-Read-Timeout": "45.0",
            }
        ),
        expected_key=KEY,
        now=NOW,
    )
    assert base.body_sha256 == result.body_sha256
    assert base.retained_bound_cny == result.retained_bound_cny
    assert base.request_sha256 != result.request_sha256


@pytest.mark.parametrize(
    "name,value",
    [
        ("X-Stainless-Retry-Count", "1"),
        ("X-Stainless-Retry-Count", "-1"),
        ("X-Stainless-Raw-Response", "unknown"),
        ("X-Stainless-Read-Timeout", "46"),
        ("X-Stainless-Read-Timeout", "NaN"),
        ("X-Stainless-Read-Timeout", "Infinity"),
        ("X-Stainless-Read-Timeout", "0"),
    ],
)
def test_sdk_retry_and_unbounded_timeout_metadata_cannot_pass(name, value):
    with pytest.raises(WirePolicyDenied):
        policy().validate(
            request(headers={"Authorization": "Bearer " + KEY, name: value}),
            expected_key=KEY,
            now=NOW,
        )
