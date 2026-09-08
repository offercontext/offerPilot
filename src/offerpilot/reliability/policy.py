"""Contract-driven recovery lookups for API and harness code."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from offerpilot.reliability.recovery_policy_generated import (
    NETWORK_TRANSPORT_POLICY,
    RECOVERY_POLICIES,
    UNKNOWN_CODE_DISPOSITION,
    UNKNOWN_CODE_POLICY,
    RecoveryPolicyEntry,
)


def get_recovery_policy(error_code: str) -> RecoveryPolicyEntry | None:
    """Return the contract entry for an error code, or None when unknown."""
    return RECOVERY_POLICIES.get(error_code)


def require_recovery_policy(error_code: str) -> RecoveryPolicyEntry:
    """Return the contract entry or fail loudly: emitted codes must be covered."""
    entry = RECOVERY_POLICIES.get(error_code)
    if entry is None:
        raise KeyError(
            f"error code {error_code!r} is not covered by contracts/recovery-policy.v1.json"
        )
    return entry


def recovery_disposition(error_code: str) -> str:
    """Disposition for a response error code; unknown codes fail closed."""
    entry = get_recovery_policy(error_code)
    if entry is not None:
        return entry.disposition
    return str(UNKNOWN_CODE_POLICY["disposition"])


def provider_retry_allowed(error_code: str) -> bool:
    """Whether the contract permits a (lease-bounded) provider retry."""
    entry = get_recovery_policy(error_code)
    if entry is not None:
        return entry.provider_retry_allowed
    return bool(UNKNOWN_CODE_POLICY["provider_retry_allowed"])


def network_transport_disposition() -> str:
    """Disposition used when a request failed before any server response."""
    return str(NETWORK_TRANSPORT_POLICY["disposition"])


def fail_closed_disposition() -> str:
    return str(UNKNOWN_CODE_DISPOSITION)


def recovery_error(
    error_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build an error response whose status and code come from the contract."""
    entry = require_recovery_policy(error_code)
    payload: dict[str, Any] = {"error": message, "error_code": error_code}
    if details:
        payload.update(details)
    return JSONResponse(payload, status_code=entry.http_status)
