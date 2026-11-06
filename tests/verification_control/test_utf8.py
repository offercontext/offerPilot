"""Serialized-byte reservation is scoped to the SDK's UTF-8 JSON representation."""

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from scripts.real_ai_control.wire import WirePolicy, WirePolicyDenied


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_non_utf8_json_is_not_an_admissible_sdk_request_representation(encoding):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    raw = json.dumps(
        dict(
            model="deepseek-flash",
            messages=[dict(role="user", content="合成")],
            max_tokens=1,
            temperature=0,
            thinking={"type": "disabled"},
        ),
        ensure_ascii=False,
    ).encode(encoding)
    request = httpx.Request(
        "POST",
        "https://api.deepseek.com/v1/chat/completions",
        content=raw,
        headers={"Authorization": "Bearer fake-test-key", "Content-Type": "application/json"},
    )
    with pytest.raises(WirePolicyDenied):
        WirePolicy(64000, 8192, "2", "8", now + timedelta(hours=1)).validate(
            request, expected_key="fake-test-key", now=now
        )
