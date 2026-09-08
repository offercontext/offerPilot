# Clickable Pilot Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Pilot evidence row open the exact application, Offer, resume, or scheduled event that supplied it.

**Architecture:** Keep citations fully client-side. `model.ts` converts structured tool output into a validated `EvidenceTarget`; the evidence renderer exposes it as an accessible action; `AppShell` changes the active view or opens an existing detail surface. Destination views consume focus state once, so opening evidence cannot create, edit, or repeatedly reopen data.

**Tech Stack:** React 18, TypeScript, Vite/Vitest, TanStack Query, Ant Design, Day.js, existing OfferPilot local tool-result model.

---

## File Structure

- `web/src/components/ChatPanel/model.ts` — defines validated local evidence targets while retaining the current display normalization.
- `web/src/components/ChatPanel/model.test.ts` — proves targets are derived only from safe structured IDs.
- `web/src/components/ChatPanel/EvidenceList.tsx` — renders the optional evidence action as a native button.
- `web/src/components/ChatPanel/EvidenceList.test.tsx` — verifies button and non-button behavior in jsdom.
- `web/src/components/ChatPanel/{MessageBubble,ProcessTimeline,ContextPanel,ProposalCard,index}.tsx` — forwards one evidence-open callback across every existing evidence surface.
- `web/src/components/ChatPanel/ChatPanel.module.css` — adds hover and focus-visible states without changing read-only evidence layout.
- `web/src/lib/pilotEvidenceFocus.ts` — contains pure one-shot target lookup/date helpers shared by record destinations.
- `web/src/lib/pilotEvidenceFocus.test.ts` — covers resolved, missing, and invalid focus inputs.
- `web/src/layout/AppShell.tsx` — owns evidence navigation, one-shot focus state, and all Pilot callback wiring.
- `web/src/layout/AppShell.test.ts` — protects the shell wiring contract.
- `web/src/components/{OfferCenterView,ResumeLibraryView,CalendarView}.tsx` — consume the focus state in their existing detail views.
- `web/src/components/CalendarView.module.css` — makes a focused calendar event visibly identifiable.

### Task 1: Derive Safe Evidence Targets

**Files:**
- Modify: `web/src/components/ChatPanel/model.ts:8-31,294-412`
- Modify: `web/src/components/ChatPanel/model.test.ts:495-943`

- [ ] **Step 1: Write failing target-normalization assertions**

Add the following expectations next to the existing application, event, resume, Offer, and resume-match normalization cases. Reuse the local `msg(...)` fixture already defined in the test file.

```ts
expect(turns[1].steps?.[0].evidence?.[0]).toMatchObject({
  id: 'application-7',
  target: { kind: 'application', id: 7 },
});

expect(turns[0].steps?.[0].evidence?.[0]).toMatchObject({
  id: 'list_application_events-1',
  target: { kind: 'event', id: 1, scheduledAt: '2026-07-01T07:00:00Z' },
});
```

Add one table-style test that builds four tool rows and asserts these target rules:

```ts
expect(resumeEvidence.target).toEqual({ kind: 'resume', id: 12 });
expect(resumeMatchEvidence.target).toEqual({ kind: 'resume', id: 12 });
expect(offerEvidence.target).toEqual({ kind: 'offer', id: 9 });
expect(invalidEvidence.target).toBeUndefined();
```

`invalidEvidence` must use `id: '9'`, `id: 0`, and an event without a parseable `scheduled_at` in separate rows. It proves display strings and malformed rows cannot become navigation targets.

- [ ] **Step 2: Run the focused test to confirm the red state**

Run:

```powershell
cd web
npm.cmd test -- --run src/components/ChatPanel/model.test.ts
```

Expected: the new assertions fail because `EvidenceItem` has no `target` field.

- [ ] **Step 3: Add the target type and strict derivation helpers**

In `model.ts`, define the target beside `EvidenceKind` and extend `EvidenceItem` with an optional target:

