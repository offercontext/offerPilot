# Onboarding Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make all four onboarding milestones actionable, routing users to the existing UI needed to complete each milestone and visibly focusing Pilot without implicit writes.

**Architecture:** OnboardingChecklist emits a typed action key; DashboardView forwards it; AppShell resolves a pure action intent into existing navigation, drawer, modal, and focus-token state. ResumeLibraryView and ChatPanel consume the transient tokens locally so focus animation and timers never affect backend onboarding state.

**Tech Stack:** React 18, TypeScript, Ant Design, CSS Modules, Vitest, Vite, existing FastAPI-backed onboarding status API.

---

## File Structure

- Create: web/src/features/onboarding/actionRouting.ts — typed action names and pure desktop/mobile routing intents.
- Create: web/src/features/onboarding/actionRouting.test.ts — exact intent coverage for all four actions.
- Modify: web/src/features/onboarding/OnboardingChecklist.tsx — native-button action cards and callback contract.
- Modify: web/src/features/onboarding/OnboardingChecklist.module.css — interactive card, keyboard-focus, and reduced-motion styles.
- Modify: web/src/features/onboarding/OnboardingChecklist.test.tsx — semantic action-card assertions.
- Modify: web/src/features/dashboard/DashboardView.tsx — forward the app-shell action callback.
- Create: web/src/features/dashboard/DashboardView.test.ts — forwarding contract assertion.
- Modify: web/src/layout/AppShell.tsx — translate action intents into existing Settings, resume, application, and Pilot state.
- Modify: web/src/layout/AppShell.test.ts — shell routing and token-propagation assertions.
- Modify: web/src/components/ResumeLibraryView.tsx — consume a temporary resume-entry focus token.
- Modify: web/src/components/ResumeLibraryView.module.css — resume-entry highlight and reduced-motion fallback.
- Create: web/src/components/ResumeLibraryView.test.ts — focus-target contract assertion.
- Modify: web/src/components/ChatPanel/index.tsx — consume a temporary whole-Pilot focus token.
- Modify: web/src/components/ChatPanel/Composer.tsx — focus the text area and highlight the composer for the token lifetime.
- Modify: web/src/components/ChatPanel/ChatPanel.module.css — Pilot/composer pulse and reduced-motion fallback.
- Modify: web/src/components/ChatPanel/layout.test.ts — Pilot focus prop and CSS contract assertions.

### Task 1: Define and Test the Pure Onboarding Routing Intent

**Files:**

- Create: web/src/features/onboarding/actionRouting.ts
- Create: web/src/features/onboarding/actionRouting.test.ts

- [ ] **Step 1: Write the failing intent tests**

~~~ts
import { describe, expect, it } from 'vitest';
import { onboardingActionIntent } from './actionRouting';

describe('onboardingActionIntent', () => {
  it('maps the three navigation or form actions without implicit writes', () => {
    expect(onboardingActionIntent('configure_ai', true)).toEqual({
      view: 'settings',
      openAISettings: true,
    });
    expect(onboardingActionIntent('create_primary_resume', true)).toEqual({
      view: 'resumes',
      focusResumeEntry: true,
    });
    expect(onboardingActionIntent('create_first_application', true)).toEqual({
      openApplicationForm: true,
    });
  });

  it('keeps desktop Pilot docked but opens the mobile drawer', () => {
    expect(onboardingActionIntent('send_first_pilot_message', true)).toEqual({
      focusPilot: true,
      openPilotDrawer: false,
    });
    expect(onboardingActionIntent('send_first_pilot_message', false)).toEqual({
      focusPilot: true,
      openPilotDrawer: true,
    });
  });
});
~~~

- [ ] **Step 2: Run the focused test to verify it fails**

Run: npm.cmd test -- --run src/features/onboarding/actionRouting.test.ts

Expected: FAIL because ./actionRouting does not exist.

- [ ] **Step 3: Write the minimal typed intent implementation**

~~~ts
export const ONBOARDING_ACTIONS = [
  'configure_ai',
  'create_primary_resume',
  'create_first_application',
  'send_first_pilot_message',
] as const;

export type OnboardingAction = (typeof ONBOARDING_ACTIONS)[number];

export type OnboardingActionIntent = {
  view?: 'settings' | 'resumes';
  openAISettings?: true;
  openApplicationForm?: true;
  focusResumeEntry?: true;
  focusPilot?: true;
  openPilotDrawer?: boolean;
};

