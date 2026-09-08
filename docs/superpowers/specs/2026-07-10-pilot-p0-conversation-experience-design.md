# Pilot P0 Conversation Experience Design

Date: 2026-07-10

Status: approved

Branch: `feat/20260710-ai-chat-experience-research`

Research basis: `docs/archive/ai-chat-experience-research-20260710.md`

## Goal

Make Pilot reliably advance the user's current job-search task while preserving the existing v0.1 trust boundary. This design closes the highest-priority context, control, recovery, conversation-management, and evidence-quality gaps without introducing the P1 UI-message or resumable-run architecture.

The result must let a user:

- See and control the page context sent with the current turn.
- Leave a pending write in one conversation and continue a different read-only conversation.
- Edit a proposed write before execution or reject it with useful feedback.
- Find, archive, restore, and understand conversations at realistic local-data volume.
- Read compact, human-formatted evidence without losing access to distinct source records.

## Product Principles

1. Conversation is an interaction surface, not the product outcome.
2. Context must be visible and removable before it affects the model.
3. A pending write belongs to one conversation, not the whole Pilot workspace.
4. A user can approve, edit, or reject a proposed write; editing never bypasses validation.
5. High-risk tools continue to require confirmation even when auto-approve is enabled.
6. Explanations expose plans, tool facts, evidence, and effects, never hidden chain-of-thought.
7. Domain facts remain in OfferPilot repositories. Page context is a bounded UI snapshot, not a new source of truth.

## Scope

### Included

- Request-level page context for all contextual Pilot surfaces.
- Application binding when the current page has an application entity.
- Visible, removable page-context chips.
- Pending-action isolation and pending badges in the conversation rail.
- Type-aware editing of proposed write arguments.
- Optional rejection feedback.
- Conversation search, deterministic time grouping, archive view, and restore.
- Deterministic title cleanup, preserving manual rename.
- Evidence formatting, bounded rendering, and conservative grouping.
- Backend, frontend, repository, API, Agent, and UI tests for these behaviors.
- Real browser verification of the Pilot page and contextual rail.

### Excluded

- `pilot-sse-v2`, UI message `parts[]`, persistent run records, and stream reconnection.
- Message branching, regenerate-from-message, or message-level model metadata.
- Long-term user memory.
- New Agent skills or tools.
- Voice, file attachments, screenshots, and other multimodal input.
- LLM-generated conversation titles.
- Server-side conversation pagination or search.
- Arbitrary JSON editing in confirmation cards.

## Chosen Approach

Use a bounded extension of the existing P0 architecture:

- Keep `context_type/context_ref` as the persistent conversation identity.
- Keep `pilot-sse-v1` and the current chat endpoints.
- Add a request-level `page_context` object to chat requests.
- Extend confirmation requests with optional `edited_args` and `rejection_feedback` while retaining `approved`.
- Add UI-only search, grouping, and archive filtering on the existing conversation list endpoints.
- Improve evidence presentation without changing tool-result storage.

This approach solves the immediate user-control problems and preserves compatibility with current CLI/API behavior. The P1 structured-message design remains a separate migration.

## Architecture

```text
AppShell / business view
  -> buildPilotPageContext(view, selected entity, active filters)
  -> ChatPanel displays removable page-context chips
  -> streamChat(..., page_context)
  -> API validates and normalizes the bounded snapshot
  -> system message describes current UI state as data, not instructions
  -> Agent receives persistent conversation context + current page snapshot

Pending write
  -> Agent interrupt persists PendingAction
  -> ThreadRail marks only that conversation as waiting
  -> ProposalCard edits allowed args locally
  -> confirm/stream sends approved + edited_args or rejection_feedback
  -> Agent revalidates effective args after resume
  -> tool executes only when approved and validation succeeds
```

## Page Context

### Frontend Contract

Add a shared `PilotPageContext` type:

```ts
interface PilotPageContext {
  view: ViewMode;
  label: string;
  entity?: {
    kind: 'application' | 'offer';
    id: string;
    label: string;
    description?: string;
  };
  filters?: Array<{
    key: string;
    label: string;
    value: string;
  }>;
}
```

`AppShell` owns construction because it knows the current view and selected entity. In P0 it always supplies the view and any selected application or offer. A business component supplies filter summaries only when that filter state already has a stable typed representation; P0 does not lift otherwise-local filter state solely for Pilot. Raw page state, full records, secrets, and user-entered document bodies must not be copied into the context.

The contextual Pilot rail receives this object. The full Pilot tab receives no page context and remains a workspace conversation surface.

### Persistent Context Rules

- Selected application: new conversation uses `context_type=application` and `context_ref=<application_id>`.
- Offer coach: preserve the current negotiation mode and its linked application behavior.
- Other views: use `context_type=workspace` and an empty `context_ref`.
- Existing conversations keep their persistent `context_type/context_ref`; page context supplements the current turn and does not silently rewrite conversation binding.

### Request Rules

`sendChat` and `streamChat` accept optional `page_context`. ChatPanel includes it only while the page-context chip is enabled.

The backend accepts only:

