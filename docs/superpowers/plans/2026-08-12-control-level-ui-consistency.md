# Control-Level UI Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify OfferPilot's forms, actions, lists, histories, evidence blocks, empty states, and feedback states without changing existing layouts or business behavior.

**Architecture:** Extend the existing theme tokens with a small reusable workflow-surface class layer, then apply those classes and focused CSS Modules to the high-exposure interview/evaluation components and lower-frequency utility surfaces. Preserve every existing prop, callback, service call, request branch, and navigation target; tests assert both the new presentation hooks and the unchanged interaction boundaries.

**Tech Stack:** React 18, TypeScript, Ant Design, CSS Modules, Vitest, Testing Library, Vite.

---

## Fixed Baseline and Scope

- Baseline commit: `4b8ddfa`
- Allowed production paths:
  - `web/src/theme/tokens.css`
  - `web/src/components/ui/WorkflowSurface.module.css`
  - `web/src/components/InterviewV01View.tsx`
  - `web/src/components/MockInterviewDrawer.tsx`
  - `web/src/components/InterviewPreparationProposalDrawer.tsx`
  - `web/src/components/InterviewKnowledgeCaptureDrawer.tsx`
  - `web/src/components/OpportunityFitReviewDrawer.tsx`
  - `web/src/components/ResumeMatchModal.tsx`
  - `web/src/components/ScheduleEventForm.tsx`
  - `web/src/components/AISettingsDrawer.tsx`
  - `web/src/features/pipeline/ActionDetailDrawer.tsx`
  - `web/src/layout/CommandPalette.tsx`
- Allowed test paths: corresponding existing component tests plus `web/src/theme/controlPolish.test.ts`.
- Allowed documentation paths: this plan and `docs/reports/2026-08-12-control-level-ui-consistency-browser-acceptance.md`.
- Forbidden: `src/offerpilot/**`, API/service/type files, migrations, AI contracts, navigation definitions, business copy changes unrelated to overflow or state clarity.

### Task 1: Establish the reusable workflow-surface contract

**Files:**
- Modify: `web/src/theme/controlPolish.test.ts`
- Modify: `web/src/theme/tokens.css`
- Create: `web/src/components/ui/WorkflowSurface.module.css`

- [ ] **Step 1: Write the failing style-contract test**

Add assertions that the theme exposes reusable selectors and safety properties:

```ts
expect(css).toContain('.op-control-toolbar');
expect(css).toContain('.op-inline-status');
expect(css).toContain('.op-empty-state');
expect(css).toContain('overflow-wrap: anywhere');
expect(css).toContain('@media (max-width: 720px)');
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm.cmd test -- --run src/theme/controlPolish.test.ts`

Expected: failure because the new reusable selectors do not exist.

- [ ] **Step 3: Implement the global and scoped style layer**

Add global classes for toolbar/action wrapping, inline status, empty state, long-text safety, section headings, and responsive stacking. Add a CSS Module that exports:

```css
.surface {}
.section {}
.sectionHeader {}
.listRow {}
.listContent {}
.metaRow {}
.actionGroup {}
.formGrid {}
.evidenceBlock {}
.scrollRegion {}
```

The module must use existing CSS variables and must not define a second color system.

- [ ] **Step 4: Run the test and verify GREEN**

