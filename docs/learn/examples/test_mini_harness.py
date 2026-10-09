"""Run with: python -m unittest discover -s docs/learn/examples -p 'test_*.py' -v."""

import io
import unittest
from contextlib import redirect_stdout

from mini_harness import MiniHarness, ScriptedModel, main


class MiniHarnessTests(unittest.TestCase):
    def test_default_command_stops_at_confirmation(self):
        output = io.StringIO()
        with redirect_stdout(output):
            main([])
        self.assertIn("待确认：0 条日程", output.getvalue())
        self.assertNotIn("确认后：1 条日程", output.getvalue())

    def test_confirm_command_demonstrates_zero_one_one(self):
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--confirm"])
        for expected in ("待确认：0 条日程", "确认后：1 条日程", "再次确认同一操作：1 条日程"):
            self.assertIn(expected, output.getvalue())

    def test_read_result_is_paired_and_proposal_does_not_write(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())

        self.assertEqual(harness.status(operation_id), "pending")
        self.assertEqual(harness.events, [])
        call = harness.messages[1]["tool_calls"][0]
        result = harness.messages[2]
        self.assertEqual(call.name, "list_applications")
        self.assertEqual(result["tool_call_id"], call.id)
        self.assertEqual(result["content"][0], (1, "云岚数据", "测试开发"))

    def test_confirmation_saves_exactly_the_proposal(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())

        result = harness.confirm(operation_id)

        self.assertEqual(result, 1)
        self.assertEqual(harness.status(operation_id), "committed")
        self.assertEqual(len(harness.events), 1)
        event = harness.events[0]
        self.assertEqual(event.application_id, 1)
        self.assertEqual(event.scheduled_at, "2026-10-15T15:00:00+08:00")
        self.assertEqual(event.duration_minutes, 60)
        self.assertEqual(event.location, "线上")
        self.assertEqual(harness.messages[-1]["tool_call_id"], "write-1")

    def test_repeated_confirmation_reuses_the_original_result(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())
        first = harness.confirm(operation_id)
        message_count = len(harness.messages)

        self.assertEqual(harness.confirm(operation_id), first)
        self.assertEqual(len(harness.events), 1)
        self.assertEqual(len(harness.messages), message_count)

    def test_rejected_operation_cannot_be_confirmed_later(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())
        harness.reject(operation_id)

        with self.assertRaisesRegex(ValueError, "rejected"):
            harness.confirm(operation_id)
        self.assertEqual(harness.events, [])
        self.assertEqual(harness.status(operation_id), "rejected")

    def test_unknown_confirmation_does_not_save(self):
        harness = MiniHarness()
        harness.prepare(ScriptedModel())

        with self.assertRaises(KeyError):
            harness.confirm("another-operation")
        self.assertEqual(harness.events, [])

    def test_repeated_rejection_does_not_duplicate_the_tool_result(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())
        harness.reject(operation_id)
        message_count = len(harness.messages)

        harness.reject(operation_id)

        self.assertEqual(harness.events, [])
        self.assertEqual(harness.status(operation_id), "rejected")
        self.assertEqual(len(harness.messages), message_count)

    def test_preparation_is_not_an_implicit_retry(self):
        harness = MiniHarness()
        harness.prepare(ScriptedModel())

        with self.assertRaisesRegex(ValueError, "one task"):
            harness.prepare(ScriptedModel())
        self.assertEqual(harness.events, [])

    def test_rejection_cannot_undo_a_committed_event(self):
        harness = MiniHarness()
        operation_id = harness.prepare(ScriptedModel())
        harness.confirm(operation_id)

        with self.assertRaisesRegex(ValueError, "committed"):
            harness.reject(operation_id)
        self.assertEqual(len(harness.events), 1)


if __name__ == "__main__":
    unittest.main()
