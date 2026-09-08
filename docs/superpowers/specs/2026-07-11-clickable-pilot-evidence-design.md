# Clickable Pilot Evidence Design

## Goal

Make Pilot's existing evidence rows actionable. A user should be able to open
the original local record from an AI answer, rather than manually searching
for the application, Offer, resume, or event that informed it.

This is an explainability improvement, not a new model capability. It exposes
only records returned by local tools and never asks the model to create or
interpret citations.

## Current State

Pilot already normalizes tool results into `EvidenceItem` rows and shows them
in the process timeline, the current-thread evidence panel, and write
confirmation cards. Those rows have labels and snippets but no stable,
actionable record reference. They are therefore informative but not
traceable.

## Options Considered

1. Keep evidence read-only and add more metadata. This is low-risk, but does
   not remove the user's search work and does not meet the traceability goal.
2. Add client-side, record-backed links to the four primary local record
   types. This reuses the existing application drawer and record views, adds
   no model latency, and is the chosen approach.
3. Ask the model to emit markdown citations. This would be provider-dependent
   and could produce incorrect or fabricated links, so it is out of scope.

## User Experience

- An evidence row is a keyboard-accessible button only when it has a validated
  local target. Its accessible label states the record type and title.
- Clicking an application opens its existing detail drawer while keeping Pilot
  open.
- Clicking an Offer opens the Offer centre and that Offer's existing detail
  editor.
- Clicking a resume opens the resume library and that resume's existing
  editor.
- Clicking an event opens the calendar at its date, selects that day, and
  visually focuses the event in the day's list. It does not open an edit form
  or change the event.
- Rows without a resolvable target remain ordinary, non-clickable evidence.
  This includes knowledge excerpts, unstructured tool output, missing ids,
  and records deleted after the conversation was saved.
- If a referenced record cannot be found locally, the UI keeps the chat in
  place and gives a short unavailable notice; it must not open a blank editor
  or fall back to a different record.

## Data Model And Flow

`EvidenceItem` gains an optional client-only `target` value:

```ts
type EvidenceTarget =
  | { kind: 'application'; id: number }
  | { kind: 'offer'; id: number }
  | { kind: 'resume'; id: number }
  | { kind: 'event'; id: number; scheduledAt: string };
```

`model.ts` derives this value from the structured tool result at the same time
it derives the display title and metadata. A target is emitted only for a
finite positive numeric id; event targets also require a parseable scheduled
time so the calendar destination is exact. The model response text, an
evidence title, and any client-provided label are never treated as an
identifier.

`EvidenceList` accepts an optional `onOpenEvidence` callback and renders a
button only for an item carrying `target`. `ProcessTimeline`, `MessageBubble`,
`ProposalCard`, and `ContextPanel` pass the callback through so all three
existing evidence surfaces behave identically.

`ChatPanel` exposes one `onOpenEvidence` callback to `AppShell`. `AppShell`
owns navigation and record focus state because it already owns the active view,
application-detail drawer, and Pilot visibility. It resolves targets as
follows:

| Target | Destination |
| --- | --- |
| application | Existing application detail drawer |
| offer | Offer centre plus the existing Offer editor for that id |
| resume | Resume library plus the existing resume editor for that id |
| event | Calendar at the event's date, selected day, focused event row |

Offer, resume, and calendar views receive narrowly scoped optional focus props.
They use their already-loaded query data to resolve a record. A missing record
is reported to the caller rather than being substituted. Calendar focus only
changes view state; normal event editing remains user initiated.

## Boundaries

- No backend schema, API, conversation persistence, or provider prompt changes.
- No hidden reasoning or model-generated citation syntax.
- No new long-lived chat memory.
- Knowledge documents and JD analyses remain display-only in this release:
  their current views do not expose a consistent, safe record-detail target.
- The feature preserves the existing confirmation/HITL boundary. Opening an
  evidence target never confirms, edits, or creates data.

## Error Handling And Accessibility

- All clickable rows use native buttons, visible focus treatment, an
  `aria-label`, Enter/Space activation, and a concise hover affordance.
- Invalid ids, missing event times, and malformed tool results produce no
  target and cannot trigger navigation.
- If an item has a target but no longer exists, the destination reports the
  missing record and leaves the current chat and selected record unchanged.
- Focus changes do not automatically scroll the chat stream or dismiss Pilot.

## Verification

- Unit-test evidence normalization for application, Offer, resume, and event
  targets; malformed, missing, and non-positive ids must remain non-clickable.
- Render-test `EvidenceList` for button semantics, callback forwarding, and
  the read-only fallback.
- Test each destination's focus contract: correct local record opens; missing
  records do not open an editor; event focus selects the right calendar day.
- Run focused frontend tests, the frontend build, and the relevant Python/API
  regression suite. Before integration, run the repository release gate where
  practical.

## Breaking Changes And Risks

There are no API or persistence changes. The main residual risk is that old
stored tool results lack a usable numeric id; those records intentionally keep
their current read-only presentation. The implementation must also keep view
focus state one-shot so returning to a module does not repeatedly reopen an
editor.
