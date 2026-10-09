"""One in-memory task: read, propose, confirm once, reuse the same result.

Teaching code only: no network, database, persistence, concurrency or crash recovery.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

REQUEST = "为云岚数据测试开发新增 2026-10-15 北京时间 15:00 的线上面试，60 分钟。"
APPLICATIONS = (
    (1, "云岚数据", "测试开发"),
    (2, "云岚数据", "后端开发"),
    (3, "青禾科技", "前端开发"),
)


@dataclass(frozen=True)
class Interview:
    application_id: int
    scheduled_at: str
    duration_minutes: int
    location: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Interview | None = None


@dataclass
class Operation:
    call: ToolCall
    status: str = "pending"
    result_id: int = 0


class ScriptedModel:
    """Fixed replies; this does not understand or evaluate natural language."""

    def __init__(self) -> None:
        self._replies = iter((
            ToolCall("read-1", "list_applications"),
            ToolCall("write-1", "create_application_event", Interview(
                1, "2026-10-15T15:00:00+08:00", 60, "线上",
            )),
        ))

    def complete(self, _messages: list[dict[str, Any]]) -> ToolCall:
        return next(self._replies)


class MiniHarness:
    """One instance owns one task. All state disappears when the process exits."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": REQUEST}]
        self.events: list[Interview] = []
        self._operations: dict[str, Operation] = {}
        self._started = False

    def prepare(self, model: ScriptedModel) -> str:
        if self._started:
            raise ValueError("This lab accepts one task; create a new instance for another.")
        self._started = True
        for _ in range(3):
            call = model.complete(self.messages)
            if call.name not in {"list_applications", "create_application_event"}:
                raise ValueError("Unknown tool")
            self.messages.append({"role": "assistant", "tool_calls": [call]})
            if call.name == "list_applications":
                self._tool_result(call.id, APPLICATIONS)
                continue
            proposal = call.arguments
            if proposal is None or proposal.application_id not in {row[0] for row in APPLICATIONS}:
                raise ValueError("Unknown application")
            # Operation identity differs from tool-call identity. One task in this lab only.
            operation_id = "operation-1"
            self._operations[operation_id] = Operation(call)
            return operation_id  # Pause: no event is saved here.
        raise RuntimeError("Model-step limit reached")

    def status(self, operation_id: str) -> str:
        return self._operations[operation_id].status

    def proposal(self, operation_id: str) -> Interview:
        value = self._operations[operation_id].call.arguments
        assert value is not None
        return value

    def confirm(self, operation_id: str) -> int:
        operation = self._operations[operation_id]
        if operation.status == "committed":
            return operation.result_id  # Reuse; do not call the writer again.
        if operation.status == "rejected":
            raise ValueError("This operation was rejected")
        self.events.append(self.proposal(operation_id))
        operation.result_id = len(self.events)
        operation.status = "committed"
        self._tool_result(operation.call.id, {"event_id": operation.result_id})
        return operation.result_id

    def reject(self, operation_id: str) -> None:
        operation = self._operations[operation_id]
        if operation.status == "committed":
            raise ValueError("A committed event cannot be undone by rejecting a proposal")
        if operation.status == "pending":
            operation.status = "rejected"
            self._tool_result(operation.call.id, {"status": "rejected"})

    def _tool_result(self, call_id: str, content: Any) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="仅内存的 Harness 教学实验")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--confirm", action="store_true", help="模拟确认，再重复同一确认")
    choice.add_argument("--reject", action="store_true", help="模拟拒绝建议")
    args = parser.parse_args(argv)
    harness = MiniHarness()
    operation_id = harness.prepare(ScriptedModel())
    print(f"请求：{REQUEST}")
    print(f"待确认：{len(harness.events)} 条日程；操作 {operation_id}")
    assert len(harness.events) == 0
    if args.confirm:
        first = harness.confirm(operation_id)
        print(f"确认后：{len(harness.events)} 条日程；日程 #{first}")
        assert len(harness.events) == 1
        replay = harness.confirm(operation_id)
        print(f"再次确认同一操作：{len(harness.events)} 条日程；返回原日程 #{replay}")
        assert first == replay and len(harness.events) == 1
    elif args.reject:
        harness.reject(operation_id)
        print(f"拒绝后：{len(harness.events)} 条日程；状态 {harness.status(operation_id)}")
        assert len(harness.events) == 0
    else:
        print("本次没有确认。使用 --confirm 或 --reject 在新的内存实验中模拟决定。")
    print("实验结束：状态不落盘，不影响 OfferPilot 数据。")


if __name__ == "__main__":
    main()
