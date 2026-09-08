# Event-Bound Mock Interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each task is test-first and ends with a focused commit. Do not restore the legacy `/api/mock/*` contract.

**Goal:** Replace the unmounted, score-based MockSession path with an event-bound text mock interview whose questions and feedback are frozen, evidence-gated, idempotent, and confirmed by the user before any review draft is written.

**Architecture:** Remove the legacy `mock_sessions`/MockStudio/conversation path in migration `0016_event_bound_mock_interview`. Add independent Attempt, Turn, immutable Feedback Proposal, and confirmed Review Draft records. Application/Event/Resume/JD/preparation input uses an immutable `input_fingerprint`; the append-only Turn transcript uses a separate `transcript_fingerprint` for CAS and replay. Provider calls happen only after short SQLite transactions close and are committed with lease/token CAS.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, Pydantic, pytest, React/TypeScript, Vitest, Axios service wrappers, existing `ChatModel`/Provider capability routing, isolated PowerShell smoke harness.

**Source design:** `docs/superpowers/specs/2026-07-28-event-bound-mock-interview-design.md` at approved commit `a420f48`.

---

## Global implementation rules

- Work only in `D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260724-evidence-gated-interview-preparation` on the current branch.
- Do not change product behavior before the failing test for that behavior exists.
- Do not reintroduce `MockSession`, `/api/mock/*`, `Conversation(mode="mock_interview")`, free scores, automatic InterviewNote saving, audio, transcription, URL fetching, recruiting-platform access, automatic status changes, Knowledge/Question/Memory/Reminder writes, or generic mock-interview entry points.
- Use `input_fingerprint`/`source_fingerprint` only for immutable Application/Event/Resume/JD/selected-preparation input. Use `transcript_fingerprint` only for ordered Turns and provider CAS. A normal answer append never becomes `source_changed`.
- `strengths` and `practice_points` require a Turn answer ref. `next_practice_steps` require a Turn answer ref and add JD/Resume refs when they mention role or prior experience. `follow_up_questions` either carry evidence or exactly match the versioned fixed-question allowlist.
- The browser may contact only local static resources and local `/api`. Provider egress is server-side only; the server harness parses the configured Provider URL and requires every recorded Provider request to match its exact `(scheme, host, effective port)` triple.
- Every task ends with separate `git add` and `git commit` commands. No shell command may combine them with `&&`.

## File map before implementation

| Responsibility | Files to create or modify |
| --- | --- |
| Destructive migration and model registry | `src/offerpilot/db.py`, `src/offerpilot/models.py`, `src/offerpilot/schemas.py` |
| Attempt/Turn and draft repositories | `src/offerpilot/repositories/mock_interviews.py`, `src/offerpilot/repositories/mock_interview_review_drafts.py` |
| AI contracts and provider diagnostics | `src/offerpilot/ai/mock_interview.py`; reuse the existing optional `ChatModel.complete(messages, tools, response_format=None)` contract in `src/offerpilot/ai/agent.py` and `src/offerpilot/ai/client.py` |
| HTTP API | `src/offerpilot/api.py` |
| Backend tests | `tests/test_mock_interview_migrations.py`, `tests/test_mock_interview_repository.py`, `tests/test_mock_interview_ai.py`, `tests/test_mock_interview_api.py`, `tests/test_mock_interview_review_drafts.py`, plus targeted updates to `tests/test_chat_api.py`, `tests/test_conditional_delete_repositories.py`, and `tests/test_smoke.py` |
| Frontend service/types/state | `web/src/services/mockInterviews.ts`, `web/src/types/mockInterview.ts`, `web/src/layout/AppShell.tsx` |
| Frontend entry and drawer | `web/src/components/InterviewV01View.tsx`, new `web/src/components/MockInterviewDrawer.tsx` and `web/src/components/MockInterviewDrawer.module.css` plus tests, `web/src/features/pilot/PilotOpportunityFitV2Card.tsx`, and `web/src/layout/AppShell.tsx` |
| Legacy frontend removal | Delete `web/src/components/MockStudio/MockChat.tsx`, `MockResultCard.tsx`, `MockStudioView.tsx`, `MockStudio.module.css`, `RadarChart.tsx`, `web/src/services/mock.ts`, `web/src/types/mock.ts`; modify `web/src/layout/navigation.ts`, `navigation.test.ts`, `web/src/components/ChatPanel/capabilities.ts`, and `conversationList.test.ts` |
| Isolated runtime acceptance | `src/offerpilot/smoke.py`, `tests/test_smoke.py`, new `scripts/mock-interview-real-ai-browser-harness.ps1` |