- Known view identifiers.
- String fields up to explicit limits.
- At most one entity.
- At most eight filters.

Unknown keys are dropped. Invalid types return `422`. The generated system message explicitly labels the payload as current UI state and instructs the model not to treat field values as commands.

### UX Rules

- Show a `当前页面` chip before or immediately above the composer.
- Show an entity chip separately when present.
- Removing the chip affects subsequent turns in the current page session only.
- Navigating to a new view resets the local inclusion choice to enabled.
- The persistent conversation-context chip remains independently removable through the existing conversation update API.

## Pending-Action Isolation

### Initial Recovery

When Pilot first opens and no conversation has been explicitly chosen, it selects the most recently updated pending conversation so an interrupted write remains visible.

### Explicit New Conversation

After the user clicks `新建对话`:

- Do not auto-select a pending conversation.
- Show the normal empty state and enabled composer if AI is configured.
- Leave all existing pending actions persisted and visible through rail badges.

Selecting a pending conversation restores its confirmation card and disables only that conversation's composer.

### Rail Indicator

Each conversation with `pending_action` shows a `待确认` badge. The badge remains visible in search results and archive view. Archiving a pending conversation is disallowed until the action is approved or rejected, preventing an invisible unresolved write.

## Editable Approval

### API Contract

Keep the current request shape and add optional fields:

```json
{
  "conversation_id": 42,
  "approved": true,
  "edited_args": {
    "status": "offer"
  },
  "rejection_feedback": "公司名称不对，请先问我确认。"
}
```

Rules:

- `edited_args` is allowed only when `approved=true`.
- `rejection_feedback` is allowed only when `approved=false`.
- The API rejects payloads that send both.
- `edited_args` is an object merged onto the original pending args.
- Identity and target keys such as `id` are immutable unless a tool explicitly declares them editable.
- Unknown editable keys are rejected with `422`.
- Empty rejection feedback remains valid and preserves today's cancel behavior.

### Tool Metadata

Every write tool with user-editable arguments declares `editable_fields`. Tools without this declaration are fully read-only inside ProposalCard. The UI derives editors only from this allowlist and `PendingAction.proposed_changes`; it never exposes arbitrary tool JSON.

The pending-action response adds explicit editor descriptors so the frontend does not duplicate backend safety policy:

```json
{
  "editable_fields": [
    {
      "field": "status",
      "type": "enum",
      "options": ["applied", "screening", "written_test", "interview", "offer", "rejected"]
    }
  ]
}
```

Supported descriptor types are `string`, `long_text`, `number`, `boolean`, `enum`, and `datetime`. Options come from existing domain enums. A field absent from this array is immutable even if it appears in raw tool args.

Field editors preserve primitive types:

- String: input or textarea for known long-form fields.
- Number: numeric input.
- Boolean: switch.
- Application status and event type: existing domain selects.
- Date/time: existing normalized string format with validation.

Delete operations expose no editable fields.

### Agent Resume

The effective args are computed before resuming:

1. Parse original pending args.
2. Apply allowed edits.
3. Serialize effective args.
4. Run the tool's existing validator against effective args.
5. If validation fails, do not execute the tool and keep the pending action available with an inline error.
6. Resume the LangGraph interrupt with `approved` and effective args.
7. Execute the tool with effective args in both checkpoint and no-checkpoint fallback paths.

Rejected actions produce a tool result that includes the user's optional feedback, then continue through a normal model follow-up turn. The write tool is never executed.

### Draft Preservation

ProposalCard keeps edited values in local state keyed by conversation and pending tool-call ID. Network or provider errors preserve those values. Successful execution, rejection, or selecting a different completed conversation clears the draft.

## Conversation Findability

### Search

Search is client-side over loaded conversations. It matches normalized title, mode label, context label, and pending state. Search is case-insensitive and trims whitespace.

### Groups

Active conversations render in this deterministic order:

1. Pinned.
2. Today.
3. Previous seven days.
4. Earlier.

Within each group, preserve backend update ordering. Empty groups are omitted.

### Archive View

ThreadRail provides `当前` and `归档` views:

- Current view calls `listConversations(false)`.
- Archive view calls `listConversations(true)` and filters to rows with `archived_at`.
- Archived conversations can be restored with `updateConversation(id, {archived: false})`.
- Delete remains available with confirmation.

### Titles

New-conversation titles remain deterministic and do not call an LLM. Use the first non-empty normalized line, stop at the first sentence-ending punctuation when that leaves at least eight characters, and cap the result at 36 Unicode characters. Fall back to `新对话` for empty input. Do not add test-specific or language-specific prompt rules. Manual rename always wins and is never overwritten.

## Evidence Presentation

### Formatting

- Convert known ISO date/time values to local human-readable display.
- Keep raw values available in `title` attributes when useful.
- Preserve snippets with the existing length limit.

### Bounded Rendering

- Context panel renders at most five evidence groups.
- ProcessTimeline renders at most eight evidence rows per tool step before a `另有 N 条` summary.
- ProposalCard continues to show at most three directly relevant evidence rows.

### Conservative Grouping

