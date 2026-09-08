# Confirmation Continuation Title Race Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent automatic conversation-title generation from spuriously
rejecting a valid confirmation continuation as stale.

**Architecture:** `Conversation.updated_at` remains the optimistic generation
for conversation content, pending-confirmation state, and archive state. The
generated-title repository update becomes metadata-only so it cannot advance
that generation. A focused repository regression test simulates the exact
interleaving while existing stale and replay tests prove real conflicts remain
protected.

**Tech Stack:** Python 3.10, SQLAlchemy, SQLite, pytest, Ruff, mypy, OfferPilot
CLI verification with the configured OpenAI-compatible provider.

---

## File Structure

- `src/offerpilot/repositories/chat.py` owns repository-level conversation
  persistence and the `updated_at` optimistic-concurrency generation.
- `tests/test_chat_repository.py` verifies repository atomicity and
  confirmation-continuation behavior without relying on model timing.
- `docs/superpowers/specs/2026-07-14-confirmation-title-race-design.md` records
  the approved boundary: title metadata must not advance confirmation state.

### Task 1: Protect the confirmation generation from generated titles

**Files:**
- Modify: `tests/test_chat_repository.py:211-244`
- Modify: `src/offerpilot/repositories/chat.py:80-97`

- [x] **Step 1: Write the failing repository regression test**

  Add this test immediately before
  `test_confirmation_continuation_rejects_stale_conversation_generation`:

  ```python
  def test_confirmation_continuation_survives_generated_title_update(tmp_path):
      repo = ChatRepository(init_database(tmp_path / "data.db"))
      conversation = repo.create_conversation("confirm")
      pending = PendingAction("write-1", "update_application_status", '{"id":1}', "first")
      repo.set_pending_action(conversation.id, pending)
      generation = repo.resolve_pending_confirmation(
          conversation.id,
          pending,
          Message(role="tool", content='{"ok":true}', tool_call_id="write-1"),
          {"kind": "undo"},
      )

      assert repo.apply_generated_title(conversation.id, "Generated title") is True
      persisted = repo.persist_confirmation_continuation(
          conversation.id,
          generation,
          [{"role": "assistant", "content": "next", "tool_calls": "", "tool_call_id": ""}],
      )

      assert persisted is not None
      assert persisted.title == "Generated title"
      assert [message.content for message in repo.list_messages(conversation.id)] == [
          '{"ok":true}',
          "next",
      ]
  ```

- [x] **Step 2: Run the regression test and verify the current failure**

  Run:

  ```powershell
  uv run pytest tests/test_chat_repository.py::test_confirmation_continuation_survives_generated_title_update -q
  ```

  Expected: FAIL because `persist_confirmation_continuation()` returns `None`
  after `apply_generated_title()` advances `updated_at`.

- [x] **Step 3: Make the minimal metadata-only title update**

  In `ChatRepository.apply_generated_title`, remove the
  `updated_at=datetime.now(timezone.utc)` entry from the SQLAlchemy `.values()`
  call. Preserve the existing `id` plus `title_source == "fallback"` predicate,
  `title`, `title_source="generated"`, transaction commit, and Boolean result.

  The resulting update body is:

  ```python
  .values(
      title=title,
      title_source="generated",
  )
  ```

- [x] **Step 4: Run repository regression and existing contention coverage**

  Run:

  ```powershell
  uv run pytest tests/test_chat_repository.py -q
  ```

  Expected: PASS. In particular, the new generated-title interleaving succeeds,
  while `test_confirmation_continuation_rejects_stale_conversation_generation`,
  `test_confirmation_continuation_generation_is_consumed_once`, and
  `test_confirmation_continuation_cannot_create_pending_after_archive` still
  pass.

- [x] **Step 5: Commit the focused implementation**

  Run these commands separately:

  ```powershell
  git add src/offerpilot/repositories/chat.py tests/test_chat_repository.py
  git commit -m "fix: AI prevent confirmation title race"
  ```

### Task 2: Validate the user-visible confirmation flow with the real provider

**Files:**
- Verify: `src/offerpilot/api.py:1556-1812`
- Verify: `src/offerpilot/repositories/chat.py:245-380`

- [x] **Step 1: Run chat API coverage**

  Run:

  ```powershell
  uv run pytest tests/test_chat_api.py -q
  ```

  Expected: PASS, confirming HTTP confirmation behavior and background-title
  handling remain compatible.

- [x] **Step 2: Run static checks for the edited production module**

  Run:

  ```powershell
  uv run ruff check src/offerpilot/repositories/chat.py tests/test_chat_repository.py
  uv run mypy src/offerpilot/repositories/chat.py
  ```

  Expected: both commands exit with code 0.

- [x] **Step 3: Run the isolated real-AI verification profile**

  Run:

  ```powershell
  uv run oc verify --profile real-ai --static-dir web/dist
  ```

  Expected: exit code 0. The configured provider must create a
  confirmation-required write and the confirmation must complete without an
  unexpected HTTP 409.

- [x] **Step 4: Request independent code review**

  Ask a fresh subagent to inspect the committed diff and test evidence. Require
  concrete file-and-line findings, do not permit source edits, and resolve any
  blocking finding before completion.

- [x] **Step 5: Commit the implementation plan artifact**

  Run these commands separately:

  ```powershell
  git add -f docs/superpowers/plans/2026-07-14-confirmation-title-race.md
  git commit -m "docs: AI add confirmation race plan"
  ```

## Self-Review

- **Spec coverage:** Task 1 enforces metadata-only title updates, preserves the
  existing CAS, and directly proves the observed interleaving. Its existing
  neighboring tests prove semantic activity, replay, and archive changes still
  fail closed. Task 2 covers API compatibility, static correctness, real-model
  confirmation, and independent review.
- **Placeholder scan:** no TODOs, deferred implementation, or unspecified test
  steps remain.
- **Type consistency:** the plan uses existing `ChatRepository`,
  `PendingAction`, `Message`, `resolve_pending_confirmation`,
  `apply_generated_title`, and `persist_confirmation_continuation` signatures.
