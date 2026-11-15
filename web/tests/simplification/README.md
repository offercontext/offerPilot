# Product simplification browser gate

This gate renders the production `web/dist` in real headless Chromium. It does not use jsdom or replace React components. All HTTP assets and API responses are fulfilled inside the browser context; unknown APIs, external origins and WebSockets fail closed. No backend, real provider, mailbox, secret or installer is used.

## Run in an authorized browser-capable environment

```sh
npm ci --prefix web
npm run build --prefix web
cd web
npx playwright-core install --with-deps chromium
SIMPLIFICATION_EVIDENCE_DIR=../artifacts/simplification-browser npm run test:simplification
```

An explicit `CHROMIUM_PATH` can select an already installed Chromium. The pure Node fixture contract can be checked separately with `node web/tests/simplification/fixture-contract.mjs`. That command is not a browser test. Its filename intentionally avoids Vitest's `.test.*` discovery.

## Cases and evidence

Each case uses a new browser context and fixture state. The runner continues after an individual failure and exits nonzero if any case fails.
Only the knowledge and experience cases enable the populated `materials` fixture; all other cases retain empty capture/reference collections, and `firstUse` remains empty even with that option.
The cases below are execution targets, not a record of passed runs. The added populated-list assertions and screenshots still require a real Chromium CI run on the final commit.

- First application with no AI, draft Back/Cancel, onboarding dismissal across reload and unrelated settings save, explicit reopen.
- Multiple provider drafts, testing the current unsaved draft, cancellation and late-response suppression, one settings save.
- Command destination deduplication, Escape, Enter, browser Back/Forward.
- Today's read-only candidate, email review, proactive unknown/draft results and historical reminder separation.
- Quick/review/question practice entry, source-mode separation, navigation return and narrow-screen overflow.
- External reference sources stay separate from confirmed notes and personal experience. Confirmed knowledge retains frozen evidence, edit cancellation, pending duplicate suppression and exact-identity uncertain retry.
- A nonempty confirmed interview capture with its complete source relation renders in personal experience, including the changed-source notice, rather than an empty/error placeholder. The localization fixture retains the valid empty `{ items: [] }` contract; the Node fixture check rejects its former bare-array response.
- Canonical experience owner across navigation, manual save and uncertain retry, real AppShell dirty/unknown safety reader via a synthetic preload interface. After retry, wait for the saved story row and verify its title, version, exact request content and evidence before `experience-owner-and-safety-saved-manual-story.png`; also retain `experience-owner-and-safety-confirmed-capture-list.png` and `knowledge-evidence-cancel-and-unknown-reference-source-boundary.png`.
- Diagnostics remain unqueried while hidden and stop polling after collapse or entering AI settings. These checks wait beyond the actual 15-second polling interval.

`report.json` records the commit/tree, worktree status, built-asset hashes, Chromium version, assertions, failures, console errors and coverage limits. Per-case request logs, Playwright traces and PNG screenshots are retained. Screenshots are actual browser pixels, not generated images. Failures include the visible DOM text.

## Boundaries

This is client-side acceptance against a strict synthetic API, not backend persistence or end-to-end real-service acceptance. The simulated preload interface exercises production safety aggregation but does not validate Electron IPC, native exit/update installation, or global coverage of unrelated editors. It does not cover every retained-owner domain, full model recovery, voice, permissions or actual mailbox behavior.

The local cloud executor used to prepare this harness cannot launch Chromium because AF_UNIX is denied; its supported escalation also fails during executor setup. Syntax and fixture contracts can be verified there, but only a successful browser run and its artifacts count as browser acceptance. Do not report the prepared cases as passed before that run.