The plan below assigns every file to a task so a task can be reviewed independently.

## Task 1: `0016` destructive migration and legacy Mock cleanup

**Files:**

- Create: `tests/test_mock_interview_migrations.py`
- Modify: `src/offerpilot/db.py`, `src/offerpilot/models.py`, `src/offerpilot/schemas.py`, `src/offerpilot/api.py`
- Modify: `tests/test_conditional_delete_repositories.py`, `tests/test_chat_api.py`, `tests/test_smoke.py`
- Delete: `src/offerpilot/repositories/mock.py`, `tests/test_mock_api.py`
- Delete: `web/src/components/MockStudio/MockChat.tsx`, `web/src/components/MockStudio/MockResultCard.tsx`, `web/src/components/MockStudio/MockStudioView.tsx`, `web/src/components/MockStudio/MockStudio.module.css`, `web/src/components/MockStudio/RadarChart.tsx`, `web/src/services/mock.ts`, `web/src/types/mock.ts`
- Modify: `web/src/layout/navigation.ts`, `web/src/layout/navigation.test.ts`, `web/src/components/ChatPanel/capabilities.ts`, `web/src/components/ChatPanel/conversationList.test.ts`

- [ ] **Step 1: Add failing migration tests using a real legacy DDL.**

Create a SQLite database with the previous `mock_sessions` columns, `idx_mock_sessions_conv`, `idx_mock_sessions_status`, `conversations`, `chat_messages`, one `mode='mock_interview'` conversation, one ordinary conversation, and messages in both. Do not call current `create_all()` before the migration. Add these tests:

Add these exact tests: `test_0016_drops_mock_rows_and_messages_but_preserves_normal_chat`, `test_0016_handles_legacy_named_indexes_before_dropping_mock_table`, `test_0016_creates_new_tables_and_is_idempotent`, and `test_0016_preserves_formal_interview_notes_but_creates_no_legacy_mock_data`. Each test creates the legacy DDL in its own `tmp_path`, invokes the migration entry point, and asserts the rows, indexes, tables, and migration version described above.

Run:

```powershell
uv run pytest tests/test_mock_interview_migrations.py -q
```

Expected: FAIL because `0016` and the new tables do not exist.

- [ ] **Step 2: Add failing removal tests for the old model, API and UI.**

Replace `tests/test_mock_api.py` with `tests/test_mock_legacy_removed.py` and assert `GET`, `POST`, `GET/{id}`, `POST/{id}/end`, and `DELETE/{id}` under `/api/mock/sessions` all return ordinary 404. Update navigation and Chat tests to assert no `mock` view, no `mock_interview` mode filter, and no `mock-*` capability IDs.

Run:

```powershell
uv run pytest tests/test_mock_legacy_removed.py tests/test_conditional_delete_repositories.py tests/test_chat_api.py -q
cd web
npm.cmd test -- --run src/layout/navigation.test.ts src/components/ChatPanel/conversationList.test.ts
cd ..
```

Expected: FAIL while the old model and routes remain registered.

- [ ] **Step 3: Implement migration `0016_event_bound_mock_interview`.**

In `src/offerpilot/db.py`, delete old `mock_sessions` rows and their `chat_messages`/`mode='mock_interview'` conversations in dependency order, drop the legacy named indexes before dropping the table, create the four new tables, then insert the migration version with `INSERT OR IGNORE`. Preserve ordinary Chat rows and formal `InterviewNote` rows. Do not add foreign keys from frozen Attempt source IDs to Application/Event/Resume.

In `src/offerpilot/models.py`, add the four new models and remove `MockSession` from `APPLICATION_FOREIGN_KEY_MODELS`; in `schemas.py`, remove `MockSessionOut`. Keep `Base.metadata.create_all()` compatible with a brand-new database, while the migration test remains based on the real previous DDL.

- [ ] **Step 4: Remove the legacy registrations and satisfy the tests.**

Remove old API imports, repository construction, all `/api/mock/sessions*` routes, `_mock_session_json`, `_mock_scoring_prompt`, `_mock_transcript`, and `_save_mock_feedback_note`. Remove old cleanup/smoke references. Delete the listed MockStudio/service/type files and remove the stale navigation and Chat capability types.

Run:

