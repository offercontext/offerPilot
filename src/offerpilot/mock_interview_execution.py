from __future__ import annotations

# 跨层定位见 docs/architecture/code-map/interview-mock.md。
# 两种入口共用 Provider -> 条件提交 -> trace；来源准入和 claim 仍由
# handler/Repository 负责，因此不能在执行失败后自行新建一次 Attempt。

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Literal
from uuid import uuid4

from offerpilot.ai.agent_contracts import ChatModel
from offerpilot.ai.client import ConfiguredAIClient
from offerpilot.ai.mock_interview import (
    MOCK_INTERVIEW_FEEDBACK_SCHEMA,
    MOCK_INTERVIEW_QUESTION_OUTPUT_SCHEMA,
    MockInterviewProviderError,
    MockInterviewUnverifiableError,
    generate_feedback,
    generate_question,
)
from offerpilot.config import load_config
from offerpilot.diagnostics import append_log_entry
from offerpilot.models import (
    MockInterviewAttempt,
    MockInterviewFeedbackProposal,
    MockInterviewTurn,
)
from offerpilot.repositories.mock_interviews import (
    MockInterviewFeedbackClaim,
    MockInterviewQuestionClaim,
    MockInterviewRepository,
    MockInterviewSourceChanged,
    provider_mock_interview_snapshot,
)
from offerpilot.reliability.policy import recovery_disposition
from offerpilot.reliability.trace import (
    MockInterviewTraceEnvelope,
    hash_idempotency_key,
    record_mock_interview_trace,
)

_MOCK_INTERVIEW_TRACE_RUN_ID = uuid4().hex


@dataclass(frozen=True)
class MockInterviewExecutionContext:
    """Presentation differences only; source/claim authority stays in the repository."""

    kind: Literal["quick_practice", "application_event"]

    def provider_failure(self, stage: str) -> tuple[str, str]:
        if self.kind == "application_event":
            return "mock_interview_provider_error", "AI service is temporarily unavailable"
        if stage == "question":
            return "mock_interview_question_result_unknown", "下一题结果待确认，请使用原 key 恢复。"
        return "mock_interview_feedback_result_unknown", "复盘结果待确认，请使用原 key 恢复。"

    @property
    def source_conflict(self) -> str:
        if self.kind == "quick_practice":
            return "本次练习使用的冻结资料不可验证。"
        return "mock interview frozen source changed"

    @property
    def unverifiable(self) -> str:
        if self.kind == "quick_practice":
            return "AI 输出未通过验证，请重新开始本次练习。"
        return "mock interview output could not be verified; please start a new attempt"

    def transcript_conflict(self, stage: str, *, initial: bool = False) -> str:
        if self.kind == "application_event":
            return "mock interview transcript changed"
        if stage == "feedback":
            return "复盘写入状态已变化，请使用原 key 对账。"
        return "快速练习回答状态已变化。" if initial else "下一题写入状态已变化。"


QUICK_PRACTICE_EXECUTION = MockInterviewExecutionContext("quick_practice")
APPLICATION_EVENT_EXECUTION = MockInterviewExecutionContext("application_event")


class MockInterviewExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, attempt_id: int, operation_id: str) -> None:
        super().__init__(message)
        self.code = code
        self.details = {"attempt_id": attempt_id, "operation_id": operation_id}


@dataclass(frozen=True)
class QuestionExecutionResult:
    attempt: MockInterviewAttempt
    turn: MockInterviewTurn
    operation_id: str


@dataclass(frozen=True)
class FeedbackExecutionResult:
    record: MockInterviewFeedbackProposal
    created: bool
    operation_id: str


