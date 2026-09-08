"""Mock Interview reliability contract adapters.

The single source of truth is contracts/recovery-policy.v1.json; this package
exposes the generated policy map plus the sanitized trace envelope used by the
API, the harness, and diagnostics. Consumers must never guess a recovery action
from an HTTP status code.
"""

from offerpilot.reliability.policy import (
    get_recovery_policy,
    provider_retry_allowed,
    recovery_disposition,
    recovery_error,
    require_recovery_policy,
)
from offerpilot.reliability.trace import (
    TRACE_FIELDS,
    MockInterviewTraceEnvelope,
    hash_idempotency_key,
    read_mock_interview_traces,
    record_mock_interview_trace,
)

__all__ = [
    "TRACE_FIELDS",
    "MockInterviewTraceEnvelope",
    "get_recovery_policy",
    "hash_idempotency_key",
    "provider_retry_allowed",
    "read_mock_interview_traces",
    "record_mock_interview_trace",
    "recovery_disposition",
    "recovery_error",
    "require_recovery_policy",
]