```powershell
uv run pytest tests/test_mock_interview_migrations.py tests/test_mock_legacy_removed.py tests/test_conditional_delete_repositories.py tests/test_chat_api.py -q
cd web
npm.cmd test -- --run src/layout/navigation.test.ts src/components/ChatPanel/conversationList.test.ts
cd ..
```

Expected: all targeted tests pass; old Mock routes are 404 and ordinary Chat remains usable.

- [ ] **Step 5: Commit the isolated migration/removal.**

```powershell
git add src/offerpilot/db.py src/offerpilot/models.py src/offerpilot/schemas.py src/offerpilot/api.py src/offerpilot/repositories/mock.py tests/test_mock_interview_migrations.py tests/test_mock_legacy_removed.py tests/test_conditional_delete_repositories.py tests/test_chat_api.py tests/test_smoke.py web/src
git commit -m "feat: AI remove legacy mock interview path"
```

## Task 2: Attempt/Turn idempotency, two-connection lease/CAS, and fingerprints

**Files:**

- Create: `src/offerpilot/repositories/mock_interviews.py`
- Modify: `src/offerpilot/models.py`, `src/offerpilot/api.py`
- Create: `tests/test_mock_interview_repository.py`, `tests/test_mock_interview_api.py`

- [ ] **Step 1: Write repository tests for immutable input and separate transcript fingerprints.**

Use a visible Application with two scheduled interview events and two visible Resumes. Assert `create_or_replay_start()` stores the original JD/Resume/Event/preparation snapshot and `source_fingerprint`, with an empty initial transcript and deterministic `transcript_fingerprint`. After adding an answer, assert only transcript state changes and the source fingerprint remains equal.

Add exact tests:

Add these exact tests: `test_start_requires_visible_scheduled_interview_and_selected_resume`, `test_start_rejects_empty_jd_without_provider_call`, `test_answer_updates_transcript_not_source_fingerprint`, `test_same_attempt_key_same_input_replays_existing_attempt`, `test_same_attempt_key_different_input_returns_idempotency_conflict`, `test_same_turn_key_different_answer_returns_turn_idempotency_conflict`, and `test_editing_submitted_answer_requires_new_attempt`. The assertions must cover the stored frozen fields, status code, key reuse, and the distinction between input and transcript fingerprints.

Also add `test_initial_question_key_is_persisted_on_first_turn`, `test_same_initial_question_key_replays_first_turn`, and `test_different_initial_question_key_returns_turn_idempotency_conflict`. `initial_question_idempotency_key` is stored on the first Turn as its `question_idempotency_key`, participates in `UNIQUE(attempt_id, turn_no, question_idempotency_key)`, and is checked against the existing first Turn before any new insert; changing it for the same Attempt/turn returns 409 without a second Provider call.

Run:

```powershell
uv run pytest tests/test_mock_interview_repository.py -q
```

Expected: FAIL because the repository and models are not present.

- [ ] **Step 2: Add the two-independent-connection concurrency tests before implementation.**

Use two independent SQLAlchemy session factories against the same SQLite file and a provider barrier. The test must release the first transaction/session before the provider barrier is entered. Assert one Attempt row, one initial Turn, one provider call, and a 202/200 replay for the losing request.

Add these exact tests: `test_concurrent_first_start_creates_one_attempt_and_one_provider_owner`, `test_expired_question_lease_has_one_cas_takeover`, `test_expired_feedback_lease_has_one_cas_takeover`, `test_late_question_owner_cannot_overwrite_new_turn`, `test_late_feedback_owner_cannot_overwrite_ready_proposal`, and `test_transcript_advanced_returns_safe_current_state_not_source_conflict`.

Run:

```powershell
uv run pytest tests/test_mock_interview_repository.py -q -k "concurrent or lease or late or transcript"
```

Expected: FAIL with missing repository methods; after implementation, each test proves SQLite `BEGIN IMMEDIATE`/revision/token CAS rather than a mock-only lock.

- [ ] **Step 3: Implement the repository state transitions.**

Implement short-transaction methods with these stable signatures:

```python
create_or_replay_start(application_id, event_id, resume_id, jd_text,
                       preparation_proposal_id, attempt_idempotency_key,
                       initial_question_idempotency_key)
submit_answer(attempt_id, turn_no, answer_text, turn_idempotency_key)
claim_question(attempt_id, turn_no, question_idempotency_key)
claim_feedback(attempt_id, feedback_idempotency_key)
complete_question(attempt_id, revision, provider_call_token, transcript_fingerprint, question)
complete_feedback(attempt_id, revision, provider_call_token, transcript_fingerprint, proposal)
mark_provider_unknown(attempt_id, revision, provider_call_token, operation)
cancel_unconfirmed(attempt_id)
get_attempt_history(application_id, event_id, attempt_id)
```