@dataclass
class _ExecutionTrace:
    data_dir: Path
    context: MockInterviewExecutionContext
    attempt: MockInterviewAttempt
    revision: int
    key: str
    stage: Literal["question", "feedback"]
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timer: float = field(default_factory=perf_counter)
    model: ChatModel | None = None

    def emit(
        self,
        outcome: str,
        diagnostic: dict[str, Any],
        *,
        code: str = "",
        category: str = "",
        elapsed_ms: int | None = None,
        input_fingerprint: str | None = None,
    ) -> None:
        _emit_mock_interview_trace(
            self.data_dir,
            scenario_id=f"mock_interview:{self.context.kind}:{self.stage}",
            operation_id=self.operation_id,
            attempt_id=self.attempt.id,
            generation_revision=self.revision,
            idempotency_key=self.key,
            model=self.model,
            input_fingerprint=(
                self.attempt.source_fingerprint if input_fingerprint is None else input_fingerprint
            ),
            schema_fingerprint=(
                _mock_interview_question_schema_fingerprint()
                if self.stage == "question"
                else _mock_interview_feedback_schema_fingerprint()
            ),
            started_at=self.started_at,
            elapsed_ms=(
                int((perf_counter() - self.timer) * 1000) if elapsed_ms is None else elapsed_ms
            ),
            provider_outcome=outcome,
            validator_stage=self.stage,
            failure_category=category,
            repair_count=int(diagnostic.get("repair_count") or 0),
            response_error_code=code,
        )

    def error(self, code: str, message: str) -> MockInterviewExecutionError:
        return MockInterviewExecutionError(code, message, self.attempt.id, self.operation_id)


