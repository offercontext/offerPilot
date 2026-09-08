# Onboarding Action Routing Design

Date: 2026-07-13

Status: approved

Branch: `feat/20260713-onboarding-actions`

## Goal

Make every milestone in the four-step onboarding checklist actionable without creating data implicitly. A user can use the checklist to reach the exact configuration, resume, application, or Pilot surface needed to complete the milestone.

## Scope

### Included

- Clickable behavior for all four onboarding cards, including already-completed cards.
- Navigation to Settings and opening the existing AI settings drawer.
- Navigation to the resume library and visible focus on its create or upload entry points.
- Opening the existing add-application form.
- A temporary, accessible Pilot highlight that does not expand the desktop Pilot rail.
- A mobile fallback that opens the normal Pilot drawer before highlighting its composer.
- Frontend unit and browser acceptance coverage.

### Excluded

- New onboarding persistence fields or API endpoints.
- Automatic creation of resumes, applications, conversations, or messages.
- Changes to how the four completion states are calculated.
- Changes to AI provider configuration or write-confirmation policy.

## Chosen Approach

Use a single semantic action callback from `OnboardingChecklist` to `DashboardView` and then to `AppShell`. The checklist owns presentation and sends one of four action keys. `AppShell` owns application-level navigation, modal and drawer state, and Pilot focus state.

This preserves the existing component boundaries: the onboarding component does not import routing, the resume library does not need to know why it was opened, and Pilot focus state remains owned by the shell that renders both the rail and drawer variants.

## User Flows

| Milestone | Action | Result |
| --- | --- | --- |
| `configure_ai` | Click `配置 AI` | Set the active view to `settings`, then open the existing AI settings drawer. |
| `create_primary_resume` | Click `创建主简历` | Set the active view to `resumes` and place a short-lived focus treatment on the resume library's creation entry point. The user chooses whether to upload, start from a blank structure, or use a sample. |
| `create_first_application` | Click `添加第一条投递` | Open the existing `AddApplicationForm`. The user must submit the form before any record is created. |
| `send_first_pilot_message` | Click `向 Pilot 发出一条消息` | On wide screens, keep the docked Pilot rail closed as-is and pulse its boundary and composer. On narrow screens, open the normal Pilot drawer and apply the same treatment. Focus moves to the composer, but no text is sent automatically. |

Completion state remains read from the existing onboarding endpoint. A completed card remains actionable so the checklist can still be used as a shortcut.

## Interaction Design

- Every card is rendered as a native button with an accessible name and visible keyboard focus state.
- The complete state remains visually distinct but does not disable the card.
- Hover and focus make cards visibly interactive without resembling a destructive control.
- Pilot focus lasts for a bounded interval and clears itself before another action can run.
- The animation uses a pulse outline and composer emphasis rather than changing layout or automatically expanding the desktop rail.
- Under `prefers-reduced-motion: reduce`, use a static high-contrast outline instead of animation.
- An `aria-live` announcement identifies the destination or focused Pilot composer.

## Component Boundaries

- `OnboardingChecklist`: defines the four action keys, renders button semantics, and calls `onAction(key)`.
- `DashboardView`: passes the callback through without owning global UI state.
- `AppShell`: maps keys to view changes, existing drawer and modal setters, and a temporary Pilot-focus token.
- `ResumeLibraryView`: receives an optional focus request and applies it to a stable creation target after the resume view renders.
- `ChatPanel`: accepts an optional focus request and highlights/focuses the composer without changing message state.
- Component CSS modules own their focus and reduced-motion styles; no global event bus is introduced.

## Error Handling

- A missing or unknown action key performs no state change and is covered by TypeScript exhaustiveness.
- If the target view has not rendered yet, the destination component waits until its target exists before focusing it.
- A rapid second action replaces the prior temporary highlight instead of leaving overlapping timers.
- Failed data queries in the destination views retain their existing retry and error UI; clicking an onboarding shortcut does not create a fallback write.

## Testing Strategy

### Frontend Unit Tests

- All four cards render as buttons and emit their exact action key when clicked, whether completed or not.
- The dashboard forwards the action callback.
- App-shell action routing opens Settings plus the AI drawer, resumes plus focus request, the application form, and Pilot focus.
- The Pilot highlight clears after its bounded lifetime and reduced-motion styling remains usable.
- No onboarding action creates data before the user submits an existing form or sends a message.

### Browser Acceptance

1. Open the dashboard with the checklist visible.
2. Click each of the four cards and verify its corresponding destination behavior.
3. Confirm the AI drawer is open after the settings navigation.
4. Confirm the resume library is active and its create entry is focused or highlighted.
5. Confirm the application form opens and no application exists until submission.
6. Confirm desktop Pilot is not expanded, but its composer is visibly highlighted and keyboard-focused.
7. Repeat the Pilot action in a narrow viewport and confirm the drawer opens before the composer is highlighted.

## Acceptance Criteria

- Each of the four displayed onboarding milestones responds to mouse, keyboard, and touch activation.
- The configuration, resume, and application actions reach the intended existing UI without implicit writes.
- The Pilot action creates a clear, temporary visual cue without expanding the desktop rail.
- Mobile users can reach the Pilot composer even when the rail is unavailable.
- Completed cards remain usable shortcuts.
- Existing onboarding completion and collapse behavior continues to work.
- Frontend tests, production build, and browser walkthrough pass.

## Breaking Changes

None. The onboarding API and its persisted completion state are unchanged.

## Deferred Follow-Ups

- Persist an explicit user choice to permanently hide onboarding after completion.
- Add contextual copy that explains which resume creation path best fits a new user.
- Measure action completion funnels only if product analytics is introduced with an explicit privacy design.
