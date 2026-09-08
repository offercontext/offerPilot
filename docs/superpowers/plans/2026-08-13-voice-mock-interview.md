# Voice Mock Interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a browser-first, privacy-preserving voice answer experience to the existing event-bound Mock Interview without changing its backend text contract.

**Architecture:** A focused browser capability layer owns TTS, recording and local-only speech recognition. `VoiceAnswerComposer` owns ephemeral audio and exposes only confirmed text; `MockInterviewDrawer` continues to own the existing Attempt/Turn workflow. Haru receives decorative activity updates through an optional callback.

**Tech Stack:** React 18, TypeScript, Ant Design, MediaRecorder, Web Speech APIs, Vitest/JSDOM, existing OfferPilot Mock Interview services.

---

### Task 1: Persist the approved design and freeze the implementation boundary

**Files:**
- Create: `docs/superpowers/specs/2026-08-13-voice-mock-interview-design.md`
- Create: `docs/superpowers/plans/2026-08-13-voice-mock-interview.md`

- [ ] Verify both documents contain no `TBD`, `TODO`, placeholder, remote-STT promise, backend migration, or audio persistence promise.
- [ ] Run `git diff --check` and expect exit code 0.
- [ ] Commit with `docs: AI design voice mock interview`.

### Task 2: Add browser capability normalization

**Files:**
- Create: `web/src/features/mockInterviewVoice/voiceInterviewCapability.ts`
- Create: `web/src/features/mockInterviewVoice/voiceInterviewCapability.test.ts`

- [ ] Write failing parameterized tests for MediaRecorder/TTS detection, missing SpeechRecognition, unsupported local processing, `available`, `downloadable`, `downloading`, `unavailable`, rejected availability and install success/failure.
- [ ] Run `npm.cmd test -- --run src/features/mockInterviewVoice/voiceInterviewCapability.test.ts` and confirm the failure is caused by the missing module.
- [ ] Implement typed wrappers that never invoke remote recognition and always set `processLocally=true`.
- [ ] Re-run the test and expect all cases to pass.

### Task 3: Build the ephemeral voice answer controller and UI

**Files:**
- Create: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx`
- Create: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.module.css`
- Create: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx`

- [ ] Write failing tests for text/voice mode, TTS play/stop, recording start/pause/resume/stop, audio preview, re-record cleanup, local transcript updates, manual fallback, language-pack install, confirm-only output, permission denial, pending disablement and unmount cleanup.
- [ ] Use injected browser dependencies in tests; assert real component behavior rather than mock call choreography.
- [ ] Run the focused test and verify RED.
- [ ] Implement the minimum component and cleanup lifecycle required by the tests.
- [ ] Add the polished answer-studio layout, 40px controls, status text, keyboard focus, narrow layout, dark theme and reduced-motion behavior.
- [ ] Re-run the focused test and expect all cases to pass without new console errors.

### Task 4: Integrate with Mock Interview and preserve the text contract

**Files:**
- Modify: `web/src/components/MockInterviewDrawer.tsx`
- Create: `web/src/components/MockInterviewDrawer.voice.test.tsx`
- Modify: `web/src/layout/AppShell.tsx`
- Modify: `web/src/features/pilotMascot/live2dRuntime.ts`
- Modify: `web/src/features/pilotMascot/PilotMascot.tsx`
- Modify: `web/src/features/pilotMascot/PilotMascot.test.tsx`
- Modify: `web/src/features/pilotMascot/live2dRuntime.test.ts`

- [ ] Write failing mounted tests proving unconfirmed voice text does not change `draft.answer`, confirmed text does, the original answer service receives only confirmed text, pending states freeze controls, and closing with an unsaved recording requires confirmation.
- [ ] Write failing mascot tests for `speaking`, `listening`, `transcribing` and idle cleanup under normal and reduced-motion settings.
- [ ] Run the focused tests and verify RED.
- [ ] Add the optional activity callback and mount `VoiceAnswerComposer` in the answer stage without changing any Mock Interview service request shape.
- [ ] Extend the mascot activity union and decorative motion mapping, keeping the text state as the source of truth.
- [ ] Re-run the focused tests and expect them to pass.

### Task 5: Regression and safety verification

**Files:**
- Modify only tests required to express existing mounted Mock Interview behavior if the new accessible labels intentionally replace old labels.

- [ ] Run all Mock Interview, mascot and voice tests.
- [ ] Run the complete frontend suite with `npm.cmd test -- --run`.
- [ ] Run `npm.cmd run build`.
- [ ] Run `git diff --check`.
- [ ] Inspect `git diff --name-only main..HEAD` and confirm there are no backend, API, database, Knowledge, Story or service changes.
- [ ] Verify test assertions cover zero audio persistence and zero additional network requests.

### Task 6: Browser acceptance and screenshot evidence

**Files:**
- Create: `docs/reports/2026-08-13-voice-mock-interview-browser-acceptance.md`
- Create: `artifacts/2026-08-13-voice-mock-interview/01-voice-answer-ready.png`
- Create: `artifacts/2026-08-13-voice-mock-interview/02-transcript-confirmation.png`

- [ ] Start an isolated local backend and frontend with a temporary data directory.
- [ ] Seed a Chinese candidate named “筱哲”, one Application, one valid Interview Event, one Resume and current JD Version.
- [ ] Open Mock Interview in the in-app browser using light mode and a viewport at least 1440×900.
- [ ] If microphone automation is unavailable, inject only the browser media/transcript dependencies and visibly label the demonstration “Mock 语音输入”; do not mock the Mock Interview business API.
- [ ] Capture the recording-ready state and editable transcript-confirmation state.
- [ ] Verify text mode still works, unconfirmed text is not submitted, no audio persistence endpoint is called, console errors are zero, and only local app/API URLs are used.
- [ ] In `finally`, close browser tabs, stop process trees, release ports and delete temporary data.
- [ ] Re-open both images and visually check clipping, Chinese copy, contrast and focus hierarchy.

### Task 7: Final verification and commit

**Files:**
- Modify: `docs/reports/2026-08-13-voice-mock-interview-browser-acceptance.md`

- [ ] Re-run focused tests, full frontend tests, TypeScript production build and `git diff --check` from the final working tree.
- [ ] Review every design requirement against implementation and record any unimplemented optional local-model boundary as a remaining risk rather than a success claim.
- [ ] Stage files with a separate `git add` command.
- [ ] Commit with a Conventional Commit title in the required format, such as `feat: AI add voice mock interview`.