```ts
export type EvidenceTarget =
  | { kind: 'application'; id: number }
  | { kind: 'offer'; id: number }
  | { kind: 'resume'; id: number }
  | { kind: 'event'; id: number; scheduledAt: string };

export interface EvidenceItem {
  id: string;
  kind: EvidenceKind;
  title: string;
  meta?: string;
  snippet?: string;
  source: string;
  target?: EvidenceTarget;
  occurrences?: number;
}

function positiveInteger(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : undefined;
}

function eventTarget(value: unknown, scheduledAt: unknown): EvidenceTarget | undefined {
  const id = positiveInteger(value);
  const timestamp = text(scheduledAt);
  return id && timestamp && dayjs(timestamp).isValid()
    ? { kind: 'event', id, scheduledAt: timestamp }
    : undefined;
}
```

Attach targets only in `evidenceFromRecord`:

```ts
// resume-match: use the returned resume_id, never the match-row id
target: positiveInteger(record.resume_id)
  ? { kind: 'resume', id: positiveInteger(record.resume_id)! }
  : undefined,

// regular resume / Offer / application
target: positiveInteger(record.id)
  ? { kind: 'resume' | 'offer' | 'application', id: positiveInteger(record.id)! }
  : undefined,

// event: prefer application_event_id, then id, and require valid scheduled_at
target: eventTarget(record.application_event_id ?? record.id, record.scheduled_at),
```

Use three small typed helpers (`resumeTarget`, `offerTarget`, `applicationTarget`) rather than a runtime union expression in the object literal, so each `kind` stays a literal type. Do not create a target for knowledge, JD, note, unknown, plain text, or malformed JSON evidence. Do not derive any id from `EvidenceItem.id`, title, metadata, or model prose.

- [ ] **Step 4: Run the model test and typecheck**

Run:

```powershell
cd web
npm.cmd test -- --run src/components/ChatPanel/model.test.ts
npm.cmd exec tsc -- -b
```

Expected: all model tests pass and TypeScript reports no errors.

- [ ] **Step 5: Commit the normalization boundary**

```powershell
git add web/src/components/ChatPanel/model.ts web/src/components/ChatPanel/model.test.ts
git commit -m "feat: AI add record targets to Pilot evidence"
```

### Task 2: Render and Propagate Accessible Evidence Actions

**Files:**
- Create: `web/src/components/ChatPanel/EvidenceList.test.tsx`
- Modify: `web/src/components/ChatPanel/EvidenceList.tsx:12-86`
- Modify: `web/src/components/ChatPanel/ChatPanel.module.css:660-718`
- Modify: `web/src/components/ChatPanel/MessageBubble.tsx:36-66`
- Modify: `web/src/components/ChatPanel/ProcessTimeline.tsx:8-98`
- Modify: `web/src/components/ChatPanel/ContextPanel.tsx:10-74`
- Modify: `web/src/components/ChatPanel/ProposalCard.tsx:20-130,300-330`
- Modify: `web/src/components/ChatPanel/index.tsx:87-100,760-900`

- [ ] **Step 1: Write the EvidenceList render test first**

Create a jsdom test using the repository's existing `createRoot`/`act` helper pattern. Cover one actionable application and one display-only knowledge item:

```tsx
// @vitest-environment jsdom
it('opens a validated evidence target with native button semantics', () => {
  const onOpenEvidence = vi.fn();
  const view = render(
    <EvidenceList
      onOpenEvidence={onOpenEvidence}
      items={[{
        id: 'application-7', kind: 'application', title: '字节跳动', source: 'list_applications',
        target: { kind: 'application', id: 7 },
      }]}
    />,
  );
  const button = view.container.querySelector('button[aria-label="打开投递：字节跳动"]');
  expect(button).not.toBeNull();
  act(() => button?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  expect(onOpenEvidence).toHaveBeenCalledWith({ kind: 'application', id: 7 });
});

it('leaves evidence without a target as non-interactive content', () => {
  const view = render(<EvidenceList items={[{ id: 'knowledge-1', kind: 'knowledge', title: '面试技巧', source: 'search_knowledge' }]} />);
  expect(view.container.querySelector('[aria-label^="打开"]')).toBeNull();
});
```