Do not merge records solely because company or title matches. Group only when kind, stable record ID, title, and normalized metadata identify the same logical source. Repeated identical sources expose an occurrence count. Distinct IDs remain separate.

The context panel improves diversity without discarding records: it selects one representative from each normalized `(kind, title)` cluster before filling remaining slots, shows `另有 N 条同类记录`, and allows expansion to the distinct IDs. ProcessTimeline retains record-level rows subject to its eight-row display limit.

The P1 resource-reference contract will replace this heuristic later; P0 must not invent new persistent identifiers.

## Component Boundaries

- `AppShell`: builds current page context and passes it to contextual ChatPanel variants.
- `ChatPanel`: owns context inclusion, selected conversation, pending isolation, archive loading, and API orchestration.
- `ThreadRail`: owns search, groups, active/archive switch, pending badge, and restore action.
- `ProposalCard`: owns type-aware edit UI and rejection-feedback input, but not tool execution.
- `model.ts`: owns pure evidence grouping/formatting helpers and pending-selection rules.
- `chat.ts`: owns extended request payloads.
- `api.py`: validates page context and confirmation payloads, constructs system messages, and preserves pending state on invalid edits.
- `agent.py`: validates and executes effective args consistently across checkpoint and fallback paths.
- Tool registry: declares editable fields alongside existing write/validation metadata.

No component may depend on ChatPanel internals to construct tool arguments.

## Error Handling

- Invalid page context: `422`, no user message or conversation is created.
- Invalid edited args: `422`, tool not executed, pending action remains stored.
- Provider failure after edit submission: pending action and UI draft remain available for retry.
- Checkpoint missing: fallback execution uses the same effective args and validation path.
- Rejection feedback too long: `422`; cap normalized feedback at 500 characters.
- Archive restore failure: keep the item in archive view and show a readable toast.
- Evidence parsing failure: retain the tool label and existing details-unavailable fallback.

## Security And Privacy

- Page context uses allowlisted UI fields and explicit length limits.
- The backend treats page-context values as untrusted data inside a system wrapper.
- No raw API keys, full resume bodies, knowledge documents, or hidden form values enter page context.
- Edited args cannot change immutable target fields.
- Existing always-confirm and auto-approve rules remain authoritative.
- Rejection feedback is stored only as conversation/tool history and follows existing local-data behavior.

## Testing Strategy

### Backend Agent Tests

- Approved edit executes effective args, not original args.
- Invalid edit never executes the tool.
- Edited args work with SQLite checkpoint resume.
- Edited args work in no-checkpoint fallback.
- Rejection feedback appears in the tool result and tool is not executed.
- Immutable and unknown fields are rejected.

### Chat API Tests

- Valid page context reaches the model as a bounded system message.
- Invalid view, type, field count, and length return `422` without persistence.
- Application page context and persistent application context coexist.
- Both confirm endpoints accept valid edited args.
- Invalid edits preserve pending state.
- Rejection feedback clears pending state only after successful validation.
- Pending conversations cannot be archived.
- Archived conversations can be restored.

### Frontend Unit Tests

- Explicit new chat suppresses automatic pending selection.
- Selecting a pending conversation restores only that conversation's lock.
- Search and group order are deterministic.
- Archive view filters and restore payload are correct.
- ProposalCard emits type-preserving edits.
- Delete proposals do not expose edit controls.
- Context chip removal excludes page context from later requests.
- Navigation resets page-context inclusion.
- Evidence limits, counts, and date formatting are deterministic.

### Browser Acceptance

1. Open a business page and verify view/entity context chips.
2. Remove page context and confirm the next request excludes it.
3. Trigger a write, edit a proposed value, approve, and verify the saved record.
4. Trigger another write, reject with feedback, and verify no mutation.
5. Leave a pending write, create another conversation, and complete a read-only query.
6. Find the pending conversation via its badge and resume it.
7. Search conversations, archive one, open archive view, and restore it.
8. Expand tool evidence and verify bounded, human-formatted rows.
9. Verify no unexpected console warnings or errors.

## Acceptance Criteria

- Current page context is visible, removable, and actually reaches the model when enabled.
- No pending action can force navigation after an explicit new-chat action.
- A pending action disables only its conversation.
- Allowed edits execute exactly the reviewed effective args.
- Invalid or immutable edits cause zero writes and retain the pending action.
- Rejection feedback causes zero writes and is available to the follow-up model turn.
- Search finds matching conversations across all active groups.
- Archived conversations are restorable from the UI.
- Evidence panels never render unbounded rows and display human-readable dates.
- Existing write confirmation, always-confirm, undo, degraded mode, stop, retry, and conversation persistence tests continue to pass.
- Full local release gate passes; Docker limitations, if any, are reported explicitly.

## Breaking Changes

None intended.

The chat and confirmation request contracts are additive. Existing clients that send only `approved` continue to behave as before. No database reset or destructive migration is required.

## Deferred Follow-Ups

- Replace heuristic evidence parsing with versioned UI message parts and stable resource references.
- Persist active run metadata and support stream reconnection.
- Add message edit/regenerate/branch and granular feedback.
- Add user-managed long-term memory and cost metadata.
