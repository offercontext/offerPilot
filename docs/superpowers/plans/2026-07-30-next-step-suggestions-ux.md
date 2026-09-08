# Next-Step Suggestions UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Keep each checkbox group as a small commit and stop at the review checkpoints.

**Goal:** Add a read-only, session-only next-step suggestion slice to the workbench and application detail without adding API calls, AI calls, database writes, or cross-domain handoffs.

**Architecture:** Build one pure TypeScript rule function over an explicit known | unknown fact snapshot. Render its result through one controlled component that shows at most one main action and one always-visible source-risk notice. AppShell owns the session disposition by applicationId + suggestionId, while existing navigation callbacks remain the only side effects.

**Tech Stack:** React, TypeScript, Ant Design, Vitest, React Testing Library, and existing AppShell/ApplicationDetail/InterviewV01View navigation callbacks.

**Design source:** docs/superpowers/specs/2026-07-30-next-step-suggestions-ux-design.md

**Feature baseline:** 05b1178. The implementation diff for this slice must be measured from this commit, not from origin/main, because the branch already contains unrelated historical backend work.

**Scope boundary:** This plan does not add backend routes, database fields, queries for per-application Material Kit/Fit/Interview history, AI calls, handoffs, automatic status changes, reminders, Offer aggregation, or persistent snooze/ignore state.

---

## File map and ownership

- Create web/src/lib/nextStepSuggestions.ts: fact-state types, discriminated destinations, stable state-key construction, valid interview classification, candidate/risk derivation.
- Create web/src/lib/nextStepSuggestions.test.ts: exhaustive pure-rule tests using explicit fact snapshots and compile-time destination assertions.
- Create web/src/components/NextStepSuggestions.tsx: presentational card, source-risk notice, snooze/ignore session callbacks, and destination dispatch.
- Create web/src/components/NextStepSuggestions.module.css: layout and collapsed/ignored visual states using existing design tokens.
- Create web/src/components/NextStepSuggestions.test.tsx: rendering, session disposition, Chinese fixed copy, source labels, and no-navigation assertions.
- Modify web/src/layout/AppShell.tsx: construct workbench/detail fact snapshots from already loaded data, own SuggestionSessionState, and pass only existing navigation callbacks.
- Create web/src/layout/AppShell.nextStepSuggestions.test.tsx: workbench/detail fact-boundary and no-write integration tests.
- Modify web/src/components/ApplicationDetail.tsx: render the controlled suggestion component for the current application and map existing detail/Pilot/interview callbacks.
- Modify web/src/features/dashboard/DashboardView.tsx: reuse its existing Material Kit query for the workbench fact snapshot, with explicit loading/error/partial-coverage states; do not add a new query.
- Modify web/src/components/InterviewV01View.tsx only if the existing event-selection callback needs a typed adapter; do not add a second event-selection heuristic.
- Modify existing navigation tests only where a destination adapter is already covered, keeping the new assertions in the dedicated files above.

---

### Task 1: Freeze the fact snapshot and destination types

**Files:**
- Create: web/src/lib/nextStepSuggestions.ts
- Test: web/src/lib/nextStepSuggestions.test.ts

- [ ] **Step 1: Write failing type/fixture tests.**

Define a single-application input fixture with explicit fields for facts that are actually available globally and explicit unknown values for facts not loaded globally:

~~~ts
const workbenchFacts = {
  application: { status: 'known', value: application },
  availableResumes: { status: 'known', value: resumes },
  events: { status: 'known', value: eventsForApplication },
  offers: { status: 'known', value: offersForApplication },
  confirmedKnowledge: { status: 'known', value: confirmedKnowledge },
  practiceStats: { status: 'known', value: practiceStats },
  jd: { status: 'unknown', reason: 'not_supported' },
  fitReview: { status: 'unknown', reason: 'not_loaded' },
  materialKit: { status: 'unknown', reason: 'not_loaded' },
  interviewPreparationHistory: { status: 'unknown', reason: 'not_loaded' },
  mockInterviewHistory: { status: 'unknown', reason: 'not_loaded' },
} satisfies NextStepFacts;
~~~