export function onboardingActionIntent(
  action: OnboardingAction,
  pilotRailAvailable: boolean,
): OnboardingActionIntent {
  switch (action) {
    case 'configure_ai':
      return { view: 'settings', openAISettings: true };
    case 'create_primary_resume':
      return { view: 'resumes', focusResumeEntry: true };
    case 'create_first_application':
      return { openApplicationForm: true };
    case 'send_first_pilot_message':
      return { focusPilot: true, openPilotDrawer: !pilotRailAvailable };
  }
}
~~~

- [ ] **Step 4: Re-run the focused test**

Run: npm.cmd test -- --run src/features/onboarding/actionRouting.test.ts

Expected: PASS with 2 tests.

- [ ] **Step 5: Commit the routing contract**

~~~bash
git add web/src/features/onboarding/actionRouting.ts web/src/features/onboarding/actionRouting.test.ts
git commit -m "test: AI cover onboarding action intents"
~~~

### Task 2: Make Every Onboarding Card a Semantic Action

**Files:**

- Modify: web/src/features/onboarding/OnboardingChecklist.tsx
- Modify: web/src/features/onboarding/OnboardingChecklist.module.css
- Modify: web/src/features/onboarding/OnboardingChecklist.test.tsx
- Modify: web/src/features/dashboard/DashboardView.tsx
- Create: web/src/features/dashboard/DashboardView.test.ts

- [ ] **Step 1: Write failing card and forwarding contract tests**

Append the following assertions to the existing checklist test after the progress assertion:

~~~ts
expect(html.match(/data-onboarding-action=/g)).toHaveLength(4);
expect(html).toContain('data-onboarding-action="configure_ai"');
expect(html).toContain('data-onboarding-action="create_primary_resume"');
expect(html).toContain('data-onboarding-action="create_first_application"');
expect(html).toContain('data-onboarding-action="send_first_pilot_message"');
expect(html).toContain('<button');
~~~

Create DashboardView.test.ts:

~~~ts
import { describe, expect, it } from 'vitest';
import source from './DashboardView.tsx?raw';

describe('DashboardView onboarding action contract', () => {
  it('forwards onboarding actions from the checklist to AppShell', () => {
    expect(source).toContain('onOnboardingAction: (action: OnboardingAction) => void;');
    expect(source).toContain('onAction={onOnboardingAction}');
  });
});
~~~

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: npm.cmd test -- --run src/features/onboarding/OnboardingChecklist.test.tsx src/features/dashboard/DashboardView.test.ts

Expected: FAIL because cards are div elements and DashboardView has no forwarded action prop.

- [ ] **Step 3: Implement the checklist callback and dashboard forwarding**

In OnboardingChecklist.tsx, import OnboardingAction from actionRouting, add this prop, and replace the step map outer div with this button. Keep the completed class and do not add disabled:

~~~tsx
interface Props {
  status: OnboardingStatus;
  onCollapse: () => void;
  onAction: (action: OnboardingAction) => void;
}

<button
  key={step.key}
  type="button"
  data-onboarding-action={step.key}
  className={completed ? [styles.step, styles.completed].join(' ') : styles.step}
  onClick={() => onAction(step.key)}
>
  <span className={styles.stepIcon} aria-hidden="true">
    {completed ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
  </span>
  <span className={styles.stepLabel}>{step.label}</span>
</button>
~~~

In DashboardView.tsx, import OnboardingAction, add onOnboardingAction to Props and the component parameters, then supply it to OnboardingChecklist:

~~~tsx
<OnboardingChecklist
  status={onboardingQ.data}
  onCollapse={() => collapseOnboarding.mutate()}
  onAction={onOnboardingAction}
/>
~~~

In OnboardingChecklist.module.css, retain existing spacing/background rules and add this reset, interaction, and reduced-motion behavior:

~~~css
.step {
  width: 100%;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 180ms var(--op-ease), box-shadow 180ms var(--op-ease), transform 180ms var(--op-ease);
}
.step:hover {
  border-color: var(--op-primary);
  transform: translateY(-1px);
}
.step:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--op-primary) 35%, transparent);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .step { transition: none; }
  .step:hover { transform: none; }
}
~~~