- [ ] **Step 2: Run the component test to confirm the red state**

Run:

```powershell
cd web
npm.cmd test -- --run src/components/ChatPanel/EvidenceList.test.tsx
```

Expected: the new actionable-row assertion fails because `EvidenceList` does not accept `onOpenEvidence` and has no button.

- [ ] **Step 3: Implement a single visual action primitive**

Extend `EvidenceList` and render this button only when both the callback and `item.target` exist:

```tsx
interface Props {
  // existing fields
  onOpenEvidence?: (target: EvidenceTarget) => void;
}

function evidenceActionLabel(item: EvidenceItem): string {
  const label = { application: '投递', offer: 'Offer', resume: '简历', event: '日程' }[item.target!.kind];
  return `打开${label}：${item.title}`;
}

{item.target && onOpenEvidence ? (
  <button
    type="button"
    className={styles.evidenceAction}
    aria-label={evidenceActionLabel(item)}
    onClick={() => onOpenEvidence(item.target!)}
  >
    {rowContent}
  </button>
) : rowContent}
```

Move the current icon/title/meta/snippet markup into `rowContent` so the read-only path remains byte-for-byte equivalent in content. Keep the `<li>` as the list item; the button is the full-width row control. Replace row padding/background on `.evidenceItem` with `.evidenceAction` for clickable rows, and add only these interaction states:

```css
.evidenceAction {
  display: flex;
  width: 100%;
  gap: 9px;
  padding: 9px 10px;
  border: 0;
  border-radius: 9px;
  background: var(--chat-tint-soft);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.evidenceAction:hover { background: color-mix(in srgb, var(--chat-tint-soft) 78%, var(--op-primary) 22%); }
.evidenceAction:focus-visible { outline: 2px solid var(--op-primary); outline-offset: 2px; }
```

Give the existing non-actionable `.evidenceItem` the original padding/background so display-only rows do not change appearance.

- [ ] **Step 4: Thread the same callback through every evidence surface**

Use the exact callback name `onOpenEvidence` in `MessageBubble`, `ProcessTimeline`, `ContextPanel`, `ProposalCard`, and `ChatPanel`. Each component should only forward it:

```tsx
// MessageBubble
export default function MessageBubble({ turn, index, onOpenEvidence }: Props) {
  // ...
  {!isUser && turn.steps ? <ProcessTimeline steps={turn.steps} onOpenEvidence={onOpenEvidence} /> : null}
}

// ProcessTimeline and ContextPanel
<EvidenceList {...selectionProps} onOpenEvidence={onOpenEvidence} />

// ChatPanel stream, confirmation, and context panel
<MessageBubble key={index} turn={turn} index={index} onOpenEvidence={onOpenEvidence} />
<ProposalCard {...proposalProps} onOpenEvidence={onOpenEvidence} />
<ContextPanel {...contextProps} onOpenEvidence={onOpenEvidence} />
```

In `ProposalCard`, leave API-only fallback `actionEvidence` targetless. Do not infer a target from a pending-action `id`; it remains display-only. When the card receives normalized thread evidence through its existing `evidence` prop, the target produced in Task 1 flows through normally.

- [ ] **Step 5: Run evidence interaction, model, and compiler checks**

Run:

```powershell
cd web
npm.cmd test -- --run src/components/ChatPanel/EvidenceList.test.tsx src/components/ChatPanel/model.test.ts
npm.cmd exec tsc -- -b
```

Expected: both test files pass; no component has an unthreaded required prop.

- [ ] **Step 6: Commit the evidence interaction layer**

```powershell
git add web/src/components/ChatPanel
git commit -m "feat: AI make Pilot evidence citations interactive"
```

### Task 3: Navigate to Existing Record Views Without Mutating Data

