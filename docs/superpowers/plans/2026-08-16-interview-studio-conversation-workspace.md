# Interview Studio Conversation Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vertically stacked Interview Studio with the approved desktop conversation/answer split and a safe mobile answer drawer without changing any interview, voice, evidence, or persistence contract.

**Architecture:** `InterviewStudio` remains the sole owner of Attempt, Turn, voice-controller, evidence, recovery, and submission state. The change introduces only local presentation state for the `回答 / 依据` workspace and moves existing JSX into two bounded visual regions. CSS owns wide-screen sizing and mobile drawer presentation; no new service calls or backend changes are allowed.

**Tech Stack:** React 18, TypeScript, Ant Design, CSS Modules, Vitest/JSDOM.

---

### Task 1: Lock the approved layout contract with failing tests

**Files:**
- Modify: `web/src/features/interviewStudio/InterviewStudio.layout.test.ts`
- Modify: `web/src/features/interviewStudio/InterviewStudio.test.tsx`

- [ ] **Step 1: Add a failing CSS layout contract**

Assert that the Studio has only two grid rows, the main region uses a bounded `1.75fr / minmax(360px, 1fr)` split, the conversation and answer content scroll independently, the action footer is sticky, and the narrow breakpoint turns the answer workspace into a fixed bottom drawer.

- [ ] **Step 2: Run the layout test and confirm RED**

Run:

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.layout.test.ts
```

Expected: FAIL because `.answerWorkspace`, `.answerWorkspaceBody`, `.workspaceActions`, and the approved main-grid contract do not exist.

- [ ] **Step 3: Add failing mounted interaction tests**

Add tests that require:

```text
回答 / 依据 tabs
default answer panel
evidence chip switching to evidence
returning to answer without new service calls
confirmed candidate answer rendered with actor label “你”
mobile workspace open/close controls
```

- [ ] **Step 4: Run the mounted tests and confirm RED**

Run:

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx
```

Expected: FAIL because the workspace tabs and mobile drawer controls are not implemented.

- [ ] **Step 5: Commit the failing contract**

```powershell
git add web/src/features/interviewStudio/InterviewStudio.layout.test.ts web/src/features/interviewStudio/InterviewStudio.test.tsx
git commit -m "test: AI define interview workspace layout"
```

### Task 2: Build the desktop conversation and answer workspace

**Files:**
- Modify: `web/src/features/interviewStudio/InterviewStudio.tsx`
- Modify: `web/src/features/interviewStudio/InterviewStudio.module.css`
- Test: `web/src/features/interviewStudio/InterviewStudio.test.tsx`
- Test: `web/src/features/interviewStudio/InterviewStudio.layout.test.ts`

- [ ] **Step 1: Add local workspace presentation state**

Introduce:

```ts
type WorkspaceTab = 'answer' | 'evidence';
const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('answer');
const [mobileWorkspaceOpen, setMobileWorkspaceOpen] = useState(false);
```

Evidence chips select `evidence`, retain the existing `selectedEvidenceKey`, and request the mobile workspace when needed. A new Turn returns to `answer` without touching the answer draft or controller.

- [ ] **Step 2: Recompose the JSX without moving business ownership**

Keep the topbar and all existing handlers. Inside `main`, render:

```text
section.conversationPane
aside.answerWorkspace
  tablist 回答 / 依据
  answerWorkspaceBody
    existing ContinuousVoiceModePanel
    existing VoiceAnswerComposer
    existing voice-review/recovery messages
  evidenceWorkspaceBody
    existing frozen source and excerpt UI
  workspaceActions
```

Do not duplicate `VoiceAnswerComposer`, evidence arrays, service calls, submit handlers, or recovery state.

- [ ] **Step 3: Render confirmed answers as candidate chat messages**

Keep each question and its evidence in the interviewer message, then render a separate right-aligned candidate message only when `turn.answer` is confirmed. Label it `你`; never move an unconfirmed transcript into timeline history.

- [ ] **Step 4: Implement the approved desktop styling**

Use the existing theme tokens. Required structure:

```css
.studio { grid-template-rows: auto minmax(0, 1fr); }
.main { grid-template-columns: minmax(0, 1.75fr) minmax(360px, 1fr); }
.conversationPane, .answerWorkspace { min-width: 0; min-height: 0; }
.conversationScroll, .answerWorkspaceBody, .evidenceWorkspaceBody { overflow: auto; }
.workspaceActions { position: sticky; bottom: 0; }
```