Run: `npm.cmd test -- --run src/theme/controlPolish.test.ts`

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/theme/controlPolish.test.ts web/src/theme/tokens.css web/src/components/ui/WorkflowSurface.module.css
git commit -m "style: AI add shared workflow control surfaces"
```

### Task 2: Polish the interview and preparation surfaces

**Files:**
- Modify: `web/src/components/InterviewV01View.test.tsx`
- Modify: `web/src/components/InterviewV01View.tsx`
- Modify: `web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx`
- Modify: `web/src/components/MockInterviewDrawer.tsx`
- Modify: `web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx`
- Modify: `web/src/components/InterviewPreparationProposalDrawer.tsx`
- Modify: `web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx`
- Modify: `web/src/components/InterviewKnowledgeCaptureDrawer.tsx`

- [ ] **Step 1: Write failing mounted presentation tests**

Assert real rendered surfaces expose stable semantic hooks while retaining behavior:

```ts
expect(screen.getByTestId('interview-surface')).toBeInTheDocument();
expect(screen.getByTestId('mock-interview-action-group')).toBeInTheDocument();
expect(screen.getByTestId('interview-preparation-source-panel')).toBeInTheDocument();
expect(screen.getByTestId('knowledge-capture-source-panel')).toBeInTheDocument();
```

Keep the existing assertions for callbacks, disabled controls, result-unknown recovery, zero unintended writes, and history behavior.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
npm.cmd test -- --run `
  src/components/InterviewV01View.test.tsx `
  src/components/MockInterviewDrawer.cleanup.interaction.test.tsx `
  src/components/InterviewPreparationProposalDrawer.interaction.test.tsx `
  src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx
```

Expected: failures because the new semantic surfaces are absent.

- [ ] **Step 3: Apply the shared presentation layer**

- Add section headers and stable action grouping without moving feature entry points.
- Convert dense tag runs into metadata rows while preserving every text value.
- Wrap long JD, answer, evidence, error, and history text with the long-text class.
- Use inline status panels for generating, unknown result, source change, confirmed, and safe-empty states.
- Improve empty states with existing safe next actions only.
- Keep Drawer widths, component order, service calls, and callbacks unchanged.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the same command as Step 2.

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components/InterviewV01View.tsx web/src/components/InterviewV01View.test.tsx web/src/components/MockInterviewDrawer.tsx web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx web/src/components/InterviewPreparationProposalDrawer.tsx web/src/components/InterviewPreparationProposalDrawer.interaction.test.tsx web/src/components/InterviewKnowledgeCaptureDrawer.tsx web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx
git commit -m "style: AI unify interview workflow controls"
```

### Task 3: Polish evaluation and resume-match surfaces

**Files:**
- Modify: `web/src/components/OpportunityFitReviewDrawer.test.tsx`
- Modify: `web/src/components/OpportunityFitReviewDrawer.tsx`
- Create: `web/src/components/ResumeMatchModal.test.tsx`
- Modify: `web/src/components/ResumeMatchModal.tsx`

- [ ] **Step 1: Write failing presentation and behavior tests**

Add mounted assertions for the shared source/history/result surfaces and preserve request boundaries:

```ts
expect(screen.getByTestId('opportunity-fit-source-panel')).toBeInTheDocument();
expect(screen.getByTestId('opportunity-fit-action-group')).toBeInTheDocument();
expect(screen.getByTestId('resume-match-result-surface')).toBeInTheDocument();
expect(onClose).not.toHaveBeenCalled();
```

The Resume Match test must cover loading, empty, success, and evidence text with no extra service calls caused by presentation.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npm.cmd test -- --run src/components/OpportunityFitReviewDrawer.test.tsx src/components/ResumeMatchModal.test.tsx
```

Expected: failures because the semantic surfaces do not exist.

- [ ] **Step 3: Implement minimal presentation changes**

