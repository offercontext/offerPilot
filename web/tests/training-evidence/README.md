# Training evidence browser acceptance

Real Chromium renders the actual `web/dist` application. All page requests are
intercepted and fulfilled from local build files or a strict synthetic API;
unknown writes, unknown reads, credentials, external origins and WebSockets fail
closed. No backend, real user data, provider, model or mailbox is used.

```sh
node --test web/tests/training-evidence/fixture-contract.mjs
npm run build --prefix web
cd web && npx --no-install playwright-core install --with-deps chromium && cd ..
node web/tests/training-evidence/run.mjs artifacts/training-evidence-browser
```

The dedicated workflow admits only same-repository PR #66 from
`feat/20261109-interview-training-memory` (or explicit dispatch on that branch).
It does not change the existing full-gate routing.

The bilingual browser flow checks explicit selection, unknown historical
assistance, recording, correction, another scenario, lost-response retry with the
original command with the frozen structured choice, opposite-option feedback with
identical free text, six distinct scenarios, pool exhaustion and explicit retest
selection, filling all 12 active slots through the UI, withdrawing and reusing
capacity, source suspension and withdrawal. Output includes source/tree
and production-asset hashes, browser version, request/assertion logs, DOM,
accessibility snapshots and PNG screenshots. A passing report proves these UI
flows against the synthetic transport only, not backend persistence, model
quality, complete product coverage or installed Electron acceptance.

Capacity is 12 active (not withdrawn) records per plan. Withdrawal clears the
training answer and releases one active slot, while retaining its tombstone and
idempotency receipt so a retried old command cannot resurrect withdrawn content.
Corrections remain available at capacity. The original completed plan answer is
unchanged; a duplicate plan for the same signal/target is not a capacity workaround.

The six-question snapshot in `questions.json` is generated from the backend's
`training_evidence/scenarios.py` (`SCENARIOS`, `POOL_IDS`, `RULES`, `GUIDANCE`).
Its source hash is checked by the fixture contract; regenerate it when that
versioned content changes. Withdrawn scenario identities remain in pool coverage.
Only explicit option IDs are checked against the versioned rule. Free text is
always `not_evaluated`; missing/uncertain choices abstain. This is not a learning
improvement or free-text understanding claim.

Screenshots target the relevant feedback record, status, alert or control rather
than the full training panel. Each target is scrolled to the viewport center;
its viewport screenshot and visible target crop are named by phase and purpose.
The report records target text and geometry. A capture fails if less than 95% of
the target fits through its ancestor scrollports. This prevents a tall panel
clipped by the app shell from being presented as a complete evidence screenshot.
Failure diagnostics still preserve page HTML, accessibility text and the current
viewport independently of whether the intended target remains visible.
