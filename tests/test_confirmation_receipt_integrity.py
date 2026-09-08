"""A receipt policy cannot be stripped or moved to another terminal result."""

from __future__ import annotations

import json

import pytest

from offerpilot.ai.confirmation_receipt import EDITED_CONFIRMATION_RECEIPT_STRATEGY
from offerpilot.ai.write_operations import (
    LedgerKeyDomain,
    WriteOperationError,
    build_terminal_payload,
    confirmation_strategy_fingerprint,
    payload_from_operation,
)
from offerpilot.models import WriteOperation


KEY = LedgerKeyDomain("receipt-integrity-fixture", b"synthetic-test-key-32-bytes-long!!")


def _operation(*, edited: bool = True) -> WriteOperation:
    transport = {
        "confirmation_strategy_version": EDITED_CONFIRMATION_RECEIPT_STRATEGY,
        "confirmation_strategy_fields": ["signing_bonus"],
    } if edited else {}
    payload = build_terminal_payload(
        status="committed", result_contract="typed_json_v1", result={"signing_bonus": 8000},
        visible_result="saved", transport=transport, undo=None,
        failure_category=None, failure_code=None,
    )
    operation = WriteOperation(
        id="receipt-operation", operation_request_fingerprint="request-fingerprint",
        status=payload.status, result_contract=payload.result_contract,
        result_json=payload.result_json, visible_result=payload.visible_result,
        transport_json=payload.transport_json, undo_json=payload.undo_json,
        failure_category=None, failure_code=None, terminal_payload_sha256=payload.digest,
        confirmation_strategy_version=EDITED_CONFIRMATION_RECEIPT_STRATEGY if edited else None,
        confirmation_strategy_fields_json='["signing_bonus"]' if edited else None,
        confirmation_strategy_fingerprint=confirmation_strategy_fingerprint(
            KEY, operation_id="receipt-operation", request_fingerprint="request-fingerprint",
            terminal_payload_sha256=payload.digest,
            strategy_version=EDITED_CONFIRMATION_RECEIPT_STRATEGY, fields=("signing_bonus",),
        ) if edited else None,
    )
    return operation


@pytest.mark.parametrize("edited", [True, False])
def test_original_and_legacy_terminal_payloads_remain_readable(edited: bool) -> None:
    operation = _operation(edited=edited)
    assert payload_from_operation(operation, key=KEY).result_json == '{"signing_bonus":8000}'


@pytest.mark.parametrize(("field", "value"), [
    ("confirmation_strategy_version", None),
    ("confirmation_strategy_version", "future_strategy_v2"),
    ("confirmation_strategy_fields_json", None),
    ("confirmation_strategy_fields_json", "[]"),
    ("confirmation_strategy_fields_json", "null"),
    ("confirmation_strategy_fields_json", "{"),
    ("confirmation_strategy_fields_json", '["perks"]'),
    ("confirmation_strategy_fields_json", '["signing_bonus","signing_bonus"]'),
    ("confirmation_strategy_fingerprint", None),
    ("confirmation_strategy_fingerprint", "hmac-sha256:wrong"),
    ("operation_request_fingerprint", "different-request"),
    ("operation_request_fingerprint", None),
    ("id", "different-operation"),
])
def test_partial_or_substituted_policy_fails_closed(field: str, value: str | None) -> None:
    operation = _operation()
    setattr(operation, field, value)
    with pytest.raises(WriteOperationError, match="operation_integrity_error"):
        payload_from_operation(operation, key=KEY)


def test_removing_all_policy_columns_does_not_disguise_an_edited_write_as_legacy() -> None:
    operation = _operation()
    operation.confirmation_strategy_version = None
    operation.confirmation_strategy_fields_json = None
    operation.confirmation_strategy_fingerprint = None
    with pytest.raises(WriteOperationError, match="operation_integrity_error"):
        payload_from_operation(operation, key=KEY)


def test_recomputed_unkeyed_terminal_digest_cannot_change_the_receipt_fact() -> None:
    operation = _operation()
    replacement = build_terminal_payload(
        status="committed", result_contract="typed_json_v1", result={"signing_bonus": 10000},
        visible_result=operation.visible_result,
        transport=json.loads(operation.transport_json), undo=None,
        failure_category=None, failure_code=None,
    )
    operation.result_json = replacement.result_json
    operation.terminal_payload_sha256 = replacement.digest
    with pytest.raises(WriteOperationError, match="operation_integrity_error"):
        payload_from_operation(operation, key=KEY)
