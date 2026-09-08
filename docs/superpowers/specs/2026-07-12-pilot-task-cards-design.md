# Pilot Task and Structured Action Cards

## Status

Approved design. Scope is the Pilot conversation only.

## Problem

Pilot currently persists assistant text and tool calls, but renders them as a
Markdown bubble followed by a separate expandable process timeline. Users must
scan across two visual patterns to understand the task, what happened, the
conclusion, and what to do next. There is no durable, per-turn task summary.

The Chat API is buffered: it returns after the agent finishes and does not emit
tool-progress events. The UI must not present invented live tool progress.

## Product decisions

- A card describes one completed Pilot turn, not a cross-turn project.
- Cards appear only in Pilot, not on the dashboard, reminders, or other pages.
- The selected layout is one unified task card: progress, conclusion, and next
  actions share one visual container.
- Clicking a next action sends a focused follow-up prompt to Pilot. It never
  writes data directly; any write remains behind the existing confirmation
  flow.
- Existing and malformed messages retain their current Markdown rendering.

## Approaches considered

### A. Pure frontend heuristic

Infer a conclusion and actions from arbitrary prose. This avoids prompt work
but produces unreliable cards and cannot distinguish supporting detail from a
true conclusion.

### B. Lightweight Markdown response contract (selected)

For substantive task responses, the existing Pilot system prompt asks for
`## 结论` and `## 下一步` sections. The frontend parses these sections and
combines them with persisted tool calls. This keeps the existing Chat API and
database schema intact, works when a conversation reloads, and has a safe
plain-Markdown fallback.

### C. Persisted response metadata

Add a structured response field to the database and Chat API. This is the most
strict option but requires a migration, API compatibility work, and model output
validation beyond the scope of this interaction improvement.

## User experience

### Completed task card

When an assistant reply has real tool steps or parses into a meaningful
conclusion/action payload, render one `PilotTaskCard` before any supporting
Markdown detail. It contains:

1. A concise task title derived from the initiating user message, truncated for
   the rail.
2. A completed status and a real step count.
3. A compact list of completed steps derived from `ToolStep` and existing tool
   metadata. Each step preserves the current evidence expansion path.
4. A `结论` section containing the parsed conclusion.
5. A `下一步` section containing one to three prompt buttons.

The parser removes these structured sections from the Markdown bubble so the
conclusion and actions are not duplicated. Any remaining explanation, caveat,
or evidence narrative continues to render with the existing Markdown renderer.

If an answer needs no tools but does have structured sections, the card uses a
single completed `整理建议` step. If an answer contains neither tool steps nor a
valid structured payload, no card is rendered.

### During execution

The existing thinking indicator remains the loading state. Its wording may
describe that Pilot is preparing the reply, but it does not claim that a named
tool has run. After the buffered response arrives, it is replaced by the
completed card built from persisted messages and actual tool calls.

### Follow-up actions

An action button invokes the existing `sendMessage` path with a focused
follow-up such as `继续处理：生成技术一面准备清单`. Buttons are disabled under
the same loading, pending-confirmation, and missing-key conditions as the
composer. The next turn can still require confirmation for any write tool.

## Response and presentation contract

For task-like answers that have a conclusion and useful follow-up, the system
prompt asks the model to use this shape:

```markdown
## 结论

One concise conclusion grounded in the reply.

## 下一步

- A focused follow-up the user can ask Pilot to perform.
- Another focused follow-up.
```

The frontend parser accepts level-two or level-three Chinese headers, trims
empty entries, limits action labels to three, and rejects unparseable sections.
It returns a typed presentation object:

```ts
interface TurnPresentation {
  conclusion?: string;
  actions: string[];
  detailMarkdown: string;
}
```

`buildTurns` combines this object with the existing `steps` field. Because both
the answer text and tool calls are already persisted, reloading a conversation
recreates the same task card without a schema or API change.

## Error handling and compatibility

- Missing, duplicate, or malformed sections never block a response; the
  original Markdown bubble is preserved.
- Old conversations remain readable and do not gain fabricated cards.
- A pending write continues to render the existing confirmation card and keeps
  the composer disabled.
- Provider degradation remains visible through existing UI. It does not bypass
  action-button or write-confirmation guards.

## Accessibility and visual constraints

- The card uses semantic sections and labelled action buttons.
- Step progress includes text such as `已完成 2 步`; color alone never conveys
  completion.
- Action buttons remain keyboard reachable and preserve the current rail's
  narrow-width behavior.
- Evidence disclosure continues to use the existing accessible process/evidence
  controls rather than duplicating source details.

## Implementation boundary

Frontend work is limited to ChatPanel presentation parsing, task-card rendering,
styling, and interaction plumbing. Backend work is limited to the Pilot system
prompt's response-format instruction. No database migration, model change, or
Chat API shape change is planned.

## Verification

- Parser unit tests: structured sections, alternate header level, empty or
  malformed input, action cap, and detail preservation.
- Turn-builder tests: actual tool steps plus structured answer, no-tool
  structured answer, and old plain answer fallback.
- Component tests: completed card semantics, action click routes through the
  normal send handler, disabled state, and non-duplication of Markdown.
- API regression test: the system instruction preserves existing chat and
  pending-write confirmation behavior.
- Browser walkthrough: a multi-tool Pilot reply shows the unified card; an
  action starts a normal follow-up; a write still requires confirmation.

## Breaking changes and residual risk

There are no database or API breaking changes. The model may omit the requested
Markdown sections; the deterministic fallback is the current message UI, so the
result remains usable but unstructured for that turn.
