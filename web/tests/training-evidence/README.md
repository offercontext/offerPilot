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
original command, source suspension and withdrawal. Output includes source/tree
and production-asset hashes, browser version, request/assertion logs, DOM,
accessibility snapshots and PNG screenshots. A passing report proves these UI
flows against the synthetic transport only, not backend persistence, model
quality, complete product coverage or installed Electron acceptance.