class MockInterviewExecutionService:
    """Execute only an owned claim; no source selection, new claims, retries or HTTP routing.

    Both products keep their context-specific admission and replay rules. This
    finite kernel owns the provider -> fenced commit -> safe trace/failure path.
    """

    def __init__(
        self,
        repository: MockInterviewRepository,
        data_dir: Path,
        load_model: Callable[[], ChatModel | None],
    ) -> None:
        self.repository = repository
        self.data_dir = data_dir
        self.load_model = load_model

    def question(
        self,
        attempt: MockInterviewAttempt,
        turn_no: int,
        claim: MockInterviewQuestionClaim,
        key: str,
        context: MockInterviewExecutionContext,
        *,
        initial: bool = False,
    ) -> QuestionExecutionResult:
        trace = _ExecutionTrace(self.data_dir, context, attempt, claim.revision, key, "question")
        diagnostic: dict[str, Any] = {}
        try:
            trace.model = self.load_model()
            if trace.model is None:
                raise MockInterviewProviderError("mock_interview_provider_error")
            result, diagnostic = generate_question(
                trace.model,
                provider_mock_interview_snapshot(attempt),
                list(claim.turns),
            )
            # Provider 成功与业务提交成功分别观察：迟到结果可能无法落库。
            completed = self.repository.complete_question(
                attempt.id,
                turn_no,
                claim.revision,
                claim.provider_call_token,
                claim.transcript_fingerprint,
                result["question"],
                result["evidence_refs"],
            )
            if completed is None:
                trace.emit("success", diagnostic, code="mock_interview_transcript_conflict")
                raise trace.error(
                    "mock_interview_transcript_conflict",
                    context.transcript_conflict("question", initial=initial),
                )
            current = self.repository.get_turn(attempt.id, turn_no)
            assert current is not None
            trace.emit(
                "success_after_repair" if diagnostic.get("repair_count") else "success",
                diagnostic,
                elapsed_ms=int(diagnostic.get("elapsed_ms") or 0),
                input_fingerprint=completed.source_fingerprint,
            )
            return QuestionExecutionResult(completed, current, trace.operation_id)
        except (MockInterviewProviderError, MockInterviewUnverifiableError) as exc:
            raise self._finalize_failure(trace, claim.provider_call_token, exc) from exc
        except MockInterviewSourceChanged as exc:
            trace.emit(
                "success",
                diagnostic,
                code="mock_interview_source_conflict",
                category="source_conflict",
            )
            raise trace.error("mock_interview_source_conflict", context.source_conflict) from exc

    def feedback(
        self,
        attempt: MockInterviewAttempt,
        claim: MockInterviewFeedbackClaim,
        key: str,
        context: MockInterviewExecutionContext,
    ) -> FeedbackExecutionResult:
        trace = _ExecutionTrace(self.data_dir, context, attempt, claim.revision, key, "feedback")
        try:
            snapshot = provider_mock_interview_snapshot(attempt)
            trace.model = self.load_model()
            proposal, diagnostic = generate_feedback(trace.model, snapshot, list(claim.turns))
        except (MockInterviewProviderError, MockInterviewUnverifiableError) as exc:
            raise self._finalize_failure(trace, claim.provider_call_token, exc) from exc
        outcome = "success_after_repair" if diagnostic.get("repair_count") else "success"
        try:
            record, created = self.repository.complete_feedback(
                attempt.id,
                key,
                claim.revision,
                claim.provider_call_token,
                claim.transcript_fingerprint,
                proposal,
                proposal["proposal_status"],
                str(diagnostic.get("failure_category", "")),
            )
        except MockInterviewSourceChanged as exc:
            trace.emit(outcome, diagnostic, code="mock_interview_source_conflict")
            raise trace.error("mock_interview_source_conflict", context.source_conflict) from exc
        if record is None:
            replay, _ = self.repository.get_feedback(attempt.id, key)
            if replay is not None:
                trace.emit(outcome, diagnostic)
                return FeedbackExecutionResult(replay, False, trace.operation_id)
            trace.emit(outcome, diagnostic, code="mock_interview_transcript_conflict")
            raise trace.error(
                "mock_interview_transcript_conflict",
                context.transcript_conflict("feedback"),
            )
        trace.emit(outcome, diagnostic)
        return FeedbackExecutionResult(record, created, trace.operation_id)

    def _finalize_failure(
        self,
        trace: _ExecutionTrace,
        provider_token: str,
        exc: MockInterviewProviderError | MockInterviewUnverifiableError,
    ) -> MockInterviewExecutionError:
        provider_failure = isinstance(exc, MockInterviewProviderError)
        _log_mock_interview_ai_failure(
            self.data_dir,
            attempt_id=trace.attempt.id,
            stage=trace.stage,
            kind="provider" if provider_failure else "contract",
            diagnostic=exc.diagnostic,
        )
        if provider_failure:
            finalization = _mark_mock_interview_failure_state(
                lambda: self.repository.mark_provider_unknown(
                    trace.attempt.id,
                    trace.revision,
                    provider_token,
                    trace.stage,
                )
            )
            default_code, default_message = trace.context.provider_failure(trace.stage)
            outcome = "provider_error"
            category = str(exc.diagnostic.get("failure_category") or "provider_error")
        else:
            category = "contract_unverifiable" if trace.stage == "feedback" else exc.category
            finalization = _mark_mock_interview_failure_state(
                lambda: self.repository.mark_contract_failure(
                    trace.attempt.id,
                    trace.revision,
                    provider_token,
                    category,
                    "contract_failed",
                )
            )
            default_code, default_message = (
                "mock_interview_unverifiable",
                trace.context.unverifiable,
            )
            outcome = "unverifiable"
            category = str(exc.category)
        code = _mock_interview_failure_response_code(finalization, default_code)
        trace.emit(outcome, exc.diagnostic, code=code, category=category)
        message = (
            default_message if code == default_code else _mock_interview_failure_message(code, "")
        )
        return trace.error(code, message)


