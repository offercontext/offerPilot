# Story History Control Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix JD history text overflow and polish the existing Interview Story list, immutable version content, evidence disclosure, and controls without changing layout or behavior.

**Architecture:** Keep all queries, services, callbacks, and domain types unchanged. Move presentation-only rules into the existing component CSS modules, replace the nowrap JD history button content with a wrapping semantic selector, and derive Story display groups from the already loaded immutable version in a pure local render helper.

**Tech Stack:** React, TypeScript, Ant Design, CSS Modules, Vitest/JSDOM, Vite.

---

### Task 1: Make JD history text safely wrap

**Files:**
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/components/ApplicationDetail.module.css`
- Test: `web/src/components/ApplicationDetail.deterministicPilot.test.tsx`

- [ ] **Step 1: Write the failing mounted test**

Extend the application JD query mock so history returns a long Chinese/English preview, open “查看历史”, and assert the dialog contains `.jdHistoryOption`, `.jdHistoryPreview`, and `.jdHistoryDetail` rather than a nowrap Ant Button label.

```tsx
expect(dialog?.querySelector(`.${styles.jdHistoryOption}`)).not.toBeNull();
expect(dialog?.querySelector(`.${styles.jdHistoryPreview}`)?.textContent).toContain('FastAPI');
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
npm.cmd test -- --run src/components/ApplicationDetail.deterministicPilot.test.tsx
```

Expected: FAIL because the wrapping history classes do not exist.

- [ ] **Step 3: Implement the minimal selector and CSS**

Render each version as a native button with separate metadata and preview spans. Apply `min-width: 0`, `white-space: normal/pre-wrap`, `overflow-wrap: anywhere`, `word-break: break-word`, and `max-width: 100%`. Set Modal width to 680 and keep its responsive maximum.

- [ ] **Step 4: Run the test and verify GREEN**

Run the same test; expected: all ApplicationDetail deterministic tests pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components/ApplicationDetail.tsx web/src/components/ApplicationDetail.module.css web/src/components/ApplicationDetail.deterministicPilot.test.tsx
git commit -m "fix: AI wrap application JD history text"
```

### Task 2: Polish Interview Story presentation

**Files:**
- Modify: `web/src/components/InterviewStoryLibraryView.tsx`
- Create: `web/src/components/InterviewStoryLibraryView.module.css`
- Modify: `web/src/components/InterviewStoryLibraryView.test.tsx`

- [ ] **Step 1: Write failing Story hierarchy tests**

Use a version containing applicable questions, all STAR block kinds, repeated evidence links, and source states. Assert:

```tsx
expect(view.querySelector('[data-testid="story-version-content"]')).not.toBeNull();
expect(view.textContent).toContain('情境');
expect(view.textContent).toContain('3 条证据引用');
expect(view.querySelectorAll('details[data-evidence-target]').length).toBeGreaterThan(0);
```

Also click version, archive, restore, and close actions and retain existing service-call assertions.

- [ ] **Step 2: Run the test and verify RED**

```powershell
npm.cmd test -- --run src/components/InterviewStoryLibraryView.test.tsx
```

Expected: FAIL because the grouped version/evidence structure does not exist.

- [ ] **Step 3: Implement local presentation helpers and CSS**

Add stable labels for block kinds and target IDs. Group `evidence_links` by exact `target_kind + target_id`, render summary counts, and place matching frozen references inside closed native details. Do not mutate version data or call a service. Add CSS for story rows, status metadata, 40px actions, section surfaces, natural wrapping, focus-visible, active scale `0.96`, and narrow stacking.

- [ ] **Step 4: Run the Story tests and verify GREEN**

Run the same test; expected: all Story Library tests pass with no new request/write calls.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components/InterviewStoryLibraryView.tsx web/src/components/InterviewStoryLibraryView.module.css web/src/components/InterviewStoryLibraryView.test.tsx
git commit -m "style: AI polish interview story controls"
```

### Task 3: Verify and capture browser evidence

**Files:**
- Create: `docs/reports/2026-08-12-story-history-control-polish-browser-acceptance.md`
- Create: `artifacts/2026-08-12-story-history-control-polish/*.png`

- [ ] **Step 1: Run focused and full static verification**

```powershell
cd web
npm.cmd test -- --run src/components/ApplicationDetail.deterministicPilot.test.tsx src/components/InterviewStoryLibraryView.test.tsx
npm.cmd run build
cd ..
git diff --check
```

Expected: exit code 0 for every command.

- [ ] **Step 2: Run a light-mode Chinese browser walkthrough**

Deploy isolated local data containing candidate 筱哲, two long JD versions, and a confirmed Story with STAR blocks and multiple frozen sources. Use a `1455×1200` viewport. Verify the JD modal has no horizontal overflow and the Story page shows the refined list, version sections, collapsed/expanded evidence, and unchanged actions.

- [ ] **Step 3: Save and inspect screenshots**

Capture at minimum:

```text
01-jd-history-wrapped.png
02-story-library-polished.png
03-story-evidence-expanded.png
```

Read every image back, confirm dimensions, light mode, Chinese content, no clipping, and no console errors.

- [ ] **Step 4: Record acceptance and clean up**

Document exact viewport, requests, console errors, and cleanup. Stop local service/browser processes and delete temporary data.

- [ ] **Step 5: Commit evidence**

```powershell
git add -f docs/reports/2026-08-12-story-history-control-polish-browser-acceptance.md artifacts/2026-08-12-story-history-control-polish
git commit -m "test: AI verify story history control polish"
```

### Task 4: Final branch verification

**Files:** No product changes expected.

- [ ] **Step 1: Run TypeScript/build and relevant tests again from current HEAD**
- [ ] **Step 2: Run `git diff --check main..HEAD` and confirm the worktree is clean**
- [ ] **Step 3: Review changed files against the design boundary**
- [ ] **Step 4: Use the finishing-a-development-branch workflow and report merge options without pushing**
