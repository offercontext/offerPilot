# Durable Execution Journal Release Verification

Date: 2026-08-18

Branch: `feat/20260817-durable-execution-journal`

Fixed baseline: `dd083a950b403bd96da02999413d4c2aa6233c50`

## Scope and allowlist

This phase adds a fail-open diagnostic execution journal around the existing Agent runtime. It does not make the Journal a business source of truth and does not change the existing Conversation, Pending Action, confirmation, Provider, SSE, or domain-write contracts.

The immutable positive allowlist contained these paths:

- `src/offerpilot/models.py`
- `src/offerpilot/db.py`
- `src/offerpilot/repositories/agent_runs.py`
- `src/offerpilot/agent_runtime/__init__.py`
- `src/offerpilot/agent_runtime/events.py`
- `src/offerpilot/agent_runtime/journal.py`
- `src/offerpilot/agent_runtime/keyring.py`
- `src/offerpilot/agent_runtime/trace.py`
- `src/offerpilot/api.py`
- `src/offerpilot/ai/agent.py`
- `tests/test_agent_run_migrations.py`
- `tests/test_agent_runs_repository.py`
- `tests/test_agent_run_journal.py`
- `tests/test_agent_run_keyring.py`
- `tests/test_agent_run_trace.py`
- `tests/test_ai_agent.py`
- `tests/test_chat_api.py`
- `tests/test_settings_api.py`
- `tests/test_smoke.py`
- `docs/reports/2026-08-17-durable-execution-journal-release-verification.md`

The pre-report scope assertion found zero changed paths outside this allowlist. A diagnostic cleanup change outside the allowlist was committed and immediately reverted; it has no net baseline diff. After the user separately authorized closing the real-AI cleanup blocker before merge, the final baseline diff contains exactly one separately scoped path outside the immutable Journal allowlist: `src/offerpilot/smoke.py`. The original allowlist remained unchanged.

## Delivered behavior

- Added the `AgentRun`, `AgentEvent`, and `AgentContextSnapshot` models and migration `0024_durable_execution_journal`.
- Added an independent, atomically created Journal HMAC key domain with bounded permission handling, backup exclusion, concurrent creation protection, and fail-open startup.
- Added strict canonical event and context contracts, complete stable-event digests, bounded manifests, source-reference validation, UUID constraints, and 4 KiB event / 16 KiB snapshot database limits.
- Added an atomic repository with monotonic sequence allocation, idempotent dedupe, conflict detection, snapshot/event atomicity, terminal immutability, waiting-run lookup, and bounded SQLite lock behavior.
- Added `SafeRunRecorder`, `NullRunRecorder`, a degraded latch, 150 ms segment budgets, 50 ms final-disposition convergence, and deterministic trace reconstruction with separate lifecycle, completion, stale, suspension, terminal, and integrity classifications.
- Instrumented model calls and tool-call facts without recording prompts, answers, credentials, job-description text, resume text, or arbitrary context strings.
- Connected ordinary JSON Chat, SSE Chat, deterministic actions, and confirmation resume to the same logical Run while preserving existing business CAS, fencing, and confirmation semantics.
- Added behavior-equivalence coverage for Journal enabled, disabled, degraded, and failed paths.

There are no public API, CLI, UI, SSE envelope, Provider, or business-domain schema changes. The only database change is the three diagnostic Journal tables and their indexes/constraints.

## Commits

- `655fd44 feat: AI add durable journal key domain`
- `d318b6e feat: AI add durable agent journal schema`
- `32426d6 feat: AI add durable journal event contracts`
- `f6a74b7 fix: AI harden durable journal privacy boundaries`
- `7154565 fix: AI align durable journal event contracts`
- `c0fb8a8 fix: AI close durable journal batch one review`
- `2934450 feat: AI add atomic durable journal repository`
- `2fbdbf6 fix: AI harden durable journal repository review`
- `a5126c9 feat: AI add safe journal recorder and trace`
- `3369877 fix: AI satisfy durable journal type gate`
- `493fd7c feat: AI instrument agent model and tool runs`
- `e28bab3 feat: AI journal chat execution lifecycle`
- `fec5faf feat: AI journal confirmation resume lifecycle`
- `125020e test: AI verify durable journal equivalence`
- `39947a6 fix: AI harden durable journal lifecycle convergence`
- `8df478f test: AI make journal key gate platform neutral`
- `c110892 fix: AI dispose durable journal smoke engine`
- `97d6c31 revert: AI remove out-of-scope smoke cleanup`
- `86c6cde fix: AI close durable journal smoke lifecycle`

The `c110892` / `97d6c31` pair is a net-zero diagnostic attempt retained in history for auditability. The separately authorized lifecycle fix was then implemented in `src/offerpilot/smoke.py` as a one-path exception to the immutable Journal allowlist, with its regressions added to the already allowlisted `tests/test_smoke.py`.

## Schema and migration verification

- Fresh database initialization created all three Journal tables and recorded migration `0024_durable_execution_journal`.
- Upgrade from a `0023` database recorded `0024` exactly once.
- Foreign-key cascade, rollback, unique constraints, waiting tool-call partial uniqueness, UUID checks, payload limits, and model-call constraints were exercised.
- Journal repository access uses its independent short-timeout SQLite Session/Pool while retaining the existing business database as the authoritative store.

## Focused privacy, repository, trace, and equivalence verification