Assert that TypeScript accepts only fully formed destinations. A review history destination must contain applicationId, eventId, and reviewId; a selection destination must contain applicationId and must not require an event or review ID. Use compile-time negative assertions rather than comments alone:

~~~ts
// @ts-expect-error review history cannot omit reviewId
const incompleteHistory: InterviewReviewHistoryDestination = {
  kind: 'interview_review_history',
  applicationId: 1,
  eventId: 2,
};

// @ts-expect-error a multi-event selection cannot guess an eventId
const guessedSelection: InterviewEventSelectionDestination = {
  kind: 'interview_event_selection',
  applicationId: 1,
  eventId: 2,
};
~~~

- [ ] **Step 2: Run the focused test to verify it fails.**

Run from web:

~~~powershell
npm.cmd test -- --run src/lib/nextStepSuggestions.test.ts
~~~

Expected: FAIL because the module and discriminated union types do not exist.

- [ ] **Step 3: Implement the minimum shared types.**

Add FactState<T>, NextStepFacts, NextStepSource, NextStepCandidate, SourceRiskNotice, and NextStepSuggestions. Use these exact destination forms:

~~~ts
type InterviewEventDestination = {
  kind: 'interview_event';
  applicationId: number;
  eventId: number;
};

type InterviewEventSelectionDestination = {
  kind: 'interview_event_selection';
  applicationId: number;
};

type InterviewReviewDestination = {
  kind: 'interview_review';
  applicationId: number;
  eventId: number;
};

type InterviewReviewHistoryDestination = {
  kind: 'interview_review_history';
  applicationId: number;
  eventId: number;
  reviewId: number;
};

type InterviewReviewSelectionDestination = {
  kind: 'interview_review_selection';
  applicationId: number;
};
~~~

Do not add optional eventId or reviewId fields to any destination. SourceRiskNotice may omit readonlyDestination; when omitted the UI must be text-only.

- [ ] **Step 4: Run the focused type tests.**

~~~powershell
npx tsc -b --pretty false
npm.cmd test -- --run src/lib/nextStepSuggestions.test.ts
~~~

Expected: tsc exits with code 0; the two @ts-expect-error annotations suppress exactly the intentional invalid assignments, and the focused tests pass.

- [ ] **Step 5: Commit the type boundary.**

~~~powershell
git add web/src/lib/nextStepSuggestions.ts web/src/lib/nextStepSuggestions.test.ts
git commit -m "feat: AI define next-step fact types"
~~~

---

### Task 2: Implement deterministic fact derivation and event classification

**Files:**
- Modify: web/src/lib/nextStepSuggestions.ts
- Test: web/src/lib/nextStepSuggestions.test.ts

- [ ] **Step 1: Add failing rule tests for unknown facts and empty Resume sources.**

Cover these exact outcomes:

1. availableResumes.status === unknown yields no “缺简历” candidate.
2. availableResumes.status === known with an empty array yields one “选择简历” candidate, but its sources contain no status=current Resume source and no “当前使用来源” label; the only allowed neutral label is “已检查简历库”.
3. jd.status === unknown yields no “缺 JD”, “JD 已变化”, or “确认岗位信息” candidate.
4. fitReview, materialKit, and interview history with not_loaded never become “未评估”“缺材料” or a history claim.
5. Workbench mode with insufficient known facts yields exactly one application_detail candidate with fixed title/reason “查看投递详情以确认下一步”. Detail mode may return no candidate when its known facts are insufficient.

- [ ] **Step 2: Add failing event and history navigation tests.**

Use events with valid scheduled_at and positive finite integer duration_minutes:

- one current/future event -> interview_event with its eventId;
- multiple current/future events -> interview_event_selection with only applicationId;
- one ended event with no Review -> interview_review with applicationId + eventId;
- one ended event with exactly one Review -> interview_review_history with applicationId + eventId + reviewId;
- multiple ended events -> interview_review_selection with only applicationId;
- missing/invalid date, NaN, zero, negative, or non-integer duration -> unknown, no interview destination;
- invisible or soft-deleted event -> excluded;
- duplicate scheduled time -> sort by created_at DESC, then id DESC without auto-selecting.

