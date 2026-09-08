# Pilot Context Attachments Design

**Status:** Approved direction; pending user review before implementation

## Goal

Let a user drag a persistent business card into the currently open Pilot conversation so that its latest source data becomes explicit, removable context for the next request. When the composer is empty, offer safe, attachment-aware question suggestions.

The interaction must never send a message, execute a tool, or mutate business data merely because a card was dropped.

## Scope

### First release

- Application cards
- Offer cards
- Resume cards
- A Pilot composer attachment rail that accepts drops and exposes removable chips
- At most five attachments at a time
- Up to three attachment-aware quick questions when the composer is empty
- Keyboard and touch-accessible “add to Pilot context” alternatives to drag-and-drop

### Deferred

- Application-event cards: valuable for interview/deadline preparation, but add after the first-release attachment contract is stable.
- JD analyses, knowledge documents, review notes, and question cards: require stable reference and summarization policies before they can be safely attached.
- Unsaved form drafts, aggregate dashboard/KPI cards, Kanban columns, and derived action cards: these are not stable source entities and remain page context rather than attachments.
- The v0.1 interview placeholder: it has no formal persistent interview-record contract.

## Interaction Contract

```text
Persistent card
  -> explicit drag handle or “添加到 Pilot 上下文” action
  -> active Pilot attachment rail
  -> removable reference chip
  -> user edits or selects a suggested question
  -> user explicitly sends
  -> backend resolves current source records by reference
```

Attachments apply to the current conversation draft only. They do not auto-create a conversation, do not carry to another conversation, and clear after a successful send. The sent request records attachment labels in user-visible context metadata so a later reader can understand why the answer used those sources; the P1 structured-message migration will replace this lightweight audit representation.

The existing page-context chips remain separate. Page context answers “where is the user?”; attachments answer “which explicit records did the user provide?”.

## Data Boundary

Introduce an additive request-only attachment shape:

```ts
type PilotAttachmentKind = 'application' | 'offer' | 'resume';

interface PilotContextAttachment {
  kind: PilotAttachmentKind;
  id: string;
  label: string;
}
```

The browser sends only this reference tuple. It must not send full card payloads, resume text, compensation details, or stale client snapshots. The API validates kind, count, ID shape, and duplicate identity, then resolves authorized current data server-side. Invalid or missing records become visible, non-fatal context warnings rather than model instructions.

## Drag and Accessibility Design

The Kanban already uses dragging to change status, so its whole-card drag gesture cannot be reused. Each supported card receives a dedicated context-drag handle. The Pilot attachment rail becomes visibly droppable only while a compatible drag is active.

Every draggable action also has a keyboard/touch alternative in the card menu: “添加到 Pilot 上下文”. The attachment rail exposes removal buttons with accessible labels and announces attachment-limit or unsupported-card feedback.

## Quick Questions

When attachments exist and the composer is empty, show no more than three suggestion chips. Clicking a chip fills the composer but does not send it. Typing hides the suggestions; adding or removing attachments recomputes them.

| Attachment set | Suggested intent |
| --- | --- |
| Application | next steps, preparation gaps, follow-up plan |
| Offer | decision risks, response checklist, negotiation points |
| Resume + application | fit gap, highest-value edits, self-introduction outline |
| Multiple applications/offers | trade-off comparison, prioritized plan, shared gaps |

Suggestions must be read-only intents: no prompt that directly performs a write action is offered.

## Acceptance Criteria

1. Dropping or adding a supported card shows one removable attachment chip in the active Pilot conversation.
2. Duplicate attachments are collapsed by `(kind, id)`; a sixth attachment is rejected with clear feedback.
3. Drop alone has no network write, message send, or tool execution side effect.
4. The next submitted chat request contains reference-only attachments; the backend resolves current records and does not trust client card fields.
5. Switching conversations does not leak draft attachments.
6. Empty-composer suggestions are attachment-aware, fill-only, recompute after changes, and never send automatically.
7. Kanban status drag continues to work independently of the dedicated context-drag handle.
8. Keyboard and touch users can add and remove every supported attachment without drag-and-drop.

## Preconditions and Risks

- Fix the observed real-browser stream recovery issue before launch: a persisted assistant reply can currently remain visually stuck in “thinking”, and reselecting the saved conversation may not render its messages.
- Limit attachment count and resolve only server-side references to bound prompt size, protect privacy, and prevent stale context.
- Keep writes behind the existing HITL confirmation flow; attachments are context, not authorization.