`create_or_replay_start()` must insert the Attempt and first-question owner atomically. It persists `initial_question_idempotency_key` as the first Turn's `question_idempotency_key` before the first Provider call; the first Turn is protected by both `UNIQUE(attempt_id, turn_no)` and `UNIQUE(attempt_id, turn_no, question_idempotency_key)`. A same-key/same-payload start replays that Turn, while a different initial question key for the same Attempt and turn returns `409 mock_interview_turn_idempotency_conflict` without a second Provider call. Every model call owner receives a new revision/token and closes the session before calling the Provider. Completion and failure writes use `WHERE attempt_status, generation_revision, provider_call_token, expected_transcript_fingerprint`; a failed CAS returns current state without changing it. A stale transcript is a concurrency result, never `source_changed`.

- [ ] **Step 4: Add application/event-scoped HTTP validation and replay responses.**

Add the approved endpoint family under `/api/applications/{application_id}/events/{event_id}/mock-interview/attempts`. Validate application visibility, event ownership/type/scheduled time, selected Resume visibility, non-empty original JD, preparation Proposal currentness, and strict key formats before any Provider call. Return 202 without question/answer/snapshot/Proposal for live leases; return 201 on creation and 200 on same-key replay.

Run:

```powershell
uv run pytest tests/test_mock_interview_repository.py tests/test_mock_interview_api.py -q
```

Expected: all Attempt/Turn and endpoint tests pass; ordinary answer progression never returns source conflict.

- [ ] **Step 5: Commit Attempt/Turn persistence.**

```powershell
git add src/offerpilot/models.py src/offerpilot/repositories/mock_interviews.py src/offerpilot/api.py tests/test_mock_interview_repository.py tests/test_mock_interview_api.py
git commit -m "feat: AI add event-bound mock interview attempts"
```

## Task 3: Strict question/feedback Proposal contracts, evidence rules, safe empty, and diagnostics

**Files:**

- Create: `src/offerpilot/ai/mock_interview.py`
- Do not modify `src/offerpilot/ai/agent.py` or `src/offerpilot/ai/client.py`; their existing optional `response_format=None` contract and capability branch are part of the implementation surface
- Modify: `src/offerpilot/api.py`, `src/offerpilot/repositories/mock_interviews.py`
- Create: `tests/test_mock_interview_ai.py`, `tests/test_mock_interview_diagnostics.py`

- [ ] **Step 1: Write strict contract failures first.**

Cover duplicate JSON keys, fenced JSON, NaN/Infinity, wrong root, extra fields, blank question/text, array limits, duplicate IDs, missing Turn ref, JD/Resume-only strengths or practice points, fabricated paths/excerpts, invalid Resume pointers, non-allowlisted empty follow-up questions, and next steps without answer evidence.

Use exact test names:

Add these exact tests: `test_question_contract_rejects_duplicate_keys_and_fenced_json`, `test_feedback_contract_rejects_nonfinite_extra_blank_and_over_limit_values`, `test_strengths_and_practice_points_require_turn_answer_evidence`, `test_follow_up_fixed_question_requires_versioned_id_and_exact_text`, `test_follow_up_context_question_requires_evidence`, `test_next_practice_step_requires_turn_and_optional_source_refs`, `test_resume_pointer_and_excerpt_must_resolve_to_frozen_string_leaf`, and `test_safe_empty_has_exactly_four_empty_arrays`.

Run:

```powershell
uv run pytest tests/test_mock_interview_ai.py -q
```

Expected: FAIL because the new parser/validator does not exist.

- [ ] **Step 2: Implement the strict JSON and Evidence validators.**

Create `parse_mock_interview_json()` with a duplicate-key `object_pairs_hook` and `parse_constant` rejection. Define exact top-level fields:

```python
FEEDBACK_FIELDS = {
    "schema_version", "proposal_status", "strengths", "practice_points",
    "follow_up_questions", "next_practice_steps",
}
SAFE_EMPTY_FEEDBACK = {
    "schema_version": "mock-interview-feedback-v1",
    "proposal_status": "safe_empty",
    "strengths": [], "practice_points": [],
    "follow_up_questions": [], "next_practice_steps": [],
}
```