- [ ] **Step 3: Run the tests to verify they fail.**

~~~powershell
npm.cmd test -- --run src/lib/nextStepSuggestions.test.ts
~~~

Expected: FAIL because no derivation rules exist.

- [ ] **Step 4: Implement the pure rules.**

Implement deriveNextStepSuggestions(facts, context, now) with no React, service, Axios, Date mutation, or side effect. It must:

- treat unknown as unknown and never infer absence;
- keep JD permanently unknown/not_supported in this slice;
- require a known, non-empty Resume collection before attaching a current Resume source;
- use a stable sorted available-Resume-set identifier whose entries are id + version tuples in stateKey, never a nonexistent current-resume identity; prefer an existing content fingerprint or updated_at when present, otherwise hash a deterministic canonical representation of the Resume content_json and identity fields;
- validate event dates and durations before current/future/ended classification;
- return all candidates for tests but let the renderer choose one main action;
- return source risks separately, always visible and independent of snooze/ignore;
- set status=frozen only for existing frozen Proposal/Review/Material/confirmed Knowledge inputs, changed only for explicit source-status facts, and current only for known current inputs.

Use stable serialization of sorted { id, version } Resume tuples and explicit fact statuses for stateKey; do not use only numeric IDs, arrival order, wall-clock time, or unstable object JSON. The version must be an opaque existing version/fingerprint or a deterministic digest; never concatenate raw content_json into stateKey. Add a regression where the Resume ID is unchanged but its version/content fingerprint changes: the old snoozed/ignored state must no longer hide the candidate.

The regression must be explicit:

~~~ts
const before = deriveNextStepSuggestions(factsWithResumeVersion('v1'), 'detail', now);
const after = deriveNextStepSuggestions(factsWithResumeVersion('v2'), 'detail', now);
expect(after.candidates[0].stateKey).not.toBe(before.candidates[0].stateKey);
~~~

- [ ] **Step 5: Run the pure-rule suite.**

~~~powershell
npm.cmd test -- --run src/lib/nextStepSuggestions.test.ts
~~~

Expected: PASS for all known/unknown, event validity, navigation discriminator, source-risk, and Resume-label cases.

- [ ] **Step 6: Commit the rules.**

~~~powershell
git add web/src/lib/nextStepSuggestions.ts web/src/lib/nextStepSuggestions.test.ts
git commit -m "feat: AI derive evidence-aware next steps"
~~~

---

### Task 3: Build the read-only suggestion component and session states

**Files:**
- Create: web/src/components/NextStepSuggestions.tsx
- Create: web/src/components/NextStepSuggestions.module.css
- Test: web/src/components/NextStepSuggestions.test.tsx

- [ ] **Step 1: Write failing render and interaction tests.**

Assert:

- at most one main action is rendered, even when the rule function returns multiple candidates;
- at most one source-risk region is rendered, and it remains visible when the main action is snoozed or ignored;
- “稍后处理” moves the action into a recoverable session-only collapsed section;
- “忽略” hides the action for the current session;
- a mismatched stateKey makes the action visible again;
- a risk without readonlyDestination renders plain text and clicking its container does not invoke navigation;
- a risk with a destination invokes only the provided read-only navigation callback;
- known-empty Resume collection does not render “当前使用来源”;
- two applications with the same suggestionId call onSetDisposition with their own applicationId and cannot change each other’s state;
- fixed system copy is Chinese while dynamic company, position, JD, Resume, and evidence text remains unchanged;
- no service, mutation, handoff, or browser-storage mock is called.

- [ ] **Step 2: Run the tests to verify they fail.**

~~~powershell
npm.cmd test -- --run src/components/NextStepSuggestions.test.tsx
~~~

Expected: FAIL because the component and styles do not exist.

- [ ] **Step 3: Implement the controlled component.**

Use props equivalent to:

~~~ts
type NextStepSuggestionsProps = {
  suggestions: NextStepSuggestions;
  sessionState: SuggestionSessionState | null;
  onSetDisposition: (applicationId: number, suggestionId: string, state: SuggestionSessionState | null) => void;
  onNavigate: (destination: NextStepDestination | ReadonlyDestination) => void;
};
~~~