- [ ] **Step 4: Re-run the focused tests**

Run: npm.cmd test -- --run src/features/onboarding/OnboardingChecklist.test.tsx src/features/dashboard/DashboardView.test.ts

Expected: PASS with zero failures.

- [ ] **Step 5: Commit the interactive-card surface**

~~~bash
git add web/src/features/onboarding/OnboardingChecklist.tsx web/src/features/onboarding/OnboardingChecklist.module.css web/src/features/onboarding/OnboardingChecklist.test.tsx web/src/features/dashboard/DashboardView.tsx web/src/features/dashboard/DashboardView.test.ts
git commit -m "feat: AI activate onboarding checklist actions"
~~~

### Task 3: Route Settings, Resume, and Application Milestones Through AppShell

**Files:**

- Modify: web/src/layout/AppShell.tsx
- Modify: web/src/layout/AppShell.test.ts
- Modify: web/src/components/ResumeLibraryView.tsx
- Modify: web/src/components/ResumeLibraryView.module.css
- Create: web/src/components/ResumeLibraryView.test.ts

- [ ] **Step 1: Add failing routing and focus tests**

Append this source-contract test to AppShell.test.ts:

~~~ts
it('routes onboarding actions through existing settings, resume, and application surfaces', () => {
  expect(source).toContain('const handleOnboardingAction = (action: OnboardingAction) => {');
  expect(source).toContain('const intent = onboardingActionIntent(action, pilotRailAvailable);');
  expect(source).toContain('navigateToView(intent.view);');
  expect(source).toContain('setAISettingsOpen(true);');
  expect(source).toContain('setAddOpen(true);');
  expect(source).toContain('setResumeOnboardingFocusToken((token) => token + 1);');
  expect(source).toContain('onOnboardingAction={handleOnboardingAction}');
});
~~~

Create ResumeLibraryView.test.ts:

~~~ts
import { describe, expect, it } from 'vitest';
import source from './ResumeLibraryView.tsx?raw';

describe('ResumeLibraryView onboarding focus', () => {
  it('focuses a stable creation-entry target without creating a resume', () => {
    expect(source).toContain('onboardingFocusToken?: number;');
    expect(source).toContain('data-onboarding-target="resume-create"');
    expect(source).toContain('onboardingEntryRef.current?.focus({ preventScroll: true });');
    expect(source).not.toContain('onboardingFocusToken && createDialogMut.mutate()');
  });
});
~~~

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: npm.cmd test -- --run src/layout/AppShell.test.ts src/components/ResumeLibraryView.test.ts

Expected: FAIL because the handler, token state, and resume focus target are absent.

- [ ] **Step 3: Implement AppShell intent handling and resume focus**

In AppShell.tsx, import OnboardingAction and onboardingActionIntent. Add these two states with the existing UI state:

~~~tsx
const [resumeOnboardingFocusToken, setResumeOnboardingFocusToken] = useState(0);
const [pilotOnboardingFocusToken, setPilotOnboardingFocusToken] = useState(0);
~~~

After navigateToView, add the handler:

~~~tsx
const handleOnboardingAction = (action: OnboardingAction) => {
  const intent = onboardingActionIntent(action, pilotRailAvailable);
  if (intent.view) navigateToView(intent.view);
  if (intent.openAISettings) setAISettingsOpen(true);
  if (intent.openApplicationForm) setAddOpen(true);
  if (intent.focusResumeEntry) setResumeOnboardingFocusToken((token) => token + 1);
  if (intent.openPilotDrawer) setChatOpen(true);
  if (intent.focusPilot) setPilotOnboardingFocusToken((token) => token + 1);
};
~~~

Pass onOnboardingAction={handleOnboardingAction} to DashboardView. Pass onboardingFocusToken={resumeOnboardingFocusToken} to ResumeLibraryView.

In ResumeLibraryView.tsx, add onboardingFocusToken to ResumeLibraryViewProps, create a ref plus local temporary state, and add this effect:

~~~tsx
const onboardingEntryRef = useRef<HTMLDivElement>(null);
const [onboardingFocusActive, setOnboardingFocusActive] = useState(false);

useEffect(() => {
  if (!onboardingFocusToken) return;
  setOnboardingFocusActive(true);
  onboardingEntryRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  onboardingEntryRef.current?.focus({ preventScroll: true });
  const timeout = window.setTimeout(() => setOnboardingFocusActive(false), 2400);
  return () => window.clearTimeout(timeout);
}, [onboardingFocusToken]);
~~~

