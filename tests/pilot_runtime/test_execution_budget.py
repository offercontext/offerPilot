from __future__ import annotations

import pytest

from offerpilot.pilot_runtime.execution_budget import (
    RuntimeBudget,
    RuntimeBudgetExceeded,
    RuntimeClockInvalid,
    RuntimeDeadlineExceeded,
)


def test_runtime_budget_counts_agent_and_title_model_calls_together() -> None:
    now = [10.0]
    budget = RuntimeBudget(
        timeout_seconds=5.0,
        max_model_calls=2,
        max_tool_calls=1,
        clock=lambda: now[0],
    )

    budget.reserve_model_call()
    budget.reserve_model_call(purpose="title")

    with pytest.raises(RuntimeBudgetExceeded, match="model call budget"):
        budget.reserve_model_call()

    snapshot = budget.snapshot()
    assert snapshot.model_calls == 2
    assert snapshot.title_model_calls == 1
    assert snapshot.remaining_model_calls == 0


def test_runtime_budget_deadline_is_absolute_and_queue_wait_is_consumed() -> None:
    now = [100.0]
    budget = RuntimeBudget(timeout_seconds=2.0, clock=lambda: now[0])
    now[0] = 102.0

    with pytest.raises(RuntimeDeadlineExceeded):
        budget.check_deadline()

    assert budget.remaining_seconds == 0.0


def test_runtime_budget_event_limits_are_bounded_without_borrowing_journal_budget() -> None:
    budget = RuntimeBudget(
        timeout_seconds=10.0,
        max_events=2,
        max_event_bytes=5,
    )

    assert budget.try_record_event(3) is True
    assert budget.try_record_event(2) is True
    assert budget.try_record_event(1) is False
    assert budget.snapshot().event_count == 2
    assert budget.snapshot().event_bytes == 5


def test_runtime_budget_latches_an_invalid_clock() -> None:
    samples = [10.0, 11.0, 9.0]
    budget = RuntimeBudget(timeout_seconds=10.0, clock=lambda: samples.pop(0))
    budget.check_deadline()

    with pytest.raises(RuntimeClockInvalid):
        budget.check_deadline()
    with pytest.raises(RuntimeClockInvalid):
        budget.check_deadline()
    assert budget.clock_invalid is True


def test_runtime_budget_allows_unused_title_and_tool_budgets_to_be_zero() -> None:
    budget = RuntimeBudget(max_title_model_calls=0, max_tool_calls=0)

    with pytest.raises(RuntimeBudgetExceeded, match="model call budget"):
        budget.reserve_model_call(purpose="title")
    with pytest.raises(RuntimeBudgetExceeded, match="tool call budget"):
        budget.reserve_tool_call()