The component must only call onNavigate with an already validated discriminated destination. It must call onSetDisposition with the immutable applicationId supplied by the parent, so two applications with the same suggestionId cannot share session state. It must not import services, call fetch, call AI, mutate handoff state, or write localStorage. Use fixed Chinese copy for labels, empty states, source status, and action buttons. Preserve dynamic source text exactly.

- [ ] **Step 4: Run the component tests.**

~~~powershell
npm.cmd test -- --run src/components/NextStepSuggestions.test.tsx
~~~

Expected: PASS, with no API write calls.

- [ ] **Step 5: Commit the read-only component.**

~~~powershell
git add web/src/components/NextStepSuggestions.tsx web/src/components/NextStepSuggestions.module.css web/src/components/NextStepSuggestions.test.tsx
git commit -m "feat: AI render read-only next-step suggestions"
~~~

---

### Task 4: Add AppShell-owned session state and existing-fact adapters

**Files:**
- Modify: web/src/layout/AppShell.tsx
- Modify: web/src/features/dashboard/DashboardView.tsx
- Test: web/src/layout/AppShell.nextStepSuggestions.test.tsx

- [ ] **Step 1: Write failing integration tests.**

Use existing query mocks and navigation callbacks to assert:

- AppShell supplies currently loaded global facts: Application, Event, Offer, Resume, confirmed Knowledge, and practice stats. DashboardView retains and reuses its existing Material Kit query for the workbench only;
- the workbench Material Kit fact is known only when the existing query has settled successfully and covers every displayed active application; loading, error, and partial coverage are explicit unknown/not_loaded states;
- Fit Review, interview preparation history, and Mock Interview history remain unknown/not_loaded rather than being fetched;
- JD is unknown/not_supported, and no new JD request occurs;
- the workbench with insufficient per-application facts renders exactly one neutral application_detail action and does not render “缺 JD/未评估/缺材料”; when the existing Material Kit query is loading, errored, or partially covering applications it is insufficient, not an empty Material Kit;
- ApplicationDetail receives the same fact snapshot shape, but only known local facts can produce concrete actions;
- invoking the same pure function with the same explicit fact snapshot from workbench and detail produces equal candidates and source risks;
- session state is keyed by applicationId + suggestionId, survives component remount within AppShell, and resets when stateKey changes;
- refresh creates fresh session state rather than a persisted business decision.

- [ ] **Step 2: Run the integration tests to verify they fail.**

~~~powershell
npm.cmd test -- --run src/layout/AppShell.nextStepSuggestions.test.tsx
~~~

Expected: FAIL because AppShell and DashboardView do not mount the new component or session reducer.

- [ ] **Step 3: Implement the AppShell reducer/state.**

Add this keyed in-memory state:

~~~ts
type SuggestionSessionState = {
  stateKey: string;
  disposition: 'snoozed' | 'ignored';
};
type SuggestionSessionMap = Record<string, SuggestionSessionState>;
~~~

Update state using the current map entry, not a stale render closure. On each render, apply a state only when its stateKey equals the candidate’s current key; otherwise remove it and show the candidate. Never write the map to API, localStorage, or any domain object.

- [ ] **Step 4: Implement fact adapters without new requests.**

Construct one NextStepFacts adapter for the shared rule function. AppShell passes global facts to ApplicationDetail; DashboardView combines those facts with its already-existing Material Kit query and passes a per-application snapshot to the same rule function. Do not add another Material Kit request. Treat loading, error, and partial coverage as unknown; only a settled, successful, complete result is known. A known empty Resume array is the only case that may produce “选择简历”, and it must have no current-source tag. ApplicationDetail does not generate a Material Kit action unless it already has a known Material Kit fact and an existing no-write entry callback; otherwise the candidate is omitted rather than rendering an inert button.

- [ ] **Step 5: Run integration tests.**

~~~powershell
npm.cmd test -- --run src/layout/AppShell.nextStepSuggestions.test.tsx
~~~

Expected: PASS; request spies show no additional read or write calls.

- [ ] **Step 6: Commit the AppShell integration.**

