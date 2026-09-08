# Direct Card-to-Pilot Drop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let supported cards themselves act as drag sources, with the final drop target deciding whether a Kanban card changes status or becomes a Pilot context reference.

**Architecture:** Move native attachment serialization from the visible `PilotAttachmentHandle` into a reusable card-root drag binding. Replace all handle rendering with that binding and preserve a screen-reader-only accessible attach action. Extend the existing Kanban dnd-kit resolver with a Pilot droppable target, which dispatches `onAttachToPilot` instead of the lifecycle status mutation.

**Tech Stack:** React 18, TypeScript, Ant Design, native HTML drag events, dnd-kit, Vitest.

---

## File Structure

- `web/src/components/PilotAttachmentHandle.tsx`: replace visible button with a reusable direct-card drag binding and hidden accessible action.
- `web/src/components/PilotAttachmentHandle.test.tsx`: verifies card-root payload and no visible action text.
- `web/src/components/ApplicationDetail.tsx`, `ApplicationListView.tsx`, `OfferCard.tsx`, `ResumeCard.tsx`: bind card roots as native sources.
- `web/src/components/KanbanBoard/index.tsx`, `KanbanCard.tsx`, `KanbanColumn.tsx`: resolve status-column versus Pilot dnd-kit targets.
- `web/src/components/ChatPanel/ContextAttachmentRail.tsx`: expose a dnd-kit Pilot droppable surface when a Kanban drag is active.

### Task 1: Replace the visible attachment control with a card-root source binding

**Files:**
- Modify: `web/src/components/PilotAttachmentHandle.tsx`
- Modify: `web/src/components/PilotAttachmentHandle.test.tsx`

- [ ] **Step 1: Write failing tests**

```tsx
it('returns native drag props for a supported card without visible add-to-Pilot text', () => {
  const binding = createPilotAttachmentDragBinding(attachment);
  expect(binding.draggable).toBe(true);
  expect(binding['aria-label']).toBe('将 ByteDance Backend 拖到 Pilot');
  expect(renderedCard.textContent).not.toContain('添加到 Pilot');
});
```

- [ ] **Step 2: Verify RED**

Run: `cd web && npm.cmd test -- --run src/components/PilotAttachmentHandle.test.tsx`

Expected: FAIL because the current component renders a visible button.

- [ ] **Step 3: Implement the minimal binding**

```tsx
export function createPilotAttachmentDragBinding(attachment: PilotContextAttachment) {
  return {
    draggable: true,
    'aria-label': `将 ${attachment.label} 拖到 Pilot`,
    onDragStart: (event: DragEvent<HTMLElement>) => {
      event.dataTransfer.setData(NATIVE_PILOT_ATTACHMENT_TYPE, JSON.stringify(attachment));
      event.dataTransfer.effectAllowed = 'copy';
    },
  };
}
```

Remove the visible `PilotAttachmentHandle` button export and provide a visually-hidden accessible attach command only through each card's existing action/menu surface.

- [ ] **Step 4: Verify GREEN**

Run: `cd web && npm.cmd test -- --run src/components/PilotAttachmentHandle.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/PilotAttachmentHandle.tsx web/src/components/PilotAttachmentHandle.test.tsx
git commit -m "refactor: AI bind Pilot attachment drag to card roots"
```

### Task 2: Bind direct native drag to application, offer, and resume cards