Reject every unknown field and validate the exact evidence rules from the design. Reuse the repository’s canonical JSON/SHA-256 utilities. The validator receives the frozen source snapshot and ordered Turns; it never trusts model-provided IDs, hashes, or descriptions.

- [ ] **Step 3: Add Provider capability branching and one repair attempt.**

Use `response_format` only when the configured capability is the real JSON boolean `True`; otherwise use strict JSON text. On a contract failure, call once more with only a category such as `invalid_json`, `unexpected_field`, `missing_evidence_ref`, `unknown_evidence_ref`, `excerpt_mismatch`, or `limit_exceeded`. Provider/network/timeout errors skip repair and preserve the original operation key.

Add diagnostics tests asserting logs contain only category, repair flag/count, elapsed time, and a redacted Provider request identifier: `test_contract_failure_diagnostic_never_contains_model_input_or_raw_output`, `test_provider_error_is_not_retried_as_format_repair`, and `test_format_repair_uses_same_snapshot_and_at_most_one_retry`.

- [ ] **Step 4: Wire question and feedback completion through the repository CAS.**

On valid question output, complete the claimed Turn. On valid `normal` or server-created `safe_empty` feedback, persist Proposal with both `source_fingerprint` and `transcript_fingerprint`. On two contract failures return `502 mock_interview_unverifiable` and persist no Proposal. Do not log or return raw model content.

Run:

```powershell
uv run pytest tests/test_mock_interview_ai.py tests/test_mock_interview_diagnostics.py tests/test_mock_interview_repository.py tests/test_mock_interview_api.py -q
```

Expected: all strict parsing, evidence, safe-empty, retry, diagnostic, CAS, and HTTP error tests pass.

- [ ] **Step 5: Commit AI contract and diagnostics.**

```powershell
git add src/offerpilot/ai/mock_interview.py src/offerpilot/ai/types.py src/offerpilot/api.py src/offerpilot/repositories/mock_interviews.py tests/test_mock_interview_ai.py tests/test_mock_interview_diagnostics.py
git commit -m "feat: AI gate mock interview feedback evidence"
```

## Task 4: HITL Review Draft unique confirmation and atomic write

**Files:**

- Create: `src/offerpilot/repositories/mock_interview_review_drafts.py`
- Modify: `src/offerpilot/models.py`, `src/offerpilot/api.py`
- Create: `tests/test_mock_interview_review_drafts.py`
- Modify: `tests/test_mock_interview_api.py`

- [ ] **Step 1: Add failing draft tests.**

Write tests proving an unconfirmed Proposal cannot create a draft, selected IDs must exist in the immutable Proposal, edited text retains original Evidence refs, and the transaction does not modify `InterviewNote`, Knowledge, Question, Memory, Wakeup, Reminder, Application, Event, Resume, or status. Add:

Add these exact tests: `test_confirm_selected_feedback_creates_one_independent_review_draft`, `test_confirm_requires_explicit_confirmation_and_valid_selected_blocks`, `test_concurrent_confirmation_creates_one_draft_by_unique_proposal`, `test_same_confirmation_key_replays_same_draft`, `test_different_key_after_confirmation_returns_already_confirmed`, and `test_review_draft_never_creates_or_updates_interview_note_or_knowledge`.

Run:

```powershell
uv run pytest tests/test_mock_interview_review_drafts.py tests/test_mock_interview_api.py -q
```

Expected: FAIL because the draft table/repository/endpoint does not exist.

- [ ] **Step 2: Implement the atomic confirmation repository.**

Implement `confirm_review_draft(proposal_id, confirmation_idempotency_key, selected_blocks)` in one short transaction. Validate Proposal status, source fingerprint, selected block IDs, user-edited text limits, and preserved Evidence refs. Insert exactly one row guarded by `UNIQUE(proposal_id)`. On uniqueness conflict, return the stored draft for the same key; for a different key return `409 mock_interview_review_draft_already_confirmed`.

- [ ] **Step 3: Add the API and safe error mapping.**

Expose `POST /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/review-drafts` with the confirmation key in the request. Require the frontend to show a second confirmation before calling it, but enforce all domain checks server-side. Return 201 for the first draft and 200 for the same-key replay; never return the model’s original output or a raw exception.

Run:

```powershell
uv run pytest tests/test_mock_interview_review_drafts.py tests/test_mock_interview_api.py -q
```

Expected: all draft atomicity, replay, conflict, source-drift, and zero-cross-domain-write tests pass.

- [ ] **Step 4: Commit HITL persistence.**