**Files:**
- Create: `web/src/lib/pilotEvidenceFocus.ts`
- Create: `web/src/lib/pilotEvidenceFocus.test.ts`
- Modify: `web/src/layout/AppShell.tsx:80-390,426-483`
- Modify: `web/src/layout/AppShell.test.ts:1-112`
- Modify: `web/src/components/OfferCenterView.tsx:1-113`
- Modify: `web/src/components/ResumeLibraryView.tsx:1-205`
- Modify: `web/src/components/CalendarView.tsx:1-250`
- Modify: `web/src/components/CalendarView.module.css:198-208`

- [ ] **Step 1: Write failing pure focus tests**

Create `pilotEvidenceFocus.test.ts` before changing a view. The helper test keeps record lookup and event-day parsing deterministic without mounting Ant Design drawers.

```ts
import { describe, expect, it } from 'vitest';
import { findEvidenceFocusRecord, eventFocusDate } from './pilotEvidenceFocus';

describe('Pilot evidence focus helpers', () => {
  it('resolves only the exact positive record id', () => {
    expect(findEvidenceFocusRecord([{ id: 2, title: 'A' }], 2)).toEqual({ id: 2, title: 'A' });
    expect(findEvidenceFocusRecord([{ id: 2, title: 'A' }], 3)).toBeUndefined();
    expect(findEvidenceFocusRecord([{ id: 2, title: 'A' }], undefined)).toBeUndefined();
  });

  it('returns a local calendar day only for a valid event timestamp', () => {
    expect(eventFocusDate('2026-07-01T07:00:00Z')).toBe('2026-07-01');
    expect(eventFocusDate('not-a-date')).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run the focus test to confirm the red state**

Run:

```powershell
cd web
npm.cmd test -- --run src/lib/pilotEvidenceFocus.test.ts
```

Expected: module-not-found failure for `pilotEvidenceFocus`.

- [ ] **Step 3: Implement the pure focus helpers**

Create `pilotEvidenceFocus.ts` with no React state or side effects:

```ts
import dayjs from 'dayjs';

export function findEvidenceFocusRecord<T extends { id: number }>(
  records: readonly T[],
  focusId: number | undefined,
): T | undefined {
  return focusId === undefined ? undefined : records.find((record) => record.id === focusId);
}

export function eventFocusDate(scheduledAt: string): string | undefined {
  const date = dayjs(scheduledAt);
  return date.isValid() ? date.format('YYYY-MM-DD') : undefined;
}
```

- [ ] **Step 4: Add one-shot focus contracts to destination views**

Add optional focus props without changing existing manual interactions:

```tsx
// OfferCenterView and ResumeLibraryView props
focusOfferId?: number;
focusResumeId?: number;
onEvidenceFocusConsumed?: () => void;
```

After each view's query has finished, resolve the exact record with
`findEvidenceFocusRecord`. On success, set its existing `editing` state then
call `onEvidenceFocusConsumed`. On a missing record, call
`message.warning('引用的记录已不存在')`, do not set `editing`, then consume the
focus. This must not create a new record or select a different one.

For `CalendarView`, add:

```tsx
focusEvent?: Extract<EvidenceTarget, { kind: 'event' }>;
onEvidenceFocusConsumed?: () => void;
```

When `focusEvent` is present, use `eventFocusDate` to set `currentMonth`,
`selectedDate`, and a local `focusedEventId`; immediately consume the parent
focus. Once the selected month query is settled, verify an entry has
`entry.event_id === focusedEventId`. If not, show the same unavailable warning
and clear only `focusedEventId`. Do not call `getEvent`, `openEntry`, or the
edit mutation. Add `styles.entryItemFocused` to the matching selected-day row:

```css
.entryItemFocused {
  background: color-mix(in srgb, var(--op-primary) 10%, transparent);
  box-shadow: inset 3px 0 0 var(--op-primary);
}
```

- [ ] **Step 5: Wire shell navigation and all Pilot surfaces**

In `AppShell`, add one state value and two stable handlers:

```tsx
const [evidenceFocus, setEvidenceFocus] = useState<Exclude<EvidenceTarget, { kind: 'application' }> | null>(null);

