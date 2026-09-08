# Write Operation Ledger Implementation Plan

**Goal:** Implement the approved phase-three Write Operation Ledger for all 12 typed writes, 3 deterministic legacy writes, and 4 compensation operations without changing the provider-visible tool manifest.

**Architecture:** Add migration `0026_write_operation_ledger`, a fail-closed HMAC identity domain, immutable operation/transition repositories, session-bound domain repositories, and a coordinator that owns `BEGIN IMMEDIATE`, SAVEPOINT execution, terminal result validation, replay, delivery fencing, and compensation. Persist proposal identity with Pending Action, remove persistent LangGraph checkpoints from Chat/HITL, and make HTTP/SSE confirmation route Ledger-first.

**Execution rules:** Work only in `feat/20260819-write-operation-ledger`; keep provider and journal goldens unchanged; use test-first focused increments where practical; do not push or merge; finish with an independent sub-agent review, the repository release gate, and one required conventional commit.

## Task 1: Freeze scope and add ledger contracts

- Add the closed 12/3/4 operation manifest, WriteContract/UndoPolicy declarations, canonical codecs, HMAC request identities, terminal digest verification, bounded payload validation, deterministic compensation UUIDs, and transient execution/delivery result types.
- Add focused manifest, canonicalization, privacy, UUID, and terminal-integrity tests.

## Task 2: Add schema and migration 0026

- Add `WriteOperation` and `WriteOperationTransition` models.
- Add Conversation pending/last-operation identity and ChatMessage delivery identity.
- Add role/status/delivery/byte-size checks, unique indexes, foreign keys, and cross-row triggers.
- Cover fresh database, 0025 upgrade, repeat initialization, deletion retention, and lazy Pending adoption.

## Task 3: Build the fail-closed ledger repository

- Add independent `write-operation-ledger.key` loading/creation and missing-key-with-existing-data protection.
- Implement proposal, terminal replay, transition append, delivery owner CAS/heartbeat/takeover, manifest verification, and compensation proposal helpers.
- Ensure raw arguments, tokens, owner tokens, exception text, prompts, and model answers are not stored in Ledger diagnostics.

## Task 4: Add session-bound domain repositories

- Add `bind(session)` variants for Applications, Application Events, Notes, Offers, Resumes, Application JD versions, Application Outcomes, and Chat.
- Keep public repository methods self-committing while bound methods only query/add/execute/flush.
- Add tests proving bound writes roll back with the caller and public methods still commit.

## Task 5: Implement coordinator execution

- Implement `BEGIN IMMEDIATE` primary approve/reject handling, mutable recheck, Pending claim, approved/claimed transitions, SAVEPOINT executor, result/transport/undo validation, committed/failed classification, and commit-unknown reconciliation.
- Ensure required undo is generated before domain commit and infrastructure/internal failures leave the operation proposed.
- Add per-operation and concurrency/crash tests with executor-count assertions.

## Task 6: Implement delivery and continuation fencing

- Add 120-second owner leases, 30-second heartbeat, generation/token fencing, atomic operation-bound message delivery, chained Pending proposal creation, deterministic fallback, and expired-owner takeover.
- Validate ordered delivery messages and manifests on replay; never rerun Provider/read tools during replay or recovery.

## Task 7: Cut over Chat/HITL and legacy actions

- Generate and atomically persist operation IDs with typed and deterministic Pending Actions.
- Route sync/SSE approve/edit/reject terminal-first through Ledger and replay stable terminal responses.
- Remove `checkpoint_path`, `SqliteSaver`, `Command(resume)`, and checkpoint interrupt identity from Chat/HITL; use request-scoped `InMemorySaver` and fresh continuation.
- Route all three legacy writes through the same coordinator without exposing them to the provider manifest.

## Task 8: Cut over compensation and frontend controls

- Replace direct undo payload execution with deterministic compensation operations tied to `last_write_operation_id` and immutable Ledger undo.
- Add `operation_id`, `parent_operation_id`, and `replayed` transport fields while preserving existing visible write status and messages.
- Update frontend types/services so retries retain the exact confirmation request field shape.

## Task 9: Mechanical gates and compatibility tests

- Prove the exact 12/3/4 manifest, required undo set, no coordinator bypass, no bound commit/rollback/new Session, no persistent checkpoint path, no raw owner token persistence, and no feature-flag/dual-write fallback.
- Re-run provider, tool outcome, journal sequence, sync/SSE, HITL, legacy, and compensation goldens.

## Task 10: Review, verification, and commit

- Run an independent sub-agent review and resolve every P0/P1/P2.
- Run focused ledger suites, all backend/frontend groups, Ruff, Mypy, frontend build, static smoke, local smoke, and local verify. Run controlled real-AI verification only when credentials and authorization are available.
- Run `git diff --check`, confirm no untracked files, write the release verification report, stage changes, and commit as `feat: AI add write operation ledger`.

**Status: completed on 2026-08-19.** Independent review is Go with no open
P0/P1/P2. The complete backend/frontend gates, static/local/real-AI verification,
browser acceptance, exact evidence locations, breaking changes, and residual risks are
recorded in `docs/reports/2026-08-19-write-operation-ledger-release-verification.md`.