- The focused acceptance matrix covering keyring, migration, repository, event/privacy contracts, trace, Agent instrumentation, Chat API, settings, and smoke behavior completed with `584 passed, 1 skipped` before the platform-neutral keyring gate adjustment.
- The adjusted keyring suite then completed with `12 passed` and no skip.
- Journal repository/recorder/trace suites completed with `99 passed`.
- Ten targeted lifecycle-convergence regressions passed after the final review fixes.
- `tests/test_smoke.py` completed with `58 passed` during cleanup diagnosis; the exploratory cleanup edit was subsequently reverted to preserve the immutable scope.
- After the separately authorized lifecycle fix, all four new shutdown/ownership regressions passed and the complete smoke suite finished with `61 passed`.
- Focused Ruff and Mypy checks passed, and `git diff --check` passed.

The privacy tests cover bounded canonical JSON, HMAC-only sensitive-source representation, manifest truncation/digests, key non-export, backup exclusion, rejection of arbitrary facts, and the event/snapshot byte boundaries. Equivalence tests compare business-visible responses and persisted business state across healthy, disabled, unavailable, and degraded Journal modes.

## Complete backend gate

A fresh duplicate-sensitive post-fix manifest contained `2310` unique test node IDs and no duplicates. The grouped aggregate covered the same `2310` tests:

| Group | Collected | Tests | Skipped | Failures | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| agent | 495 | 495 | 0 | 0 | 0 |
| domain | 73 | 73 | 0 | 0 | 0 |
| knowledge | 659 | 659 | 4 | 0 | 0 |
| proposals | 434 | 434 | 0 | 0 | 0 |
| misc | 649 | 649 | 0 | 0 | 0 |
| **Total** | **2310** | **2310** | **4** | **0** | **0** |

The four skips are the gate's fixed Windows symlink-permission cases:

- failed ingest cleanup does not follow a symlink;
- Knowledge reset rejects a symlinked root;
- legacy reset rejects a symlinked root;
- Knowledge reset does not follow a nested escaping symlink.

No Journal test remained skipped.

## Complete frontend gate

The frontend manifest contained `166` test files and fingerprinted `424` source/configuration files. Its manifest SHA-256 was `688778f3b0dd31216bb17362cbaf681a7b1543026163537819f96e4939985ab2`; its source SHA-256 was `103f5f777f33d3841ca5958ced8d45f6ceb5807b170d4d249d6498e651ba5617`.

| Group | Files | Tests |
| --- | ---: | ---: |
| components-core | 35 | 186 |
| components-chat | 14 | 195 |
| components-interview | 15 | 86 |
| components-offer | 8 | 45 |
| components-support | 8 | 32 |
| features | 42 | 321 |
| layout | 14 | 87 |
| lib | 14 | 199 |
| services | 15 | 67 |
| theme | 1 | 4 |
| **Total** | **166** | **1222** |

All `1222` tests passed. The aggregate contained no skipped, pending, or todo tests, and all ten groups shared the same manifest and source fingerprint.

## Static, build, and smoke gates

- `uv run ruff check .`: passed.
- `uv run mypy src`: passed for 83 source files.
- `npm.cmd run build`: passed; 3941 modules transformed.
- `uv run oc smoke --static-dir web/dist`: passed.
- `scripts/local-smoke.ps1 -Port 18766`: passed.
- `uv run oc verify --profile local --static-dir web/dist`: passed.
- `uv run oc verify --profile real-ai --static-dir web/dist`: passed in the newly authorized post-fix run. Interview preparation, material proposal, opportunity-fit review, interview review/capture, mock interview, Agent write confirmation, cleanup, and process shutdown all completed successfully.

The frontend build retained an existing large-chunk warning. Installing the exact locked frontend dependencies for the gate reported existing package-audit findings; dependency remediation was outside this allowlist.

## Independent review

Independent review examined the baseline-to-branch diff, all allowlisted product/test files, and the special privacy, atomicity, timeout, confirmation, SSE identity, and equivalence targets.

Issues found and resolved included:

- normalized the tool-shape digest format;
- prevented stale key locks from being removed unless owner death was established;
- added database UUID constraints for model-call references;
- hardened key-domain consistency and idempotent disposition convergence;
- separated historical waiting events from the current trace lifecycle;
- made stale/CAS-losing confirmation segments abandon their own segment without terminalizing the shared Run;
- converged delayed confirmation success and failure after request timeout;
- preserved terminal winners while closing replay/no-op segments.

After fixes, the reviewer reported no remaining P0, P1, P2, or P3 findings. A final independent review of the separately scoped smoke lifecycle fix found no P0-P3 issue after shutdown waiting was bounded, live-server database disposal was prohibited, caller cleanup ownership was made explicit, and both business and Journal engines were disposed only after worker exit.

## External/transient failures encountered

- One proposals-group attempt saw a dedicated Chromium process exit before readiness. The exact isolated test passed, and the full proposals group then passed all 431 tests.
- The initial frontend command could not start before locked dependencies were installed. `npm ci` restored the declared environment; all frontend groups and the build then passed.
- The first real-AI attempt encountered a Windows temporary-database handle during cleanup and was not retried under its original one-run authorization. The subsequent separately authorized lifecycle fix and post-fix real-AI run closed this issue successfully.

## Breaking changes and remaining risks

There are no intended breaking changes to public or business behavior. The migration adds diagnostic tables to existing databases; removing the feature would require deciding separately whether to retain or delete those diagnostic rows.

Remaining risks:

- Existing frontend dependency audit findings and the large production bundle warning remain outside this phase.
- The Journal intentionally remains fail-open diagnostic data. It does not provide business exactly-once, event sourcing, SSE replay, UI inspection, ToolSpec, Write Operation Ledger, or Context Projector behavior.

## Verdict

Tasks 6-10 satisfy their scoped acceptance criteria. Complete backend, frontend, static, build, local smoke, local verification, and the newly authorized real-AI verification passed. Release acceptance is **fully green** for the Durable Run Foundation scope.