~~~powershell
git add web/src/layout/AppShell.tsx web/src/features/dashboard/DashboardView.tsx web/src/layout/AppShell.nextStepSuggestions.test.tsx
git commit -m "feat: AI connect next-step session state"
~~~

---

### Task 5: Mount the detail view and map navigation without handoff

**Files:**
- Modify: web/src/components/ApplicationDetail.tsx
- Modify: web/src/components/InterviewV01View.tsx only if an existing event-selection callback needs a typed adapter
- Test: web/src/components/ApplicationDetail.nextStepSuggestions.test.tsx
- Test: web/src/components/InterviewV01View.nextStepSuggestions.test.tsx if the adapter changes this file

- [ ] **Step 1: Write failing navigation tests.**

Cover:

- one valid current/future event -> exact applicationId + eventId;
- multiple valid events -> selection with only applicationId;
- one ended event with no Review -> interview_review with applicationId + eventId;
- one ended event with exactly one Review -> interview_review_history with reviewId;
- multiple ended events -> interview_review_selection without a guessed Review ID;
- Material Kit navigation does not write materialKitHandoff and does not auto-open the drawer;
- Pilot navigation does not start AI or create a proposal;
- unknown fact states render empty/neutral content instead of guessed actions;
- detail pages with unknown Material Kit facts do not render a Material Kit “前往” button; the workbench uses only the existing settled/complete Material Kit query;
- two applications with the same suggestionId receive separate applicationId-bound disposition callbacks and cannot hide each other’s candidate.

- [ ] **Step 2: Run the tests to verify they fail.**

~~~powershell
npm.cmd test -- --run src/components/ApplicationDetail.nextStepSuggestions.test.tsx src/components/InterviewV01View.nextStepSuggestions.test.tsx
~~~

Expected: FAIL because the detail and interview surfaces do not mount the new suggestion component/adapter.

- [ ] **Step 3: Implement existing-callback adapters.**

Map each destination kind to existing callbacks only:

- application_detail -> existing detail opener;
- pilot_opportunity_fit -> existing Pilot opportunity-fit opener;
- material_kit_entry -> an existing user-clicked, no-write Material Kit entry callback only when the current surface can provide it; ApplicationDetail omits this candidate when it cannot, rather than mapping it back to the already-open detail or creating a handoff;
- interview_event -> existing event-selection/index opener with exact event ID;
- interview_event_selection -> existing interview index opener with application ID;
- interview_review / interview_review_history / selection -> existing read-only review entry;
- opportunity_fit_history -> existing history viewer.

Do not add a new route, API call, write service, or implicit first-event/first-Review fallback. If an existing callback cannot accept a required destination context, render the candidate as unavailable until a typed adapter exists; do not drop IDs.

- [ ] **Step 4: Run focused UI tests.**

~~~powershell
npm.cmd test -- --run src/components/ApplicationDetail.nextStepSuggestions.test.tsx src/components/InterviewV01View.nextStepSuggestions.test.tsx
~~~

Expected: PASS; all write spies remain at zero.

- [ ] **Step 5: Commit navigation integration.**

~~~powershell
git add web/src/components/ApplicationDetail.tsx web/src/components/InterviewV01View.tsx web/src/components/ApplicationDetail.nextStepSuggestions.test.tsx web/src/components/InterviewV01View.nextStepSuggestions.test.tsx
git commit -m "feat: AI route next-step suggestions"
~~~

---

### Task 6: Fixed-copy and regression coverage

**Files:**
- Modify: web/src/components/systemCopyRegression.test.ts
- Modify: web/src/layout/workspaceDrilldown.test.ts
- Modify: web/src/lib/nextStepSuggestions.test.ts
- Modify: web/src/components/NextStepSuggestions.test.tsx

- [ ] **Step 1: Add failing regression assertions.**

Scan only known fixed phrases. Reject fixed English such as Next step, Snooze, Ignore, Current source, and Source changed when introduced in the component, while allowing English user data for company, position, JD, Resume, and evidence excerpts. Also assert:

