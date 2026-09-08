# OfferPilot v0.1 Closeout Design

Date: 2026-07-10
Status: Approved in conversation; awaiting written-spec review
Canonical product source: [OfferPilot 开源 MVP 版本 Wiki](https://ycn8095q3nc7.feishu.cn/wiki/K6BQw1X5Piksm2kDex3cMQMenvf)

## 1. Context

The v0.1 implementation passes the current non-Docker release gate and its main resume, application, event, interview-placeholder, Pilot, and provider-settings flows work in an isolated browser acceptance run. The remaining closeout work is concentrated in six areas:

1. Workbench onboarding is missing the four-step checklist and reopen behavior.
2. Application entities do not provide a reliable “问 Pilot” entry into a new application-scoped conversation.
3. A new conversation title is a truncated first message instead of an LLM-generated title.
4. A rejected write can still leave the frontend showing “保存成功”.
5. Settings lacks the complete diagnostics surface required by v0.1.
6. The committed release and install gate scripts are not executable directly.

The application-detail interview review form is accepted as a v0.1 placeholder capability. It is not a blocker and this closeout does not remove or expand it.

## 2. Goals

- Close the remaining v0.1 P0 product gaps without broadening knowledge, practice, or formal interview scope.
- Make onboarding progress durable and derived from real user state rather than duplicated counters.
- Make every application “问 Pilot” action start a new conversation with explicit application context.
- Generate a useful title after the first user message without delaying the main answer or overwriting a manual rename.
- Make write-result feedback reflect the actual tool outcome.
- Complete local diagnostics while excluding credentials and user business data from copied output.
- Restore the documented direct-execution release path and rerun the complete acceptance matrix.

## 3. Non-goals

- No new `context_type` beyond `workspace` and `application`.
- No resume-scoped conversation contract.
- No knowledge or practice product expansion.
- No new formal interview-note or mock-session behavior.
- No redesign of the Pilot three-column page, application lifecycle, or resume editor.
- No long-term fine-grained resource invalidation contract; that remains a v0.3 concern.

## 4. Confirmed Product Decisions

### 4.1 Application “问 Pilot”

Every click starts a new blank conversation draft. It does not reuse a previous conversation for the same application. The first sent message creates a new persisted conversation with:

- `context_type=application`
- `context_ref=<application_id>`
- `mode=general`

The new-conversation surface displays an application context badge before the first message is sent. Clearing the badge converts that unsent draft to workspace context.

### 4.2 Onboarding Pilot milestone

The fourth onboarding milestone is complete as soon as the server accepts and records the first user message. It does not depend on a successful model response.

## 5. Considered Approaches

### Approach A: One coordinated v0.1 closeout slice — recommended

Implement the onboarding state, new contextual conversation entry, generated titles, structured write outcomes, settings diagnostics, and gate fixes in one feature branch. Reuse existing repositories and API boundaries, add only the state required for persistence, and run one end-to-end acceptance matrix.

This approach keeps the cross-cutting frontend and backend contracts aligned and makes the final Go/No-Go decision coherent.

### Approach B: Frontend-only closeout

Store onboarding and conversation-entry state in browser storage, infer write failures from response text, and compose diagnostics only in the client.

This is faster but is rejected because state would be browser-specific, copied diagnostics would drift from runtime truth, and success detection would remain fragile.

### Approach C: Independent branches per gap

Ship onboarding, Pilot entry/title, settings, and release scripts separately.

This reduces individual diff size but is rejected for this closeout because the shared settings, chat, and browser acceptance paths would need repeated integration and verification.

## 6. Detailed Design

### 6.1 Onboarding state

Add an onboarding status API backed by existing durable facts:

```text
GET /api/onboarding
PATCH /api/onboarding
```

`GET /api/onboarding` returns:

```json
{
  "steps": {
    "configure_ai": true,
    "create_primary_resume": false,
    "create_first_application": true,
    "send_first_pilot_message": false
  },
  "completed_count": 2,
  "is_complete": false,
  "force_open": false
}
```

The server derives step completion as follows:

- `configure_ai`: at least one enabled provider has a non-empty API key.
- `create_primary_resume`: a non-deleted primary resume exists.
- `create_first_application`: a non-deleted application exists.
- `send_first_pilot_message`: at least one persisted chat turn has `role=user`.

Progress is therefore durable through the existing config and database. The only new preference is `onboarding_force_open: bool` in `Config`, defaulting to `false` and migrating safely from older config files.

Visibility rules:

- Incomplete onboarding is expanded.
- Complete onboarding automatically renders collapsed unless `force_open=true`.
- “重新打开新手引导” in Settings sets `force_open=true`.
- The expanded completed checklist offers a collapse action that sets `force_open=false`.

The dashboard refreshes onboarding status after provider configuration, primary-resume changes, application creation, and user-message submission. A failed model request does not undo the fourth step because the accepted user turn remains persisted.

### 6.2 Application-scoped new conversation

Add one shared application action for the Kanban card, application list row, and application detail header. The action delegates to `AppShell` rather than each surface owning chat state.

`AppShell` creates a new chat-session request containing an incrementing request key and the application context. `ChatPanel` handles a new request by:

1. cancelling any active request;
2. resetting `conversation_id` to `0`;
3. clearing turns, pending actions, errors, undo state, and streaming state;
4. setting the application context badge;
5. focusing the composer.

No empty conversation is stored on click. The first submitted message uses `conversation_id=0`, causing the existing chat API to create exactly one new persisted conversation with the supplied context.

This flow must work in both the contextual Pilot rail and the full Pilot page. It must never silently continue the previously active conversation.

### 6.3 LLM-generated first-message title

Keep the current 30-character truncation as an immediate fallback title so a new conversation is never unnamed. Add a conversation `title_source` value with these states:

- `fallback`: initial truncated title;
- `generated`: title created by the configured model;
- `manual`: title set by the user.

After the first user message is recorded, schedule a separate title-generation call. The prompt asks for a concise Chinese conversation title and does not include unrelated workspace data. Generation does not block the main assistant stream.

The generated title is committed only if the conversation still has `title_source=fallback`. Manual rename changes the source to `manual`, preventing a late background result from overwriting the user's title. A provider error, timeout, empty result, or process shutdown leaves the fallback title intact and does not fail the conversation.

The title response is normalized to one line and capped at 30 characters. Conversation-list refresh picks up the generated title after completion.

### 6.4 Structured write outcomes

Extend the final chat response and SSE completion payload with a structured write outcome:

```json
{
  "write_status": "success | failed | cancelled | none",
  "write_error": "optional user-safe reason"
}
```

Rules:

- `success`: the repository mutation committed; the frontend may show “保存成功”, refresh workspace data, and expose undo when supported.
- `failed`: validation, lifecycle, repository, or tool execution rejected the write; the frontend shows “保存失败” plus `write_error` and does not refresh as if data changed.
- `cancelled`: the user rejected the proposal; the frontend returns to idle without a success message.
- `none`: the response contains no write attempt.

The frontend must not infer success from HTTP 200 or assistant prose. Confirmation transport errors continue to use the existing retry/cancel recovery dock.

### 6.5 Settings and diagnostics

Extend the settings response with the resolved data-directory path. Do not expose API keys or auth tokens.

Add these Settings controls:

- log-level filter: all, info, warning, error;
- “复制诊断信息”;
- resolved data-directory display;
- “重新打开新手引导”.

The logs API accepts an optional normalized `level` filter and applies it before the result limit.

Copied diagnostics contain:

- app version;
- runtime mode and auth-enabled state;
- log level;
- resolved data directory;
- provider label, provider type, model, enabled state, and active/fallback role;
- the filtered recent log entries visible in the panel.

Copied diagnostics exclude API keys, auth tokens, message content, resumes, applications, and other business records. Clipboard failure produces an actionable UI error and leaves the text available for manual copying.

### 6.6 Release scripts

Commit executable mode `100755` for:

- `scripts/release-gate.sh`
- `scripts/install-gate.sh`

The documented direct command `./scripts/release-gate.sh` becomes the canonical invocation. The `--install` path must invoke the install gate successfully without relying on `bash <script>` as a workaround.

## 7. Compatibility and Migration

- Adding `onboarding_force_open` to `Config` is backward compatible because missing fields use the default.
- Adding `title_source` uses the repository's existing additive SQLite migration pattern. Existing conversations are marked `manual` so a later request cannot unexpectedly retitle them.
- Chat response fields are additive. Older clients ignore them; the updated frontend uses them for authoritative write feedback.
- No application, resume, event, note, or mock-session data is reset.

## 8. Error Handling

- Onboarding-status failure leaves the dashboard usable and shows a retryable checklist error instead of treating steps as incomplete.
- Starting a new contextual draft cancels any active stream before local chat state is reset.
- Title-generation failure is logged without credentials and retains the fallback title.
- A failed write remains visible in conversation history and never exposes undo or a success badge.
- Diagnostics-copy failure shows a copy error and preserves selectable diagnostic text.
- Missing Docker is reported as an unexecuted release condition, not as a passed smoke test.

## 9. Testing Strategy

### Backend

- Onboarding derivation for each step, all-complete state, and `force_open` updates.
- First user turn completes onboarding even when the model call fails.
- New application-context conversation stores the expected `context_type/context_ref`.
- Generated title success, timeout/error fallback, normalization, and manual-rename race protection.
- Write outcomes for success, lifecycle rejection, user cancellation, and transport failure.
- Logs level filtering and secret-free settings/diagnostics payloads.
- Config and SQLite migration from the previous schema.

### Frontend

- Checklist progress, auto-collapse, completed reopen, and refresh triggers.
- Every application “问 Pilot” surface resets to a blank conversation and preserves the selected application context.
- Repeated clicks create separate conversations after messages are submitted.
- Failed write renders failure and does not render “保存成功” or refresh mutation-dependent views.
- Settings filters logs, copies redacted diagnostics, shows the data directory, and reopens onboarding.
- Generated and fallback conversation titles refresh correctly without losing manual rename.

### Full gate

Run:

```bash
./scripts/release-gate.sh --install
./scripts/release-gate.sh --docker
```

If one machine cannot provide Docker, run the non-Docker and install gates locally and record a passing Docker smoke from a machine that has Docker before declaring v0.1 accepted.

## 10. Browser Acceptance Matrix

Use a fresh isolated data directory and a deterministic OpenAI-compatible test provider.

1. Empty workspace shows the expanded four-step onboarding checklist.
2. Provider setup, primary resume creation, and first application creation complete the first three steps.
3. Sending a Pilot message completes the fourth step even if the provider response fails.
4. Completing all steps automatically collapses onboarding; Settings reopens it.
5. “问 Pilot” from two different application surfaces always opens a blank draft with the correct context badge.
6. Sending from each draft creates a distinct application-scoped conversation.
7. A generated title replaces the fallback; a simulated provider failure retains the fallback; manual rename wins a race.
8. A valid write shows success and changes data.
9. Reopening a closed application is rejected, remains closed, and shows failure without any success indicator.
10. Settings filters logs, copies redacted diagnostics, displays the data directory, and contains no credentials.
11. Existing resume, application/event, interview-placeholder, Pilot streaming/HITL, provider, backup, and session-restore flows remain green.

## 11. Acceptance Decision

The release is Go only when:

- all six closeout areas in this design are implemented;
- the backend and frontend test suites, lint, type checking, build, local smoke, and install gate pass;
- the browser acceptance matrix passes on isolated data;
- Docker smoke passes on a Docker-capable machine;
- the repository is clean and no verification artifacts are tracked.

Real-provider verification may be recorded separately when credentials are unavailable. Deterministic provider coverage remains mandatory and real-provider absence must be disclosed in the acceptance report.