Use normal dialogue typography, right-aligned answer bubbles, 20–24px gutter, restrained surfaces, 40px minimum hit targets, and tabular timing metrics.

- [ ] **Step 5: Run targeted tests and reach GREEN**

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.layout.test.ts src/features/interviewStudio/InterviewStudio.test.tsx
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the desktop workspace**

```powershell
git add web/src/features/interviewStudio/InterviewStudio.tsx web/src/features/interviewStudio/InterviewStudio.module.css web/src/features/interviewStudio/InterviewStudio.layout.test.ts web/src/features/interviewStudio/InterviewStudio.test.tsx
git commit -m "feat: AI redesign interview studio workspace"
```

### Task 3: Complete the mobile drawer and accessibility behavior

**Files:**
- Modify: `web/src/features/interviewStudio/InterviewStudio.tsx`
- Modify: `web/src/features/interviewStudio/InterviewStudio.module.css`
- Modify: `web/src/features/interviewStudio/InterviewStudio.test.tsx`

- [ ] **Step 1: Add failing mobile focus tests**

Require the mobile answer trigger to expose `aria-expanded` and `aria-controls`, focus the selected workspace tab when opened, let Escape close the mobile workspace before closing Studio, and restore focus to the trigger.

- [ ] **Step 2: Run the test and confirm RED**

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx
```

Expected: FAIL on missing focus and Escape behavior.

- [ ] **Step 3: Implement the mobile drawer boundary**

Maintain refs for the trigger, selected tab, and `mobileWorkspaceOpen`. At the narrow breakpoint, the answer workspace is hidden until opened, then uses a fixed bottom surface with a bounded height, one internal scroll, a visible close action, and the same sticky submit footer. Desktop behavior remains unchanged.

- [ ] **Step 4: Protect the global Studio focus trap**

In the existing keydown handler, handle Escape in this order:

```text
mobile answer workspace open → close workspace and restore trigger focus
otherwise → existing Studio close request
```

Tab remains trapped inside the Studio dialog; no nested modal or second business lifecycle is created.

- [ ] **Step 5: Run targeted tests and build**

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.layout.test.ts src/features/interviewStudio/InterviewStudio.test.tsx src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx
npm.cmd run build
```

Expected: tests and production build PASS.

- [ ] **Step 6: Commit mobile behavior**

```powershell
git add web/src/features/interviewStudio/InterviewStudio.tsx web/src/features/interviewStudio/InterviewStudio.module.css web/src/features/interviewStudio/InterviewStudio.test.tsx
git commit -m "fix: AI adapt interview workspace for mobile"
```

### Task 4: Browser acceptance, regression gates, and handoff

**Files:**
- Create: `docs/reports/2026-08-16-interview-studio-conversation-workspace-verification.md`
- Modify only if a verified defect is found: files listed in Tasks 1–3

- [ ] **Step 1: Run the affected frontend matrix**

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio src/features/mockInterviewVoice src/features/pilotMascot
npm.cmd run build
```

- [ ] **Step 2: Run repository checks proportionate to the UI-only change**

```powershell
uv run ruff check .
uv run mypy src
uv run oc smoke --static-dir web/dist
git diff --check 6015a16468fc38eb07446f2eded8e596759249c3..HEAD
```

- [ ] **Step 3: Perform real mounted browser acceptance**

Using the existing isolated Chinese sample and local provider, verify `1440×900` light/dark and `390×844` light:

```text
conversation history scroll
answer workspace scroll
回答 / 依据 switching and evidence highlight
voice transcript confirmation
sticky submit action
mobile drawer focus and Escape
Haru safe placement
zero horizontal overflow
```

Capture the six screenshots named in the approved design and visually read them back.

- [ ] **Step 4: Run an independent read-only code review**

Review for P0/P1/P2 with emphasis on duplicated submission, lost answer state, evidence focus, nested scrolling, mobile focus, and Haru overlap. Fix verified findings with tests before proceeding.

- [ ] **Step 5: Write and commit the verification report**

Record exact commands, counts, browser dimensions, screenshot paths, remaining risks, and cleanup status. Then:

```powershell
git add -f docs/reports/2026-08-16-interview-studio-conversation-workspace-verification.md
git commit -m "docs: AI verify interview workspace redesign"
```

- [ ] **Step 6: Final cleanliness gate**

```powershell
git status --short
git diff --check 6015a16468fc38eb07446f2eded8e596759249c3..HEAD
```

Expected: clean worktree and exit code 0.