```powershell
git add src/offerpilot/models.py src/offerpilot/repositories/mock_interview_review_drafts.py src/offerpilot/api.py tests/test_mock_interview_review_drafts.py tests/test_mock_interview_api.py
git commit -m "feat: AI add confirmed mock interview review drafts"
```

## Task 5: Interview index, Pilot, history, and Chinese UI

**Files:**

- Create: `web/src/types/mockInterview.ts`, `web/src/services/mockInterviews.ts`, `web/src/components/MockInterviewDrawer.tsx`, `web/src/components/MockInterviewDrawer.module.css`, `web/src/components/MockInterviewDrawer.test.tsx`, `web/src/components/MockInterviewDrawer.interaction.test.tsx`, `web/src/layout/AppShell.mockInterview.test.tsx`
- Modify: `web/src/components/InterviewV01View.tsx`, `web/src/components/InterviewV01View.test.tsx`, `web/src/layout/AppShell.tsx`, `web/src/features/pilot/PilotOpportunityFitV2Card.tsx`, and `web/src/features/pilot/PilotOpportunityFitV2Card.test.tsx`
- Modify: `tests/test_interview_index_api.py`, `tests/test_mock_interview_api.py`

- [ ] **Step 1: Write failing entry and state-ownership tests.**

Assert the index shows “开始文本模拟面试” only for a visible scheduled interview event, never selects the first event automatically, and passes exact `applicationId + eventId` from both the index and `PilotOpportunityFitV2Card`. Assert Application soft-delete/404 clears the drawer and pending handoff.

Add interaction coverage for a real unmount/remount: after provider unknown or 202 generating, close the drawer, reopen the same event, and retry with the original Attempt/operation key and frozen inputs. A successful response or deterministic failure clears the attempt key according to the backend contract.

Run:

```powershell
cd web
npm.cmd test -- --run src/components/InterviewV01View.test.tsx src/components/MockInterviewDrawer.test.tsx src/components/MockInterviewDrawer.interaction.test.tsx src/layout/AppShell.mockInterview.test.tsx
cd ..
```

Expected: FAIL because the event button, controlled reducer, drawer, and service do not exist.

- [ ] **Step 2: Implement the typed service and controlled reducer.**

Define discriminated frontend states `configuring`, `generating`, `awaiting_answer`, `provider_unknown`, `feedback_ready`, `source_changed`, and `history_readonly`. Store state by `(applicationId,eventId)` above the drawer; store `attempt_idempotency_key`, all operation keys, frozen input, Turns, and result-unknown status there. The drawer is a controlled view and cannot generate a new key on remount.

Define service methods matching the API: `startAttempt`, `submitAnswer`, `retryQuestion`, `finishAttempt`, `listAttemptHistory`, `getAttempt`, `confirmReviewDraft`, and `cancelAttempt`. A bare 502, timeout, network failure, and `mock_interview_provider_error` preserve the original key; only confirmed non-write errors clear it.

- [ ] **Step 3: Implement the event-bound drawer and HITL UI.**

Render configuration, explicit Resume selection, JD input, optional preparation selection, AI disclosure, one-question text turns, finish feedback, per-item selection/editing, second confirmation, independent draft result, and read-only history. Use fixed Chinese strings for system copy. Render JD, Resume titles, event/company/position, answers, Evidence excerpts, and AI text unchanged.

Do not render score/ranking/hiring language. Do not invoke Knowledge/Question/Memory/Reminder/Application/Event/Resume writes. The browser must never call an external Provider URL directly.

- [ ] **Step 4: Wire both entrances and history.**

Add the index event action and the Pilot action. Pilot can open the same drawer only with an explicitly selected `applicationId + eventId`; it cannot create a generic mock session. History requests are read-only and show frozen Attempt/Turn/Proposal/Review Draft data, with `source_changed` disabling generation while preserving the old view.

Run:

```powershell
cd web
npm.cmd test -- --run src/components/InterviewV01View.test.tsx src/components/MockInterviewDrawer.test.tsx src/components/MockInterviewDrawer.interaction.test.tsx src/layout/AppShell.mockInterview.test.tsx
npm.cmd run build
cd ..
uv run pytest tests/test_interview_index_api.py tests/test_mock_interview_api.py -q
```

Expected: targeted frontend tests, build, and backend entry/error tests pass.

- [ ] **Step 5: Commit the UI integration.**