**Files:**
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/components/ApplicationListView.tsx`
- Modify: `web/src/components/OfferCard.tsx`
- Modify: `web/src/components/ResumeCard.tsx`
- Modify: `web/src/components/applicationPilotEntry.test.ts`

- [ ] **Step 1: Write failing source-entry assertions**

```tsx
it('makes each supported card root a draggable Pilot reference without a visible attachment control', () => {
  expect(applicationDetail).toContain('createPilotAttachmentDragBinding(applicationAttachment)');
  expect(offerCard).toContain('createPilotAttachmentDragBinding(offerAttachment)');
  expect(resumeCard).toContain('createPilotAttachmentDragBinding(resumeAttachment)');
  expect(applicationDetail).not.toContain('<PilotAttachmentHandle');
});
```

- [ ] **Step 2: Verify RED**

Run: `cd web && npm.cmd test -- --run src/components/applicationPilotEntry.test.ts`

Expected: FAIL because cards still render the visible handle.

- [ ] **Step 3: Implement direct card bindings**

```tsx
const applicationAttachment = { kind: 'application' as const, id: String(application.id), label: application.company_name };
<article {...(onAttachToPilot ? createPilotAttachmentDragBinding(applicationAttachment) : {})}>
```

Use equivalent offer/resume references. Preserve existing click-to-open interactions and attach only when `onAttachToPilot` is supplied.

- [ ] **Step 4: Verify GREEN**

Run: `cd web && npm.cmd test -- --run src/components/applicationPilotEntry.test.ts src/components/PilotAttachmentHandle.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ApplicationDetail.tsx web/src/components/ApplicationListView.tsx web/src/components/OfferCard.tsx web/src/components/ResumeCard.tsx web/src/components/applicationPilotEntry.test.ts
git commit -m "feat: AI drag supported cards into Pilot"
```

### Task 3: Route Kanban drops by destination

**Files:**
- Modify: `web/src/components/KanbanBoard/index.tsx`
- Modify: `web/src/components/KanbanBoard/KanbanCard.tsx`
- Modify: `web/src/components/KanbanBoard/KanbanColumn.tsx`
- Modify: `web/src/components/ChatPanel/ContextAttachmentRail.tsx`
- Test: `web/src/components/KanbanBoard/applicationLifecycle.test.ts`

- [ ] **Step 1: Write failing destination tests**

```tsx
it('changes lifecycle only when a Kanban drag ends on a status column', () => {
  expect(resolveKanbanDrop({ overId: 'status:interview' })).toEqual({ type: 'status', status: 'interview' });
});

it('attaches only when a Kanban drag ends on Pilot', () => {
  expect(resolveKanbanDrop({ overId: 'pilot-context-drop' })).toEqual({ type: 'pilot' });
});
```

- [ ] **Step 2: Verify RED**

Run: `cd web && npm.cmd test -- --run src/components/KanbanBoard/applicationLifecycle.test.ts`

Expected: FAIL because Pilot is not a dnd-kit drop destination.

- [ ] **Step 3: Implement one destination resolver**

```tsx
if (destination.type === 'pilot') {
  onAttachToPilot?.(toApplicationAttachment(activeApplication));
  return;
}
if (destination.type === 'status') {
  updateApplicationStatus(activeApplication.id, destination.status);
}
```

Register `pilot-context-drop` only while the Pilot rail is visible; do not call the lifecycle mutation for that id. Remove the former dedicated handle from `KanbanCard`.

- [ ] **Step 4: Verify GREEN**

Run: `cd web && npm.cmd test -- --run src/components/KanbanBoard/applicationLifecycle.test.ts src/components/ChatPanel/ContextAttachmentRail.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/KanbanBoard web/src/components/ChatPanel/ContextAttachmentRail.tsx
git commit -m "feat: AI route Kanban drops to status or Pilot"
```

### Task 4: Verify no-control presentation and regressions

**Files:**
- Modify: `web/src/components/ChatPanel/ContextAttachmentRail.test.tsx`
- Modify: `web/src/layout/AppShell.test.ts`

- [ ] **Step 1: Add failing integration assertions**

```tsx
it('shows Pilot drop emphasis only during a compatible card drag', () => {
  expect(validPilotDrag.classList.contains('isDragTarget')).toBe(true);
  expect(invalidDrag.defaultPrevented).toBe(false);
});
```

- [ ] **Step 2: Verify RED**

Run: `cd web && npm.cmd test -- --run src/components/ChatPanel/ContextAttachmentRail.test.tsx src/layout/AppShell.test.ts`

Expected: FAIL until the dnd-kit and native-drag visual state share the same compatible-target styling.

- [ ] **Step 3: Implement the minimal visual state**

```tsx
className={clsx(styles.attachmentRail, isNativeDragTarget && styles.isDragTarget)}
```

Keep the drop affordance drag-only; no static text/button is added to business cards.

- [ ] **Step 4: Run the release matrix**

Run:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
cd web && npm.cmd test -- --run
cd web && npm.cmd run build
uv run oc smoke --static-dir web/dist
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit and code review**

```bash
git add web/src/components/ChatPanel/ContextAttachmentRail.test.tsx web/src/layout/AppShell.test.ts
git commit -m "test: AI verify direct card Pilot drops"
```

Request independent review focused on source drag accessibility, native/dnd-kit coexistence, and status-versus-Pilot routing.
