# Interview Studio Conversation Workspace Verification

Date: 2026-08-16

Branch: `feat/20260815-continuous-voice-interview`

Baseline: `6015a16468fc38eb07446f2eded8e596759249c3`

## Scope

- Redesign the full-screen Interview Studio as a desktop two-column workspace.
- Keep the interview conversation and confirmed candidate answers in the left timeline.
- Move text answer, voice answer, offline transcription, delivery review, and frozen evidence into a right-side workspace.
- Keep the confirmation, idempotency, recovery, service, and persistence contracts unchanged.
- Use a bottom drawer for the answer workspace below the desktop breakpoint.

No backend, public API, database, Provider, or evidence-contract changes were introduced.

## Commits

- `83b4752 docs: AI define interview studio conversation layout`
- `7802b5b docs: AI plan interview studio conversation workspace`
- `5548edd test: AI define interview workspace layout`
- `19d41d8 feat: AI redesign interview studio workspace`
- `fe49bf6 fix: AI polish interview question focus`

## TDD evidence

- The first component/layout run failed on the six new desktop/mobile workspace expectations before implementation.
- The mobile browser review exposed a programmatic question-heading focus outline; a focused CSS contract test failed before the rule was added.
- The final affected frontend run passed 26 files and 164 tests.

## Automated verification

- `npm.cmd test -- --run src/features/interviewStudio src/features/mockInterviewVoice src/features/pilotMascot`: 26 files, 164 tests passed.
- `npm.cmd run build`: passed; 3939 modules transformed.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed for 73 source files.
- `uv run oc smoke --static-dir web/dist`: passed for health, SPA, Application creation, Chat confirmation, and confirmation cards.
- `git diff --check`: passed.

The frontend run retains existing React `act(...)` warnings in `OfflineWhisperModelCard` tests; no test failed.

## Browser acceptance

The built product was exercised through the in-app browser against an isolated copy of local data and a localhost-only deterministic Provider. No external Provider was called for this visual acceptance.

- Desktop 1440×900: left conversation timeline and right answer workspace remain independently readable without horizontal overflow.
- Desktop 1440×900: switching to `依据` shows the frozen JD, resume, and current question references without changing the interview state.
- Mobile 390×844: the conversation remains the primary page and opens the answer workspace as a bottom drawer.
- Mobile 390×844: the answer drawer is vertically scrollable, retains sticky confirmation actions, closes independently, and restores focus.
- Programmatic question focus no longer produces a decorative black outline.
- QA services and browser tabs were stopped and ports `65460` / `65461` were released.

## Screenshots

| Evidence | Size | SHA-256 |
| --- | --- | --- |
| `artifacts/2026-08-16-interview-studio-conversation-workspace/02-desktop-answer-workspace-dark-1440x900.png` | 1440×900 | `8F6FEBCCF914B053937C3494E09E265D01AF26BA1C2C37AB13C07C3EB0B5B215` |
| `artifacts/2026-08-16-interview-studio-conversation-workspace/03-desktop-evidence-workspace-dark-1440x900.png` | 1440×900 | `5CFDF1DFC156E994D5A79C3877C71DDA4CF389B2C72014343BB9948A988D75C2` |
| `artifacts/2026-08-16-interview-studio-conversation-workspace/05-mobile-conversation-light-final-390x844.png` | 390×844 | `3B248D70F45811FC50DDD53320E8BB31DB5FA02AC997303DDBD402F47205AC35` |
| `artifacts/2026-08-16-interview-studio-conversation-workspace/06-mobile-answer-drawer-light-390x844.png` | 390×844 | `97E9AD594CE06F8900EDF66BDF9F9AC801B13ADB8F7F7C41D6C5908D6BDA15DA` |

## Remaining risks

- This change did not rerun the entire repository test matrix; verification was scoped to the affected frontend features plus the standard static, build, and local smoke gates.
- Browser acceptance used a deterministic localhost Provider, so it validates UI behavior rather than remote Provider stability.
- An independent sub-agent code review was not run because this execution did not have delegation authorization; review was performed locally against the approved design and test matrix.
- The temporary QA data directory could not be recursively removed because the execution environment rejected the deletion command. It is outside the repository at `%TEMP%\offerpilot-interview-ui-codex`, contains only the isolated local QA copy, and the processes and ports using it have been stopped.