```powershell
git add web/src tests/test_interview_index_api.py tests/test_mock_interview_api.py
git commit -m "feat: AI connect event-bound mock interview UI"
```

## Task 6: Isolated real-AI API, browser closure, zero writes, and cleanup

**Files:**

- Modify: `src/offerpilot/smoke.py`, `tests/test_smoke.py`
- Create: `scripts/mock-interview-real-ai-browser-harness.ps1`
- Create or modify: `tests/test_mock_interview_browser_harness.py`

- [ ] **Step 1: Add deterministic smoke tests before runtime wiring.**

Add tests for a 202-to-200 same-key replay, provider unknown bounded retry, four-array safe empty, malformed Proposal rejection, browser-only network allowlist, server-only Provider endpoint assertion, and cleanup ordering. Add these exact tests:

Add these exact tests: `test_real_ai_mock_interview_smoke_retries_202_with_same_request_and_accepts_200`, `test_mock_interview_smoke_rejects_pending_or_terminal_snapshot_leaks`, `test_mock_interview_smoke_requires_four_array_safe_empty_shape`, `test_mock_interview_smoke_rejects_untraceable_turn_evidence`, and `test_mock_interview_cleanup_deletes_draft_children_before_attempt`.

Run:

```powershell
uv run pytest tests/test_smoke.py tests/test_mock_interview_browser_harness.py -q
```

Expected: FAIL until the smoke flow and harness exist.

- [ ] **Step 2: Implement isolated API smoke.**

For `real-ai`, create a temporary data directory and copy only `config.json`; create three synthetic visible Applications, scheduled interview Events, and Resumes. Run each group through start, at least two answers, finish, and history. Accept only 200/201 terminal responses with exact public-field allowlists, correct Application/Event/Resume ownership, valid hash recomputation, and valid Turn evidence. A 202 must be retried with the same key and original body until 200/201 or a bounded timeout; pending is never a pass.

For `local` and `real-ai`, snapshot the source data directory before/after and require byte-for-byte equality. Before cleanup, assert no Material Kit, Knowledge, Question, Memory, Wakeup/Reminder, formal InterviewNote, status, or unintended Application/Event/Resume mutation. Cleanup deletes Review Draft children, Feedback Proposals, Turns, Attempts, then synthetic events/applications/resumes; final residual assertions must be zero.

- [ ] **Step 3: Implement the browser harness with the correct network boundary.**

Start the isolated service on a verified free dynamic port. Open the local base URL, navigate top-level “面试”, select a specific scheduled event, configure Resume/JD/preparation, complete text turns, finish, select feedback, confirm, close, and reopen read-only history. Record browser requests and fail on any non-local host or direct Provider request. Separately record every server outbound Provider request, parse both it and the temporary configuration URL, and require exact equality of `(scheme, host, effective port)`; a different triple fails, while a matching configured Provider host is accepted without hostname heuristics.

- [ ] **Step 4: Run the isolated runtime checks.**

```powershell
cd web
npm.cmd run build
cd ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\mock-interview-real-ai-browser-harness.ps1
```

Expected: local and real-AI use temporary data only; browser requests are local-only; server Provider egress is separately verified; all three synthetic flows reach 200/201; the browser completes the full mock-interview flow and cleanup leaves no residual data.

- [ ] **Step 5: Commit runtime acceptance assets.**

```powershell
git add src/offerpilot/smoke.py tests/test_smoke.py tests/test_mock_interview_browser_harness.py scripts/mock-interview-real-ai-browser-harness.ps1
git commit -m "test: AI verify isolated mock interview flow"
```

## Task 7: Independent code review and final release gate

**Files:**

- Review all changed files from the task commits; modify only files required by discovered regressions.
- Modify: `scripts/windows-pytest-groups.ps1` to support one named group per invocation, a caller-supplied result directory, and a separate aggregate mode.
- Update: `docs/superpowers/plans/2026-07-28-event-bound-mock-interview.md` only with checked completion state after implementation is actually verified.
- Create: a short release verification report under `docs/superpowers/reports/` only after all gates finish.

- [ ] **Step 1: Run an independent code review.**

Provide the complete diff from the pre-implementation baseline to an independent reviewer. Require explicit checks for: old Mock route removal, no score/decision fields, database-level Attempt/Turn/draft uniqueness, two-connection lease/CAS, input/transcript fingerprint separation, Turn-answer evidence, fixed-question IDs, no raw diagnostics, no direct browser Provider access, and no cross-domain writes. Any P0/P1/P2 finding must be fixed with a regression test before proceeding.