Place the existing header action controls inside the focused region:

~~~tsx
<div
  ref={onboardingEntryRef}
  tabIndex={-1}
  data-onboarding-target="resume-create"
  className={onboardingFocusActive ? [styles.headerActions, styles.onboardingFocus].join(' ') : styles.headerActions}
  aria-label="创建主简历入口"
>
  <Input.Search />
  <Button icon={<PlusOutlined />} onClick={() => createDialogMut.mutate()}>和 Pilot 创建薄版</Button>
  <Button icon={<CloudUploadOutlined />} onClick={() => setUploadOpen(true)}>上传 PDF</Button>
  <Button type="primary" icon={<FileAddOutlined />} onClick={() => sampleMut.mutate()}>用样例开始</Button>
</div>
~~~

Add these rules to ResumeLibraryView.module.css:

~~~css
.onboardingFocus {
  outline: 3px solid color-mix(in srgb, var(--op-primary) 42%, transparent);
  outline-offset: 4px;
  border-radius: 10px;
  animation: onboardingResumePulse 800ms var(--op-ease) 3;
}
@keyframes onboardingResumePulse {
  50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--op-primary) 16%, transparent); }
}
@media (prefers-reduced-motion: reduce) {
  .onboardingFocus { animation: none; }
}
~~~

- [ ] **Step 4: Re-run the focused tests**

Run: npm.cmd test -- --run src/layout/AppShell.test.ts src/components/ResumeLibraryView.test.ts

Expected: PASS with zero failures.

- [ ] **Step 5: Commit the routed non-Pilot actions**

~~~bash
git add web/src/layout/AppShell.tsx web/src/layout/AppShell.test.ts web/src/components/ResumeLibraryView.tsx web/src/components/ResumeLibraryView.module.css web/src/components/ResumeLibraryView.test.ts
git commit -m "feat: AI route onboarding setup actions"
~~~

### Task 4: Highlight and Focus Pilot Without Expanding the Desktop Rail

**Files:**

- Modify: web/src/layout/AppShell.tsx
- Modify: web/src/components/ChatPanel/index.tsx
- Modify: web/src/components/ChatPanel/Composer.tsx
- Modify: web/src/components/ChatPanel/ChatPanel.module.css
- Modify: web/src/components/ChatPanel/layout.test.ts

- [ ] **Step 1: Extend the Pilot layout test before adding focus code**

Add this test to layout.test.ts:

~~~ts
it('supports a bounded onboarding focus cue for the whole Pilot and its composer', async () => {
  const css = await loadCss();

  expect(component).toContain('onboardingFocusToken?: number;');
  expect(component).toContain('styles.onboardingFocus');
  expect(component).toContain('onboardingFocusToken={onboardingFocusToken}');
  expect(css).toContain('.onboardingFocus');
  expect(css).toContain('@keyframes onboardingPilotPulse');
  expect(css).toContain('@media (prefers-reduced-motion: reduce)');
});
~~~

- [ ] **Step 2: Run the focused test to verify it fails**

Run: npm.cmd test -- --run src/components/ChatPanel/layout.test.ts

Expected: FAIL because ChatPanel and Composer do not accept or render an onboarding focus token.

- [ ] **Step 3: Implement the bounded Pilot and composer focus**

In ChatPanel/index.tsx, add onboardingFocusToken?: number to Props. Add this local state and effect within ChatPanel:

~~~tsx
const [onboardingFocusActive, setOnboardingFocusActive] = useState(false);

useEffect(() => {
  if (!onboardingFocusToken || !open) return;
  setOnboardingFocusActive(true);
  const timeout = window.setTimeout(() => setOnboardingFocusActive(false), 2400);
  return () => window.clearTimeout(timeout);
}, [onboardingFocusToken, open]);
~~~

Add data-onboarding-target="pilot" to the existing workspace div. Add styles.onboardingFocus to its className only while onboardingFocusActive. Pass onboardingFocusToken to the existing Composer instance.

In Composer.tsx, add onboardingFocusToken?: number to Props. Import useRef, create rootRef and onboardingFocusActive state, then use this effect:

