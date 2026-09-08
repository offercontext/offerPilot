# AI Assistant Explainability Design

## Goal

Improve the OfferPilot AI assistant workbench so users can quickly understand what the assistant did, which local data it used, and why a write action needs confirmation.

## Product Principle

The assistant should not expose model chain-of-thought. It should expose verifiable work: tool steps, source records, important fields, proposed mutations, and recovery paths. The user should be able to answer three questions in a few seconds:

- What did the assistant inspect?
- Which evidence supports this answer?
- Will the assistant change my data, and what exactly will change?

## Scope

This feature focuses on the existing ChatPanel workbench experience. It keeps the current three-pane layout:

- Left: conversation rail.
- Center: chat stream, assistant responses, tool process timeline, and composer.
- Right: context panel, repurposed toward current-thread evidence and safety state.

The initial implementation should strengthen explainability without rebuilding the whole assistant, changing provider behavior, or adding chain-of-thought logging.

## UX Model

### Layer 1: Step Skeleton

Assistant answers show a compact process timeline by default. Each step is named in user-facing language, such as:

- Read application records.
- Search knowledge base.
- Review offer details.
- Prepare status update.

This layer stays short and scannable. It answers "what happened" without requiring the user to expand anything.

### Layer 2: Evidence Details

Each process step can expand to show evidence that came from local OfferPilot data. Evidence should be concrete and user-verifiable:

- Application: company, role, status, source, applied date.
- Event: type, title, scheduled time, linked application.
- Interview note: round, date, question summary, weak point summary.
- Knowledge document: title, knowledge base name, matched excerpt.
- Offer: company, role, total compensation, deadline, negotiation status.

Evidence excerpts should be concise. The UI should prefer field labels and short snippets over large raw JSON blocks.

### Layer 3: Risk And Confirmation

When the assistant proposes a write action, the confirmation card should show:

- The action name.
- The target record.
- Current value and proposed value when available.
- Evidence used to justify the change.
- Clear confirm and cancel actions.
- A warning state when evidence is thin or missing.

The confirmation card is the main trust boundary. It should be more explicit than normal assistant messages, because it is where the assistant moves from advising to mutating local data.

## Information Architecture

### Center Stream

The center stream remains the main workspace. Each assistant turn can contain:

- Markdown response.
- A collapsed process timeline.
- Expandable evidence rows inside each tool step.
- A pending action card when a write operation requires approval.

The stream should remain readable when evidence is collapsed. Expanding evidence should not push the composer off-screen unexpectedly.

### Context Panel

The right panel becomes the current conversation evidence surface. It should show:

- Active mode: general assistant, negotiation coach, or mock interview.
- AI configuration state.
- Auto-approve state.
- Current thread evidence summary.
- Common capability shortcuts.

If no evidence has been collected yet, show a quiet empty state that invites the user to ask a question or pick a capability.

### Responsive Behavior

Desktop keeps the three-pane layout. Medium screens hide the context panel behind the existing toggle. Small screens hide the conversation rail and keep evidence available through the context toggle or inline timeline expansion.

## Data Model

The frontend already receives stored `tool_calls` and tool-result messages. The first implementation should derive explainability from existing chat history where possible:

- `tool_calls` provide step names and arguments.
- Following `tool` messages provide raw tool results.
- `buildTurns` can attach normalized process steps to assistant turns.

If existing tool results are not structured enough for readable evidence, backend tool handlers should return compact JSON objects or arrays that include stable fields needed by the evidence UI. The API does not need to expose model internals.

## Component Design

### ProcessTimeline

Extend the existing process timeline so each step can render:

- Tool icon and label.
- Read/write badge.
- A compact detail line derived from arguments or result evidence.
- Expandable evidence list when available.

### Evidence Renderer

Add a focused renderer that converts normalized evidence into small, consistent rows:

- Title line: primary entity name.
- Metadata line: status, date, role, source, or amount.
- Optional snippet: short excerpt from notes or knowledge documents.

This renderer should be generic enough for application, event, note, knowledge, offer, and unknown evidence types.

### ProposalCard

Upgrade the write confirmation card to show:

- Action summary.
- Target record.
- Before and after values when known.
- Evidence list.
- Thin-evidence warning when the tool call lacks enough context.

The card should preserve the current confirm/cancel flow and continue to block the composer while pending.

### ContextPanel

Add a current-thread evidence section. It should aggregate evidence from visible turns and show the most recent sources first. Keep capability shortcuts available below the evidence section.

## Visual Direction

Use a quiet, dense SaaS workbench style aligned with the current OfferPilot UI:

- No decorative chatbot-heavy treatment.
- Clear typographic hierarchy.
- High contrast text and visible focus states.
- Icon-based tool affordances using the existing Ant Design icon set.
- Stable dimensions for timeline rows, evidence rows, and confirmation controls.
- Subtle transitions limited to opacity, transform, and row expansion.

Frontend design skill synthesis:

- Accessibility first: dynamic errors and pending states need visible text and ARIA-friendly structure.
- Interaction: all clickable rows need pointer cursor, hover state, and keyboard-accessible controls.
- Layout: avoid horizontal scroll on mobile; preserve readable 16px body text on narrow screens.
- Polish: use tabular numbers for dates and compensation values, `text-wrap: pretty` for snippets, and scale `0.96` for press feedback where appropriate.
- Motion: respect `prefers-reduced-motion` and avoid `transition: all`.

## Error Handling

When evidence parsing fails, the UI should still show the step label and a short "details unavailable" message. It should not crash or hide the assistant answer.

When the assistant returns an unknown tool name, show the raw tool name as a fallback label and classify it as a read step unless metadata says otherwise.

When a write action lacks target or before/after values, show the action summary and a warning that the user should review the proposed change carefully before confirming.

## Testing Strategy

Backend:

- Preserve existing chat API confirmation tests.
- Add tests only if backend evidence normalization or tool result shape changes.

Frontend:

- Add unit tests for chat turn normalization from stored messages into timeline steps and evidence.
- Add unit tests for unknown tool and malformed result fallback behavior.
- Build verification with `npm run build`.

Manual UI checks:

- Empty assistant panel.
- Assistant answer with one read step.
- Assistant answer with multiple read steps.
- Pending write confirmation with before/after values.
- Pending write confirmation with thin evidence warning.
- Responsive widths around 375px, 768px, 1024px, and desktop.

## Non-Goals

- Expose hidden chain-of-thought.
- Build a full audit-log system.
- Replace the existing ChatPanel layout.
- Add new AI providers or model routing.
- Implement automatic undo for write actions in this first pass.

## Open Decisions Resolved

- Primary design direction: evidence-level explainability.
- Default view: step skeleton collapsed by default.
- Risk layer: shown primarily for write confirmations and strategy-sensitive recommendations.
- Visual direction: quiet, high-density workbench rather than a decorative chatbot.