const clearEvidenceFocus = (target: EvidenceTarget) => {
  setEvidenceFocus((current) => (current === target ? null : current));
};

const openEvidence = (target: EvidenceTarget) => {
  setAISettingsOpen(false);
  if (target.kind === 'application') {
    const application = apps.find((item) => item.id === target.id);
    if (application) openApplicationDetail(application);
    else message.warning('引用的记录已不存在');
    return;
  }
  setSelected(null);
  setEvidenceFocus(target);
  navigateToView(target.kind === 'offer' ? 'offers' : target.kind === 'resume' ? 'resumes' : 'calendar');
};
```

Pass the resulting focus values to the existing views:

```tsx
<OfferCenterView
  applications={apps}
  onCoach={(offer) => openChat(offer.id)}
  onAttachToPilot={attachToPilot}
  focusOfferId={evidenceFocus?.kind === 'offer' ? evidenceFocus.id : undefined}
  onEvidenceFocusConsumed={() => evidenceFocus && clearEvidenceFocus(evidenceFocus)}
/>
```

Mirror this exact one-shot contract for `ResumeLibraryView` and `CalendarView`.
Pass `onOpenEvidence={openEvidence}` to the page, rail, and drawer `ChatPanel`
instances. Do not close or expand Pilot in `openEvidence`; module navigation
must preserve the currently visible contextual Pilot surface.

Extend `AppShell.test.ts` with source-contract checks that all three
`ChatPanel` instances receive `onOpenEvidence={openEvidence}`, and that the
Offer, resume, and calendar views receive their corresponding focus props.

- [ ] **Step 6: Run focused navigation verification**

Run:

```powershell
cd web
npm.cmd test -- --run src/lib/pilotEvidenceFocus.test.ts src/layout/AppShell.test.ts src/components/ChatPanel/EvidenceList.test.tsx
npm.cmd exec tsc -- -b
```

Expected: exact ID lookup/date parsing, all callback wiring, and the full
frontend type graph pass.

- [ ] **Step 7: Commit destination navigation**

```powershell
git add web/src/lib/pilotEvidenceFocus.ts web/src/lib/pilotEvidenceFocus.test.ts web/src/layout/AppShell.tsx web/src/layout/AppShell.test.ts web/src/components/OfferCenterView.tsx web/src/components/ResumeLibraryView.tsx web/src/components/CalendarView.tsx web/src/components/CalendarView.module.css
git commit -m "feat: AI open source records from Pilot evidence"
```

### Task 4: Integration Verification and Review

**Files:**
- Verify only: all files changed in Tasks 1-3

- [ ] **Step 1: Run the complete automated gate**

Run from the worktree root:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
cd web
npm.cmd test -- --run
npm.cmd run build
cd ..
uv run oc smoke --static-dir web/dist
```

Expected: all commands exit zero. If Docker is unavailable for smoke, record
the command output and do not claim the Docker smoke result.

- [ ] **Step 2: Run a real browser walk-through**

Use the in-app browser against the local application and a seeded database.

1. Ask Pilot to list applications, Offers, resumes, and scheduled events.
2. Expand the process timeline and activate one evidence row of each supported kind.
3. Confirm application detail, Offer editor, resume editor, and the selected
   calendar day/event match the clicked evidence.
4. Confirm Pilot remains open and no record is modified.
5. Check a knowledge evidence row has no button and an unavailable/deleted
   record does not open a blank editor.
6. Repeat one action using Tab then Enter to verify native keyboard activation
   and visible focus treatment.

- [ ] **Step 3: Request and resolve code review**

Ask an independent reviewer to inspect the final diff for target validation,
one-shot focus handling, accidental mutation paths, accessibility semantics,
and regressions to existing evidence expansion. Resolve every actionable
finding with a focused test before final handoff.

- [ ] **Step 4: Commit review fixes if needed and verify a clean worktree**

Run:

```powershell
git status --short --branch
```

Expected: no uncommitted files. If review fixes were made, commit them with a
conventional message beginning `fix: AI` before this check.