- Group source snapshot, analysis status, proposal content, history, and action controls without changing their order.
- Replace repeated inline spacing with shared classes.
- Ensure evidence paths, long reasons, URLs, and error messages wrap.
- Keep Triage/Deep lifecycle, idempotency, result-unknown, history, and source-conflict behavior untouched.
- Preserve Resume Match service parameters and selection behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command as Step 2.

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components/OpportunityFitReviewDrawer.tsx web/src/components/OpportunityFitReviewDrawer.test.tsx web/src/components/ResumeMatchModal.tsx web/src/components/ResumeMatchModal.test.tsx
git commit -m "style: AI refine evaluation result surfaces"
```

### Task 4: Polish utility surfaces and command interactions

**Files:**
- Create: `web/src/components/ScheduleEventForm.test.tsx`
- Modify: `web/src/components/ScheduleEventForm.tsx`
- Modify: `web/src/components/AISettingsDrawer.test.ts`
- Modify: `web/src/components/AISettingsDrawer.tsx`
- Create: `web/src/features/pipeline/ActionDetailDrawer.test.tsx`
- Modify: `web/src/features/pipeline/ActionDetailDrawer.tsx`
- Modify: `web/src/layout/CommandPalette.test.ts`
- Modify: `web/src/layout/CommandPalette.tsx`

- [ ] **Step 1: Write failing mounted tests**

Assert the form grouping, provider rows, action detail surface, and command result selection use stable semantic hooks:

```ts
expect(screen.getByTestId('schedule-event-form')).toBeInTheDocument();
expect(screen.getByTestId('ai-provider-list')).toBeInTheDocument();
expect(screen.getByTestId('action-detail-surface')).toBeInTheDocument();
expect(screen.getByTestId('command-palette-results')).toBeInTheDocument();
```

Retain existing submit, close, keyboard, provider configuration, and command execution assertions.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npm.cmd test -- --run `
  src/components/ScheduleEventForm.test.tsx `
  src/components/AISettingsDrawer.test.ts `
  src/features/pipeline/ActionDetailDrawer.test.tsx `
  src/layout/CommandPalette.test.ts
```

Expected: failures because presentation hooks are absent.

- [ ] **Step 3: Apply scoped presentation changes**

- Use responsive form grids and stable help/error placement in the event form.
- Make provider rows visually separable and keep secrets masked and behavior unchanged.
- Present action metadata as readable rows rather than raw list density.
- Improve command selection, group labels, shortcut hints, and long-label wrapping without changing keyboard behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same command as Step 2.

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components/ScheduleEventForm.tsx web/src/components/ScheduleEventForm.test.tsx web/src/components/AISettingsDrawer.tsx web/src/components/AISettingsDrawer.test.ts web/src/features/pipeline/ActionDetailDrawer.tsx web/src/features/pipeline/ActionDetailDrawer.test.tsx web/src/layout/CommandPalette.tsx web/src/layout/CommandPalette.test.ts
git commit -m "style: AI polish utility workflow controls"
```

### Task 5: Full verification, visual acceptance, and report

**Files:**
- Create: `docs/reports/2026-08-12-control-level-ui-consistency-browser-acceptance.md`

- [ ] **Step 1: Verify the changed-file boundary**

Run:

```powershell
git diff --name-only 4b8ddfa..HEAD
git status --short
git diff --check 4b8ddfa..HEAD
```

Expected: only the fixed scope, tests, spec, plan, and report are present; no unstaged files.

- [ ] **Step 2: Run frontend gates**

Run:

```powershell
Set-Location web
npm.cmd test -- --run
npm.cmd exec tsc -- -b
npm.cmd run build
```

Expected: every command exits `0`.

- [ ] **Step 3: Request independent code review**

Review `4b8ddfa..HEAD` for P0/P1/P2 issues, especially business-behavior changes, inaccessible controls, overflow, hidden actions, unintended requests, and dark-mode regressions. Fix all confirmed findings with test-first changes.

- [ ] **Step 4: Run browser acceptance**

Use an isolated real deployment with existing configuration, Chinese synthetic data, light mode, and a viewport of at least `1440×900`. Inspect interview index, mock interview, evaluation/preparation, and one utility surface. Record console errors, horizontal overflow, network boundaries, and cleanup.

- [ ] **Step 5: Capture and inspect screenshots**

Capture wide screenshots showing the polished interview, evaluation, and utility surfaces. Re-open every image to verify legibility, no clipping, no empty lower half caused by capture bounds, and no secrets.

- [ ] **Step 6: Write and commit the acceptance report**

Record exact commands, test counts, screenshot paths, viewport dimensions, remaining risks, and cleanup results. Then run `git diff --check` and commit:

```powershell
git add -f docs/reports/2026-08-12-control-level-ui-consistency-browser-acceptance.md
git commit -m "test: AI verify control level UI consistency"
```

- [ ] **Step 7: Final cleanliness check**

Run:

```powershell
git status --short --branch
git diff --check 4b8ddfa..HEAD
```

Expected: clean worktree and exit code `0`.