- “暂无下一步建议” is used only for detail-level insufficient facts;
- workbench insufficient facts uses “查看投递详情以确认下一步”;
- “来源已变化” remains visible regardless of session disposition;
- empty known Resume set never shows “当前使用来源”;
- Resume ID unchanged but version/content fingerprint changed resets the session disposition;
- two applications with the same suggestionId do not share snoozed/ignored state;
- the first candidate in the rule function’s priority order, not merely an arbitrary candidate, is the rendered main action;
- no service/API write method is imported by the new component.

- [ ] **Step 2: Run the regression tests.**

~~~powershell
npm.cmd test -- --run src/components/systemCopyRegression.test.ts src/layout/workspaceDrilldown.test.ts src/lib/nextStepSuggestions.test.ts src/components/NextStepSuggestions.test.tsx
~~~

Expected: PASS with the scan limited to known fixed phrases, not arbitrary English content.

- [ ] **Step 3: Commit regression coverage.**

~~~powershell
git add web/src/components/systemCopyRegression.test.ts web/src/layout/workspaceDrilldown.test.ts web/src/lib/nextStepSuggestions.test.ts web/src/components/NextStepSuggestions.test.tsx
git commit -m "test: AI cover next-step copy and boundaries"
~~~

---

### Task 7: Verification and independent review

**Files:**
- No product files beyond the tasks above.
- Review artifact: current branch diff and test outputs.

- [ ] **Step 1: Run the focused feature suite.**

~~~powershell
Set-Location web
npm.cmd test -- --run src/lib/nextStepSuggestions.test.ts src/components/NextStepSuggestions.test.tsx src/layout/AppShell.nextStepSuggestions.test.tsx src/components/ApplicationDetail.nextStepSuggestions.test.tsx
~~~

Expected: all focused tests pass; no API write spy is non-zero.

- [ ] **Step 2: Run the front-end full suite and build.**

~~~powershell
npm.cmd test -- --run
npm.cmd run build
~~~

Expected: exit code 0. Existing React act warnings may remain, but no test may fail.

- [ ] **Step 3: Verify the no-backend-change boundary.**

~~~powershell
$featureBase = '05b1178'
$backendChanges = git diff --name-only "$featureBase..HEAD" -- src tests
$serviceChanges = git diff --name-only "$featureBase..HEAD" -- web/src/services web/src/types

if ($backendChanges -or $serviceChanges) {
  throw 'Next-step suggestions must not add backend, service, or shared API/type changes.'
}
~~~

Expected: the script exits successfully with no output. This check measures only changes introduced after 05b1178; the full branch hygiene check remains the origin/main diff check in the next step.

- [ ] **Step 4: Run static checks and diff hygiene.**

~~~powershell
git diff --check origin/main..HEAD
git status --short --branch
~~~

Expected: diff check passes and the worktree is clean after the task commits.

- [ ] **Step 5: Perform independent code review.**

Review the final diff against the design, checking unknown-vs-empty facts, all discriminated destinations, empty-Resume source labels, stateKey reset semantics, and zero write/API calls. Any finding must receive a regression test before final handoff.

- [ ] **Step 6: Record the implementation handoff.**

Report final commit SHAs, focused/full front-end test counts, build result, diff check, no-backend-change evidence, and any non-blocking warnings. Do not claim real-AI or browser release verification for this front-end-only slice until the existing release gate is separately run.

---

## Self-review checklist

- [ ] Every design rule has a task: unknown facts, no global JD, non-empty Resume source labels, valid event classification, single/multiple event selection, unique/multiple historical Review, Offer deferral, source-risk visibility, session-only state, and zero writes.
- [ ] No task adds a backend route, API call, database field, AI call, handoff write, or persistent user decision.
- [ ] Destination IDs are required by discriminated union; no optional-ID fallback remains.
- [ ] The known-empty Resume regression explicitly rejects “当前使用来源”.
- [ ] Destination tests include @ts-expect-error compile-time negatives.
- [ ] Component tests assert the first priority candidate and applicationId-bound session callbacks.
- [ ] The workbench neutral suggestion and detail empty state have distinct tests.
- [ ] Fixed-copy scanning is limited to known phrases and does not reject user-provided English data.
- [ ] No placeholder text appears in this plan.
