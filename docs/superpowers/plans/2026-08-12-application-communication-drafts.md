# Application Communication Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, editable and copy-only follow-up and interview thank-you drafts inside the existing application outcome workspace.

**Architecture:** A pure TypeScript generator consumes only already-loaded immutable application facts. A focused React component owns session-only editing and clipboard interaction; `ApplicationOutcomeDrawer` passes its existing snapshot, outcome and event data without adding services or requests.

**Tech Stack:** React, TypeScript, Ant Design, CSS Modules, Vitest/jsdom.

---

### Task 1: Deterministic draft generator

**Files:**
- Create: `web/src/lib/applicationCommunicationDraft.ts`
- Create: `web/src/lib/applicationCommunicationDraft.test.ts`

- [ ] Write failing tests for follow-up/thank-you templates, explicit outcome selection, source ownership, invalid and timezone-crossing dates, recipient fallback, Unicode limits and determinism.
- [ ] Run `npm.cmd test -- --run src/lib/applicationCommunicationDraft.test.ts` and confirm the missing-module failure.
- [ ] Implement strict input validation and pure-text templates with no clock, random, HTML or mutation.
- [ ] Rerun the test and confirm all cases pass.

### Task 2: Draft workspace component

**Files:**
- Create: `web/src/components/ApplicationCommunicationDraftPanel.tsx`
- Create: `web/src/components/ApplicationCommunicationDraftPanel.test.tsx`
- Create: `web/src/components/ApplicationCommunicationDraftPanel.module.css`

- [ ] Write failing mounted tests for explicit outcome selection, deterministic event pairing, source invalidation, accessible type state, edit/restore, clipboard success/failure, disabled thank-you state and zero service/network calls.
- [ ] Run the component test and confirm the missing-component failure.
- [ ] Implement the session-only two-column workspace with accessible controls, wrapping source labels and `aria-live` status.
- [ ] Rerun tests and confirm all cases pass.

### Task 3: Integrate with application outcome drawer

**Files:**
- Modify: `web/src/components/ApplicationOutcomeDrawer.tsx`
- Modify: `web/src/components/ApplicationOutcomeDrawer.test.tsx`

- [ ] Write a failing real-mount test proving the panel uses existing query data and adds no service call.
- [ ] Render the draft panel after at least one frozen snapshot exists, passing existing snapshots, outcomes and events.
- [ ] Rerun drawer and component tests.

### Task 4: Verification and browser acceptance

**Files:**
- Create: `docs/reports/2026-08-12-application-communication-drafts-browser-acceptance.md`
- Create: `artifacts/2026-08-13-application-communication-drafts/01-application-follow-up-draft.png`
- Create: `artifacts/2026-08-13-application-communication-drafts/02-interview-thank-you-draft.png`

- [ ] Run targeted tests, frontend full tests, `npx.cmd tsc -b`, `npm.cmd run build` and `git diff --check`.
- [ ] Request independent code review and resolve every P0/P1/P2.
- [ ] Start an isolated local service, create a Chinese “筱哲” case, and inspect both draft modes in bright wide viewport.
- [ ] Capture and re-open both screenshots, verify console errors are zero, then clean ports, processes and temporary data.
- [ ] Commit all files with `feat: AI add application communication drafts`.

## File boundary

Allowed product files are the five frontend files listed above plus the two existing outcome drawer files and documentation/artifacts. Forbidden: `src/offerpilot/**`, `tests/**`, `web/src/services/**`, `web/src/types/**`, database migrations, Provider settings, Pilot tools and external delivery integrations.