def _mock_interview_question_schema_fingerprint() -> str:
    return (
        "qschema-"
        + hashlib.sha256(
            json.dumps(MOCK_INTERVIEW_QUESTION_OUTPUT_SCHEMA, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
    )


def _mock_interview_feedback_schema_fingerprint() -> str:
    return (
        "fschema-"
        + hashlib.sha256(
            json.dumps(MOCK_INTERVIEW_FEEDBACK_SCHEMA, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
    )


def _mock_interview_trace_metadata(model: Any, data_dir: Path) -> tuple[str, str, str]:
    """Sanitized provider/model/capability descriptors for the trace envelope."""
    provider = type(model).__name__ if model is not None else "none"
    model_name = str(getattr(model, "model", "") or "")
    if isinstance(model, ConfiguredAIClient):
        try:
            active = load_config(data_dir).active_provider()
            provider = active.id
            model_name = active.model
        except (OSError, ValueError):
            pass
    capability = {"supports_json_schema": bool(getattr(model, "supports_json_schema", False))}
    capability_hash = (
        "cap-"
        + hashlib.sha256(json.dumps(capability, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    )
    return provider, model_name, capability_hash


def _emit_mock_interview_trace(
    data_dir: Path,
    *,
    scenario_id: str,
    operation_id: str,
    attempt_id: int | None,
    generation_revision: int | None,
    idempotency_key: str,
    model: Any,
    input_fingerprint: str,
    schema_fingerprint: str,
    started_at: str,
    elapsed_ms: int,
    provider_outcome: str,
    validator_stage: str,
    failure_category: str,
    repair_count: int,
    response_error_code: str,
) -> None:
    """Append one sanitized trace envelope; the disposition comes from the contract."""
    provider, model_name, capability_hash = _mock_interview_trace_metadata(model, data_dir)
    record_mock_interview_trace(
        data_dir,
        MockInterviewTraceEnvelope(
            run_id=_MOCK_INTERVIEW_TRACE_RUN_ID,
            scenario_id=scenario_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            generation_revision=generation_revision,
            idempotency_key_hash=hash_idempotency_key(idempotency_key),
            provider=provider,
            model=model_name,
            capability_snapshot_hash=capability_hash,
            input_fingerprint=input_fingerprint[:200],
            schema_fingerprint=schema_fingerprint[:200],
            started_at=started_at,
            # Mock Interview currently uses non-streaming completions, so the
            # provider abstraction cannot observe first-byte timing honestly.
            first_byte_ms=None,
            completed_ms=elapsed_ms,
            provider_outcome=provider_outcome,
            validator_stage=validator_stage,
            failure_category=failure_category[:120],
            repair_count=int(repair_count),
            final_disposition="success"
            if not response_error_code
            else recovery_disposition(response_error_code),
            response_error_code=response_error_code,
        ),
    )


MockInterviewFailureFinalization = Literal["recorded", "stale", "source_conflict"]


def _mark_mock_interview_failure_state(
    callback: Callable[[], Any],
) -> MockInterviewFailureFinalization:
    """Finalize an Attempt after a provider call without losing its trace on source drift."""
    try:
        result = callback()
    except MockInterviewSourceChanged:
        return "source_conflict"
    return "recorded" if result is not None else "stale"


def _mock_interview_failure_response_code(
    finalization: MockInterviewFailureFinalization,
    default_error_code: str,
) -> str:
    if finalization == "source_conflict":
        return "mock_interview_source_conflict"
    if finalization == "stale":
        return "mock_interview_transcript_conflict"
    return default_error_code


def _mock_interview_failure_message(error_code: str, default_message: str) -> str:
    if error_code == "mock_interview_source_conflict":
        return "本次练习使用的冻结资料不可验证。"
    if error_code == "mock_interview_transcript_conflict":
        return "生成结果已失去当前写入所有权，请使用原 key 对账。"
    return default_message


def _log_mock_interview_ai_failure(
    data_dir: Path,
    *,
    attempt_id: int,
    stage: str,
    kind: str,
    diagnostic: dict[str, Any] | None,
) -> None:
    diagnostic = diagnostic or {}
    payload = {
        "attempt_id": attempt_id,
        "stage": stage,
        "failure_category": str(diagnostic.get("failure_category") or kind),
        "repair_attempted": bool(diagnostic.get("repair_attempted", False)),
        "repair_count": int(diagnostic.get("repair_count") or 0),
        "elapsed_ms": int(diagnostic.get("elapsed_ms") or 0),
        "http_status": diagnostic.get("http_status"),
        "timeout": bool(diagnostic.get("timeout", False)),
        "correlation_id": str(diagnostic.get("correlation_id") or ""),
        "provider_request_id": str(diagnostic.get("provider_request_id") or ""),
    }
    failure_categories = diagnostic.get("failure_categories")
    if isinstance(failure_categories, list) and all(
        isinstance(item, str) for item in failure_categories
    ):
        payload["failure_categories"] = failure_categories[:2]
    append_log_entry(
        data_dir,
        "WARNING",
        f"mock_interview_{kind}_failure "
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
    )
