# Continuous Voice Interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add an opt-in, confirmation-gated continuous voice loop to Interview Studio while preserving text, manual voice, evidence, idempotency, recovery, and history semantics.

**Architecture:** Keep \`InterviewStudio\` as the owner of Attempt, Turn, answer submission, next-question generation, evidence, and result-unknown recovery. Add a pure frontend controller/reducer that emits media/UI intents and accepts lifecycle events; extend \`VoiceAnswerComposer\` with controlled intents so its existing recorder, TTS, VAD, recognition, and offline Whisper paths remain the single media implementation. A presentational panel exposes opt-in/preflight/status/fallback controls and stores only a non-authorizing local preference.

**Tech Stack:** React 18, TypeScript, Ant Design, Vitest/jsdom, existing \`MediaRecorder\`, browser TTS, \`voiceSessionController\`, \`voiceCaptureRuntime\`, offline Whisper controller, and Mock Interview services.

---

## File map and allowlist

Files may be created or modified only in this list unless a failing test proves an additional existing boundary must change:

- Create \`web/src/features/mockInterviewVoice/continuousVoiceSessionController.ts\` for the pure state machine, generation fencing, commands, and lifecycle cleanup.
- Create \`web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts\` for transitions, countdown, fencing, fallback, and privacy-facing command tests.
- Create \`web/src/features/interviewStudio/ContinuousVoiceModePanel.tsx\` for the opt-in/preflight/status/action surface; it must not own media, timers, or business requests.
- Create \`web/src/features/interviewStudio/ContinuousVoiceModePanel.module.css\` for responsive panel styling.
- Create \`web/src/features/interviewStudio/InterviewStudio.test.tsx\` for the media-zero-request and confirmed-answer integration contract.
- Modify \`web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx\` to accept controlled commands and emit non-sensitive lifecycle events while keeping standard mode unchanged.
- Modify \`web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx\` with controlled-intent, stale-command, and fallback tests.
- Modify \`web/src/features/interviewStudio/InterviewStudio.tsx\` to own one controller instance, route controlled events, submit only confirmed text, continue the existing recovery flow, and map observable status to Haru activity.
- Modify \`web/src/features/interviewStudio/InterviewStudio.module.css\` for the single responsive content layout and fixed answer area.
- Modify \`web/src/features/interviewStudio/interviewStudioController.ts\` and its test only if an integration test proves an explicit continuous lifecycle action is required; preserve existing actions.
- Use the tracked \`artifacts/continuous-voice-interview/\` directory only for final browser screenshots and release evidence; never add raw audio or PCM fixtures.
- Create \`docs/reports/2026-08-15-continuous-voice-interview-release.md\` for grouped gate results, browser evidence, environment risks, and the no-API/no-migration statement.

No backend, database migration, public API, provider, Story, Knowledge, Memory, Application, Offer, or README file is in scope.

## Task 1: Diagnose backend startup and establish the baseline

**Files:** None.

- [ ] **Step 1: Confirm process ownership before starting tests.**

Run from the worktree:

\`\`\`powershell
git status --short --branch
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'pytest|python|uv|node|vite' -and $_.CommandLine -match 'offerpilot|pytest|uv run|vite' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
\`\`\`

Expected: no process owned by this worktree before the baseline run. Ignore unrelated worktrees and the Codex browser process.

- [ ] **Step 2: Collect a real backend target with a bounded diagnostic command.**

Run:

\`\`\`powershell
Measure-Command { uv run pytest --collect-only -q tests/test_mock_interview_api.py }
\`\`\`

Expected: collection completes and reports collected tests. If it is silent for 60 seconds, inspect child process CPU/command line/import state; do not label the product tests failed. The prior invalid \`tests/test_mock_interviews.py\` path is an invocation error, not a product result.

- [ ] **Step 3: Install only workspace frontend dependencies and run the existing voice baseline.**

Run:

\`\`\`powershell
cd web
npm.cmd ci
npm.cmd test -- --run src/features/mockInterviewVoice/voiceSessionController.test.ts src/features/mockInterviewVoice/voiceActivityDetector.test.ts
cd ..
\`\`\`

Expected: existing tests pass. If \`npm ci\` cannot reach the registry, record the exact environment failure in the release report and use the lockfile for later claims.

## Task 2: Build the pure continuous session state machine with TDD

**Files:**

- Create \`web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts\`.
- Create \`web/src/features/mockInterviewVoice/continuousVoiceSessionController.ts\`.

- [ ] **Step 1: Write the first failing transition test.**

The test must assert this contract:

\`\`\`ts
it('reads, waits for speech, counts down only after speech, and stops without submitting', () => {
  const events = createHarness();
  const controller = createContinuousVoiceSessionController(events.dependencies);
  controller.enable('筱哲的问题');
  expect(events.states.at(-1)?.status).toBe('preflight');
  controller.preflightSucceeded();
  expect(events.commands.map((item) => item.type)).toEqual(['read_question']);
  controller.questionReadFinished();
  expect(events.states.at(-1)?.status).toBe('waiting_for_speech');
  controller.speechDetected();
  controller.silenceDetected();
  expect(events.states.at(-1)?.status).toBe('end_candidate');
  expect(events.commands.at(-1)?.type).toBe('start_end_countdown');
  controller.countdownElapsed();
  expect(events.commands.at(-1)?.type).toBe('stop_recording');
  expect(events.commands.some((item) => item.type === 'submit_answer')).toBe(false);
});
\`\`\`

- [ ] **Step 2: Run the test and verify the expected RED failure.**

Run \`cd web; npm.cmd test -- --run src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts; cd ..\`.

Expected: fail because the module/API does not exist. Fix only test setup errors before implementation.

- [ ] **Step 3: Implement the minimal reducer/controller API.**

Define explicit status and event unions for \`disabled\`, \`preflight\`, \`reading_question\`, \`waiting_for_speech\`, \`listening\`, \`end_candidate\`, \`transcribing\`, \`reviewing_transcript\`, \`submitting_confirmed_answer\`, \`generating_next_question\`, \`paused\`, \`fallback_standard\`, \`result_unknown\`, \`completed\`, and \`closed\`. Expose \`enable\`, \`disable\`, \`preflightSucceeded\`, \`preflightFailed\`, \`questionReadFinished\`, \`skipReading\`, \`speechDetected\`, \`silenceDetected\`, \`cancelEndCandidate\`, \`countdownElapsed\`, \`manualStop\`, \`recordingStopped\`, \`transcriptReady\`, \`transcriptionFailed\`, \`confirmTranscript\`, \`answerSubmissionSucceeded\`, \`answerSubmissionUnknown\`, \`nextQuestionReady\`, \`nextQuestionUnknown\`, \`pause\`, \`resume\`, \`fallback\`, \`complete\`, \`close\`, \`getSnapshot\`, and \`subscribe\`.

Commands are descriptive only: \`read_question\`, \`start_recording\`, \`start_end_countdown\`, \`cancel_end_countdown\`, \`stop_recording\`, \`start_transcription\`, \`submit_answer\`, \`generate_next_question\`, and \`cleanup\`. The controller never calls browser APIs or business services. Illegal transitions return the unchanged snapshot and emit no command. \`close\`, \`disable\`, \`fallback\`, \`pause\`, mode changes, and \`beginNextTurn\` increment generation and invalidate late callbacks; every async-capable command carries the generation.

- [ ] **Step 4: Add RED/GREEN cases for each boundary.**

Write and run each test before its implementation for: silent waiting never starting the countdown; speech during \`end_candidate\` cancelling it; manual stop and the five-minute limit entering transcription/review only; no direct \`transcribing\` to submission; exactly one submission command after explicit confirmation; stale generation events updating nothing; pause/resume/fallback/result-unknown/completed/closed cleanup; TTS/media mutual exclusion; and command payloads containing no PCM, raw audio, temporary captions, or provider data.

- [ ] **Step 5: Run the pure group and commit.**

\`\`\`powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts
cd ..
git add web/src/features/mockInterviewVoice/continuousVoiceSessionController.ts web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts
git commit -m "feat: AI add continuous voice session controller"
\`\`\`

Expected: all controller tests pass and the commit contains no media or API implementation.

## Task 3: Expose controlled intents through the existing VoiceAnswerComposer

**Files:**

- Modify \`web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx\`.
- Modify \`web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx\`.

- [ ] **Step 1: Write failing controlled-command tests.**

Extend the existing browser fixture and assert:

\`\`\`ts
it('runs one controlled read/start/stop cycle without auto-confirming', async () => {
  const onEvent = vi.fn();
  const rendered = await renderComposer({ continuous: true, onContinuousEvent: onEvent, continuousCommand: { id: 1, type: 'read_question' } });
  rendered.utterances[0].onend?.();
  await rendered.rerender({ continuousCommand: { id: 2, type: 'start_recording' } });
  expect(rendered.browser.getUserMedia).toHaveBeenCalledTimes(1);
  await rendered.rerender({ continuousCommand: { id: 3, type: 'stop_recording' } });
  expect(rendered.props.onConfirmTranscript).not.toHaveBeenCalled();
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'review_available' }));
});
\`\`\`

Also add tests for stale command IDs after unmount/re-record, hidden-page pause, no automatic model download, microphone rejection fallback, and unchanged standard controls.

- [ ] **Step 2: Run the tests RED.**

Run \`cd web; npm.cmd test -- --run src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx; cd ..\`.

Expected: TypeScript/test failure because controlled props/events are absent.

- [ ] **Step 3: Add a controlled adapter around existing handlers.**

Add typed \`continuous\` props, monotonic command IDs, and a non-sensitive event union. Route \`read_question\` through \`readQuestion\`, \`start_recording\` through \`startRecording\`, \`stop_recording\` through the existing recorder stop path, and \`reset\` through \`resetVoiceDraft\`. Add \`preflight\` that checks existing capabilities and requests/releases a stream only after the explicit user click. Do not create a second recorder, VAD, recognition, or transcription implementation.

Fence controlled callbacks with command IDs and the existing recording/transcription generations. Emit only lifecycle state, capability/error status, and review availability; never emit audio, PCM, VAD frames, temporary subtitles, or logs. Keep manual editing and \`confirmTranscript\` as the only path that calls \`onConfirmTranscript\`.

- [ ] **Step 4: Run the focused composer and existing voice groups.**

\`\`\`powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx src/features/mockInterviewVoice/voiceSessionController.test.ts src/features/mockInterviewVoice/voiceCaptureRuntime.test.ts src/features/mockInterviewVoice/offlineWhisperController.test.ts
cd ..
\`\`\`

Expected: all old and new tests pass with no React act warnings or uncaught callbacks.

- [ ] **Step 5: Commit the adapter.**

\`\`\`powershell
git add web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx
git commit -m "feat: AI expose controlled voice composer intents"
\`\`\`

## Task 4: Integrate the controller with Interview Studio and add the status panel

**Files:**

- Create \`web/src/features/interviewStudio/ContinuousVoiceModePanel.tsx\`.
- Create \`web/src/features/interviewStudio/ContinuousVoiceModePanel.module.css\`.
- Create \`web/src/features/interviewStudio/InterviewStudio.test.tsx\`.
- Modify \`web/src/features/interviewStudio/InterviewStudio.tsx\`.
- Modify \`web/src/features/interviewStudio/InterviewStudio.module.css\`.

- [ ] **Step 1: Write the failing integration contract first.**

Test with mocked existing Mock Interview services and the existing browser fixture:

\`\`\`ts
it('does not call a business service during media stages and submits confirmed text once', async () => {
  const services = installStudioServiceSpies();
  await renderStudioWithCandidate('筱哲', { theme: 'light' });
  await user.click(screen.getByRole('button', { name: '开启连续语音模式' }));
  await user.click(screen.getByRole('button', { name: '开始连续语音面试' }));
  expect(services.answer).not.toHaveBeenCalled();
  expect(services.nextQuestion).not.toHaveBeenCalled();
  await finishMockedVoiceReviewWithEditedText('我先定位日志，再完成回滚。');
  expect(services.answer).toHaveBeenCalledTimes(1);
  expect(services.answer).toHaveBeenCalledWith(expect.objectContaining({ answerText: '我先定位日志，再完成回滚。' }));
  expect(services.nextQuestion).toHaveBeenCalledTimes(1);
});
\`\`\`

Add cases for real and quick-practice contexts, switching back to standard, result-unknown retry with the original key, saved preference not auto-enabling, and closed/unmounted late callbacks.

- [ ] **Step 2: Run the integration test RED.**

Run \`cd web; npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx; cd ..\`.

Expected: fail because panel/controller wiring is missing; separate fixture failures from the missing feature failure.

- [ ] **Step 3: Implement one controller per Studio instance.**

Create the controller in a ref so React StrictMode cannot create two capture runtimes. Subscribe/unsubscribe in an effect, map status to \`VoiceAnswerActivity\`, and send controlled commands to the existing composer. The panel must start preflight only from a user click, show privacy text, expose skip narration, pause/resume, cancel countdown, stop, continue, rerecord, edit, confirm, fallback, and retry states, and use Ant components/tokens.

Store only \`offerpilot:interview-studio:continuous-voice-preference\` as a non-authorizing preference. Every Studio mount starts in standard mode; a saved preference changes copy only and never requests a microphone or starts capture.

- [ ] **Step 4: Preserve Studio business semantics.**

Route controlled confirmation into the existing \`submitAnswer(answerOverride)\` path. The Studio must call \`submitInterviewStudioAnswer\` once with confirmed text, retain \`turnKey\`, save the existing voice review snapshot only through its current service, and call the existing question generator only after answer success. On question success, send \`nextQuestionReady\`; on \`202\`/unknown, freeze the current key and expose the existing retry path. No media-stage callback may call a service.

For both \`application_event\` and \`quick_practice\`, leave service context, evidence, max-five-turn behavior, close confirmation, historical read-only behavior, and result recovery unchanged. Do not add fields to service payloads.

- [ ] **Step 5: Implement responsive layout and observable Haru status.**

Keep one main content scroll region plus evidence drawer and fixed answer area. Reuse OfferPilot surface/font/token classes, avoid nested card scrollbars, and add responsive CSS for 1440×900, 1280×800, and 390×844. Map only observable continuous states to existing Haru activity labels; never expose unconfirmed transcript or raw media.

- [ ] **Step 6: Run Studio groups and the production build.**

\`\`\`powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx src/features/interviewStudio/interviewStudioController.test.ts src/features/interviewStudio/evidenceLocator.test.ts
npm.cmd test -- --run src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx
npm.cmd run build
cd ..
\`\`\`

Expected: targeted tests and TypeScript production build pass.

- [ ] **Step 7: Commit the integration.**

\`\`\`powershell
git add web/src/features/interviewStudio/ContinuousVoiceModePanel.tsx web/src/features/interviewStudio/ContinuousVoiceModePanel.module.css web/src/features/interviewStudio/InterviewStudio.test.tsx web/src/features/interviewStudio/InterviewStudio.tsx web/src/features/interviewStudio/InterviewStudio.module.css
git commit -m "feat: AI integrate continuous voice interview studio"
\`\`\`

## Task 5: Add privacy, concurrency, and fallback regression coverage

**Files:**

- Modify \`web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts\`.
- Modify \`web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx\`.
- Modify \`web/src/features/interviewStudio/InterviewStudio.test.tsx\`.

- [ ] **Step 1: Add failing negative tests before hardening.**

Cover: zero business service calls from preflight/TTS/waiting/listening/countdown/stop/transcription; no confirmation before the button; no second \`getUserMedia\`, recorder, TTS, or next-question command under StrictMode/remount; late TTS/VAD/permission/runtime/recognition/transcription callbacks ignored after generation changes; hidden-page stop/pause with explicit user resume; TTS canceled before recording and recording stopped before TTS; TTS/MediaRecorder/VAD/recognition/Whisper fallback to text/manual review; result-unknown retry with the original key; and raw audio/PCM/VAD/interim fields absent from events, service payloads, localStorage, and logs.

- [ ] **Step 2: Run RED, implement only the failing boundary, and run GREEN after each group.**

Use the focused commands from Tasks 2–4, never one full-repository command. Any old-test failure must be fixed without changing its product contract.

- [ ] **Step 3: Commit regression coverage and hardening.**

\`\`\`powershell
git add web/src/features/mockInterviewVoice/continuousVoiceSessionController.test.ts web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx web/src/features/interviewStudio/InterviewStudio.test.tsx web/src/features/mockInterviewVoice/continuousVoiceSessionController.ts web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx web/src/features/interviewStudio/InterviewStudio.tsx
git commit -m "test: AI harden continuous voice recovery boundaries"
\`\`\`

## Task 6: Independent review and grouped release gates

**Files:**

- Create \`docs/reports/2026-08-15-continuous-voice-interview-release.md\`.
- Use the existing final screenshots under \`artifacts/continuous-voice-interview/\`.

- [ ] **Step 1: Run an independent code review before browser claims.**

Ask a fresh reviewer to inspect the diff against \`e74d54e\`, the design document, and the allowlist. Require P0/P1/P2 findings for transitions, generation fencing, privacy, standard compatibility, service idempotency/recovery, and responsive accessibility. Fix every P0/P1/P2 and rerun affected groups; record only genuinely external residual risks.

- [ ] **Step 2: Run backend gates in groups.**

\`\`\`powershell
uv run pytest -q tests/test_mock_interview_api.py tests/test_mock_interview_repository.py tests/test_mock_interview_diagnostics.py tests/test_mock_interview_review_drafts.py
uv run pytest -q tests/test_mock_interview_browser_harness.py tests/test_smoke.py
uv run ruff check .
uv run mypy src
\`\`\`

If collection again exceeds 60 seconds, inspect process/CPU/import state and split by file; do not convert timeout into a product failure.

- [ ] **Step 3: Run frontend gates in groups.**

\`\`\`powershell
cd web
npm.cmd test -- --run src/features/mockInterviewVoice/ src/features/interviewStudio/
npm.cmd run build
cd ..
uv run oc smoke --static-dir web/dist
\`\`\`

If Vitest does not expand directory arguments, enumerate exact test files with \`Get-ChildItem -Recurse -Filter '*.test.*'\` and rerun with explicit paths.

- [ ] **Step 4: Perform browser acceptance with 筱哲 in light mode.**

Use the in-app browser and an isolated local fixture/provider. Capture preparation/opt-in, first question/waiting, countdown/evidence, editable transcript, confirmed answer/follow-up/next narration, completed review, and Haru drag/status. Run at 1440×900 and 390×844; verify no horizontal overflow, Haru overlap, answer-area overlap, or extra nested scroll container, and repeat one standard-mode flow. Assert browser network requests are zero through media/transcription and appear only after explicit confirmed-text submission.

- [ ] **Step 5: Clean temporary data and write the report.**

Record exact commands/results, design baseline, commits, screenshots, backend diagnostic outcome, review outcome, no migration/API statement, and remaining environment-only risks. Remove only feature-owned temporary directories and stop feature-owned dev/test processes after verifying absolute paths; preserve unrelated worktrees/processes.

- [ ] **Step 6: Final verification and commit without push/merge.**

\`\`\`powershell
git diff --check
git status --short --branch
git diff --stat e74d54e..HEAD
git add -f docs/reports/2026-08-15-continuous-voice-interview-release.md
git add artifacts/continuous-voice-interview
git commit -m "docs: AI record continuous voice interview release evidence"
\`\`\`

Expected: clean worktree on \`feat/20260815-continuous-voice-interview\`, no push, no merge, no migration, and all claims backed by fresh command or browser evidence.

## Self-review against the design

- State machine, illegal transitions, countdown cancellation, five-minute limit, manual stop, and generation fencing are covered by Task 2.
- Single media implementation, TTS/MediaRecorder/VAD/Whisper fallbacks, hidden-page cleanup, StrictMode, and zero raw-media persistence are covered by Tasks 3 and 5.
- Opt-in preflight, standard/continuous switching, confirmed-only submission, evidence/next-question ownership, result-unknown recovery, quick-practice boundaries, and Haru observable status are covered by Tasks 4 and 5.
- Accessibility, reduced motion, responsive layouts, screenshot evidence, grouped gates, independent review, and cleanup are covered by Task 6.
- No placeholder/TODO step is used. No design contract requires a backend/API/schema revision, so the design commit remains unchanged.
