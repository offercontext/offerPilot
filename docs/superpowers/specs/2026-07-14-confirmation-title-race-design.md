# Confirmation Continuation Title Race Design

Date: 2026-07-14
Status: approved and implemented
Scope: chat confirmation persistence only

## Problem

The chat confirmation flow intentionally uses `Conversation.updated_at` as an
optimistic-concurrency generation. `resolve_pending_confirmation()` returns the
generation after atomically recording the tool result; the continuation write
must observe that same generation before it persists assistant output or a
follow-on confirmation.

For a newly created conversation, title generation runs as a background task.
`apply_generated_title()` currently updates `updated_at`. If it runs between
the resolve and continuation steps, it changes the generation without changing
the conversation's semantic state. The continuation is then rejected as stale
with HTTP 409, even though no conflicting user action occurred.

## Decision

Treat `updated_at` as the generation for conversation content and confirmation
state, not derived display metadata. Generated-title writes will update only
the title fields and will no longer update `updated_at`.

The existing confirmation compare-and-swap remains unchanged. Appending a
message, changing pending state, archiving, or another stateful update still
changes `updated_at` and invalidates an obsolete continuation.

## Alternatives Considered

| Alternative | Decision | Reason |
| --- | --- | --- |
| Do not advance `updated_at` for generated titles | Adopt | Focused fix that preserves the current protection for semantic concurrent updates. |
| Add a separate confirmation-generation column | Defer | Correct but requires a schema migration and broadens the state contract beyond this race. |
| Relax or remove the continuation CAS | Reject | Would mask the race by weakening protection against actual concurrent conversation changes. |

## Behavioral Contract

1. A generated title must not make a resolved confirmation continuation stale.
2. A semantic conversation mutation after resolution must still make the same
   continuation stale.
3. Replaying a consumed continuation must remain rejected.
4. Generated titles must retain their current fallback-only condition and
   title-source behavior.

## Test Plan

Add a repository regression test that resolves a pending confirmation, applies
a generated title before continuation persistence, and verifies that the
continuation is stored successfully. Keep the existing stale-generation and
single-use continuation tests as the proof that genuine contention remains
fail-closed.

Run the targeted repository tests, the chat API tests, static checks relevant
to the edited module, and the existing `oc verify --profile real-ai` gate using
the configured provider. The real-AI gate must complete a confirmation-driven
write without an unexpected 409.
