# Pilot Mascot Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real Haru activity feedback, Pilot-page presence, session-level background reply notification, and persisted zoom controls without changing backend or AI contracts.

**Architecture:** Extend the existing Live2D adapter to return a presentation controller, keep the contextual ChatPanel mounted while hidden, and lift a sanitized activity/completion event into AppShell. The mascot remains a view of Chat state; it never starts requests or writes domain data.

**Tech Stack:** React 18, TypeScript, Ant Design, PixiJS, pixi-live2d-display, CSS Modules, Vitest/JSDOM.

---

## File map

- Modify `web/src/features/pilotMascot/live2dRuntime.ts`: runtime controller, activity motion/expression, zoom-aware fit.
- Modify `web/src/features/pilotMascot/live2dRuntime.test.ts`: controller, zoom, reduced-motion and cleanup tests.
- Modify `web/src/features/pilotMascot/pilotMascotPreference.ts`: safe zoom persistence.
- Modify `web/src/features/pilotMascot/pilotMascotPreference.test.ts`: zoom boundary tests.
- Modify `web/src/features/pilotMascot/PilotMascot.tsx`: controller updates, zoom menu, notification-aware accessible copy.
- Modify `web/src/features/pilotMascot/PilotMascot.module.css`: menu composition and Pilot-page compact placement.
- Modify `web/src/features/pilotMascot/PilotMascot.test.tsx`: runtime activity and zoom interactions.
- Modify `web/src/components/ChatPanel/index.tsx`: hidden-but-mounted operation and sanitized lifecycle callback.
- Modify `web/src/components/ChatPanel/model.ts`: close/background lifecycle pure rules.
- Modify `web/src/components/ChatPanel/model.test.ts`: ordinary close no longer aborts; explicit replacement still aborts.
- Modify `web/src/components/ChatPanel/layout.test.ts`: hidden rendering/close contract.
- Modify `web/src/layout/AppShell.tsx`: single contextual ChatPanel owner, completion notification, Pilot-page mascot.
- Modify `web/src/layout/AppShell.mascot.test.tsx`: real mounted lifecycle and exact-conversation notification.
- Create `docs/reports/2026-08-13-pilot-mascot-activity-browser-acceptance.md`: verified screenshots and runtime observations.

### Task 1: Zoom preference and runtime controller

- [ ] Add failing preference tests for default `1`, persisted `0.8–1.3`, non-finite input and blocked storage.
- [ ] Run `npm.cmd test -- --run src/features/pilotMascot/pilotMascotPreference.test.ts` and confirm the new tests fail because zoom helpers are absent.
- [ ] Add `PILOT_MASCOT_ZOOM_KEY`, `readPilotMascotZoom` and `writePilotMascotZoom`; clamp in 0.1 steps and fail closed to `1`.
- [ ] Add failing runtime tests using the real injected runtime to assert `baseScale * zoom`, resize stability, activity method calls, idempotence, reduced-motion no-op and dispose safety.
- [ ] Run `npm.cmd test -- --run src/features/pilotMascot/live2dRuntime.test.ts` and confirm expected failures.
- [ ] Change `mount()` to return `{ setActivity, setZoom, dispose }`; extend the injected model interface with guarded `motion(group, index, priority)` and `expression(name)` calls.
- [ ] Map `thinking` to a distinct existing Idle motion, `success/error` to one-shot Tap motions plus expressions, and keep all activity failures non-fatal.
- [ ] Run both targeted suites and confirm green.
- [ ] Commit with `feat: AI control Pilot mascot presentation`.

### Task 2: Mascot controls and visual states

- [ ] Add failing component tests proving the mounted controller receives prop activity/zoom changes, menu buttons clamp zoom, reset works, disabled limits are exposed, and the completion label changes the accessible action.
- [ ] Run the component suite and confirm failures are about missing controller/control behavior.
- [ ] Update `PilotMascot` props with `zoom`, `onZoomChange`, `notification`; retain the controller and synchronize it in effects.
- [ ] Add three 40px context-menu controls with current tabular percentage; preserve hide, Escape, Shift+F10 and focus restoration.
- [ ] Refine CSS with concentric menu/control radii, explicit transitions, non-overlapping hit areas and a Pilot-page compact modifier; do not add page-load motion.
- [ ] Run preference/runtime/component tests and production TypeScript check.
- [ ] Commit with `feat: AI add Pilot mascot zoom controls`.

### Task 3: Background reply lifecycle and Pilot-page integration

- [ ] Replace the existing close-aborts-chat model test with a failing test that close preserves chat/confirmation while explicit new-chat replacement aborts ordinary chat.
- [ ] Add a failing ChatPanel contract test for `open=false`: component remains mounted, request continues, sanitized callback reports `thinking` then `{ status: success|error, conversationId }`, and no response text is emitted.
- [ ] Add failing AppShell mounted tests for: contextual drawer close keeps one ChatPanel owner; completion shows unread mascot; click reopens the exact conversation; hidden mascot exposes rail notification; Pilot page renders the compact mascot and clicking idle focuses Composer.
- [ ] Run the targeted model, layout and AppShell tests and confirm expected failures.
- [ ] Introduce `PilotActivityEvent` with only activity, outcome and conversation ID; report it from `sendMessage` completion/failure without Provider text or user content.
- [ ] Keep one contextual ChatPanel mounted with `open={contextualPilotPanelOpen}` and hide it via its existing null/render boundary only after moving the request owner above that boundary. Do not mount drawer and page owners for the same active request.
- [ ] Store one session-only notification in AppShell, clear it only when the exact conversation is opened, and pass exact conversation selection into ChatPanel through a request token/ID prop.
- [ ] Render Haru on the Pilot page with a compact mode and route idle clicks to the existing Composer focus token.
- [ ] Preserve explicit stop/new-chat abort behavior and confirmation lock reconciliation.
- [ ] Run all targeted ChatPanel, AppShell and mascot tests.
- [ ] Commit with `feat: AI notify completed Pilot replies`.

### Task 4: Verification, review and browser acceptance

- [ ] Run targeted tests for every modified test file and record exact counts.
- [ ] Run `npm.cmd test -- --run`, `npm.cmd run build`, and `npx.cmd tsc -b`; require exit code 0.
- [ ] Run `git diff --check` and inspect `git diff <baseline>..HEAD` to prove there are no backend/API/database/model-asset changes.
- [ ] Request independent P0/P1/P2 code review; fix all findings with failing regressions first and rerun affected gates.
- [ ] Start the existing isolated Ark-configured local deployment without changing the formal provider configuration.
- [ ] In the built-in browser at light mode and at least 1440×900, capture: Pilot page compact Haru; closed drawer thinking; completed answer notification; zoom menu plus enlarged Haru.
- [ ] Verify one read-only Pilot request continues after drawer close, creates one Provider request, and clicking the notification opens its exact conversation. Verify hide/rail fallback, zoom persistence, reduced motion and zero new console errors.
- [ ] Save screenshots under `artifacts/2026-08-13-pilot-mascot-activity/` and write the browser acceptance report with dimensions, paths and observed request count.
- [ ] Stop only services started for this acceptance, release ports, remove temporary data, and keep the user’s pre-existing Ark preview untouched unless it is the verified target.
- [ ] Commit evidence with `test: AI verify Pilot mascot activity`.

## Release boundary

- No API, database, Provider, prompt, retry, confirmation or evidence-contract changes.
- No `l2d-widget` dependency and no new model assets.
- Continue to enforce the existing Live2D NOTICE and publication-license release gate.
- Do not merge or push without a separate user instruction.