~~~tsx
const rootRef = useRef<HTMLDivElement>(null);
const [onboardingFocusActive, setOnboardingFocusActive] = useState(false);

useEffect(() => {
  if (!onboardingFocusToken) return;
  setOnboardingFocusActive(true);
  const focusTimer = window.setTimeout(() => {
    rootRef.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
  }, 0);
  const clearTimer = window.setTimeout(() => setOnboardingFocusActive(false), 2400);
  return () => {
    window.clearTimeout(focusTimer);
    window.clearTimeout(clearTimer);
  };
}, [onboardingFocusToken]);
~~~

Attach rootRef to Composer's outer div and append styles.composerOnboardingFocus only while onboardingFocusActive. Do not set the textarea value and do not call onSend inside the effect.

Add these CSS rules to ChatPanel.module.css:

~~~css
.onboardingFocus {
  outline: 3px solid color-mix(in srgb, var(--op-primary) 46%, transparent);
  outline-offset: 4px;
  border-radius: 16px;
  animation: onboardingPilotPulse 800ms var(--op-ease) 3;
}
.composerOnboardingFocus {
  border-radius: 14px;
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--op-primary) 20%, transparent);
}
@keyframes onboardingPilotPulse {
  50% { box-shadow: 0 0 0 10px color-mix(in srgb, var(--op-primary) 14%, transparent); }
}
@media (prefers-reduced-motion: reduce) {
  .onboardingFocus { animation: none; }
}
~~~

Pass onboardingFocusToken={pilotOnboardingFocusToken} to all three ChatPanel render sites in AppShell. Do not change pilotDrawerOpen in the desktop action path; only the narrow-screen intent calls setChatOpen(true).

- [ ] **Step 4: Re-run the focused Pilot and shell tests**

Run: npm.cmd test -- --run src/components/ChatPanel/layout.test.ts src/layout/AppShell.test.ts

Expected: PASS with zero failures.

- [ ] **Step 5: Commit the Pilot focus behavior**

~~~bash
git add web/src/layout/AppShell.tsx web/src/components/ChatPanel/index.tsx web/src/components/ChatPanel/Composer.tsx web/src/components/ChatPanel/ChatPanel.module.css web/src/components/ChatPanel/layout.test.ts
git commit -m "feat: AI highlight Pilot from onboarding"
~~~

### Task 5: Run Release-Appropriate Verification and Browser Acceptance

**Files:**

- Verify only; do not create production files unless a failing verification identifies a scoped defect.

- [ ] **Step 1: Run the focused frontend suite**

Run: npm.cmd test -- --run src/features/onboarding/actionRouting.test.ts src/features/onboarding/OnboardingChecklist.test.tsx src/features/dashboard/DashboardView.test.ts src/layout/AppShell.test.ts src/components/ResumeLibraryView.test.ts src/components/ChatPanel/layout.test.ts

Expected: PASS with zero failures.

- [ ] **Step 2: Run the frontend production build**

Run: npm.cmd run build

Expected: TypeScript compilation and Vite build exit 0.

- [ ] **Step 3: Run the unchanged backend onboarding regression suite**

Run: uv run pytest tests/test_onboarding.py -q

Expected: PASS; onboarding API state and completion calculation remain unchanged.

- [ ] **Step 4: Verify the actual four actions in the Codex browser**

Run: uv run oc start --port 8081

Browser acceptance:

1. Open http://localhost:8081 and keep the dashboard checklist visible.
2. Click 配置 AI and verify both Settings and the AI drawer are open.
3. Return to the dashboard, click 创建主简历, verify the resume library and visible focused entry region, and verify the click has not created a resume.
4. Return to the dashboard, click 添加第一条投递, verify the existing modal opens, then cancel it without writing a record.
5. At desktop width, click 向 Pilot 发出一条消息, verify the rail is not expanded, its outer surface and composer pulse, and the textarea receives focus without sending a message.
6. Set the viewport below 1180 px and repeat the Pilot action; verify the normal drawer opens before its composer receives focus.
7. Repeat a completed onboarding card and verify it remains a shortcut. Inspect the browser console and verify no errors appear.

- [ ] **Step 5: Review the final diff**

Run: git status --short; git diff --check; git log --oneline main..HEAD

Expected: only the scoped commits from Tasks 1–4; no generated assets, secrets, or data files are staged.