- [ ] **Step 2: Run the backend gate as five persisted group jobs plus one aggregate job.**

The PowerShell script must accept `-Group agent|domain|knowledge|proposals|misc`, `-ResultDir $gateDir`, and `-Aggregate -ResultDir $gateDir`. A group invocation runs only its group, writes `agent.collect.txt`/`agent.junit.xml` (and the corresponding file for each group) into `ResultDir`, checks its own exit code, rejects duplicate node IDs before sorting, parses JUnit skips, and allows only the four exact symlink-permission node/reason pairs. The aggregate invocation reads the persisted files, rejects duplicate node IDs before any `Sort-Object -Unique`, compares the union to the full manifest, and verifies that every group exited zero.

Run the following from a temporary directory and remove it in `finally`; do not create a manifest in the repository root:

```powershell
$gateDir = Join-Path ([System.IO.Path]::GetTempPath()) ("offerpilot-pytest-gate-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $gateDir | Out-Null
try {
    uv run pytest --collect-only -q --disable-warnings | Out-File (Join-Path $gateDir 'full-manifest.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "full manifest collection failed with exit code $LASTEXITCODE" }
    foreach ($group in @('agent', 'domain', 'knowledge', 'proposals', 'misc')) {
        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Group $group -ResultDir $gateDir
        if ($LASTEXITCODE -ne 0) { throw "$group pytest group failed with exit code $LASTEXITCODE" }
    }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Aggregate -ResultDir $gateDir
    if ($LASTEXITCODE -ne 0) { throw "pytest group aggregate failed with exit code $LASTEXITCODE" }
}
finally {
    Remove-Item -LiteralPath $gateDir -Recurse -Force -ErrorAction SilentlyContinue
}
```

The persisted full manifest and five group node-id union must match exactly with no duplicate node IDs. Every group must exit 0. Only the four repository-approved Windows symlink-permission tests may skip, with the expected permission reason; any other skip/failure blocks release. The final report records each group exit code, JUnit path contents, collected/ran count, and allowed skips before the temporary directory is removed.

- [ ] **Step 3: Run static, frontend, and local gates.**

```powershell
uv run ruff check .
uv run mypy src
Set-Location web
npm.cmd test -- --run
npm.cmd run build
Set-Location ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
```

Record command, exit code, collected/ran count, permitted skips, and source-directory snapshot result. Do not claim a single timed-out `pytest -q` is a full pass; the grouped manifest is the gate.

- [ ] **Step 4: Run final isolated real-AI and browser gates.**

```powershell
uv run oc verify --profile real-ai --static-dir web/dist
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\mock-interview-real-ai-browser-harness.ps1
```

The report must distinguish API success from browser success. A Provider 502, timeout, unverified Proposal, incomplete 202, direct browser Provider request, unexpected domain, or residual cross-domain write is a release failure, not a degraded pass.

- [ ] **Step 5: Complete the release report and commit only verified documentation.**

The report must list each command, exit code, test total, allowed skips, independent review result, real-AI result, browser request allowlist, Provider egress target, source snapshot comparison, cleanup residual counts, and remaining risks. Update the plan checkboxes only for completed tasks.

```powershell
git add docs/superpowers/plans/2026-07-28-event-bound-mock-interview.md docs/superpowers/reports/2026-07-28-event-bound-mock-interview-release-verification.md
git commit -m "docs: AI record mock interview release verification"
```

## Self-review checklist before handing this plan to review

- [ ] Every design requirement maps to Task 1–7: destructive `0016`, Attempt/Turn/CAS, strict question/feedback/Evidence/safe-empty/diagnostics, HITL draft uniqueness, index/Pilot/history/Chinese UI, isolated API/browser/cleanup, and independent CR/release gates.
- [ ] `source_fingerprint`/`input_fingerprint` never includes Turn; `transcript_fingerprint` never triggers `source_changed`.
- [ ] Start, initial question, answer submission, next question, feedback, and review-draft confirmation each have a named key, unique scope, first/replay status, and different-payload conflict behavior.
- [ ] `safe_empty` is consistently exactly the four feedback arrays: `strengths`, `practice_points`, `follow_up_questions`, `next_practice_steps`.
- [ ] No plan step permits direct browser access to a Provider; server-side Provider egress is a separate assertion.
- [ ] No unresolved placeholder language or unspecified implementation choice remains.
- [ ] Before committing this plan, run `git diff --check` and confirm the worktree contains only this new plan file.
