# Context Projector release verification

Date: 2026-08-20  
Branch: `feat/20260819-context-projector`  
Implementation commit: `5b683cc`

## Scope

This report records the pre-merge acceptance evidence for Phase 4 Context Projector. It covers the deterministic model surface, source snapshot loader, history and budget selection, typed tool surface binding, Provider response provenance, Manifest V2, provider-free rejection, confirmation continuation, delivery fencing, controlled real-Provider execution, and the browser HITL loop.

## Final code review

The final independent review was explicitly expanded to P0, P1, and P2. The review covered fail-closed projection, provider-zero paths, source snapshot isolation, tool-surface binding, heartbeat/delivery fencing, Manifest V2 validation, complete-message history limits, and the Windows SHA-256 portability change.

The review found three P2 validation issues: non-string `tools`/`signals` members could raise a raw `TypeError`, non-string contributor status had the same failure mode, and the 1 MiB history-message boundary counted only `content` rather than the complete canonical message. All three were fixed with red/green regression tests covering the direct validator and shared journal entrypoint, tool-call arguments, and Provider blocks. The final follow-up review found no remaining P0, P1, or P2 findings.

## Environment gates

### Windows SHA-256 gate: closed

The grouped Windows scripts failed only in a fresh `powershell.exe -NoProfile` subprocess. The inherited `PSModulePath` caused module-name resolution to select an incompatible `Microsoft.PowerShell.Utility` module before the Windows inbox module, so `Get-FileHash` was unavailable even though an already initialized interactive shell exposed it.

The scripts now compute file SHA-256 with `System.Security.Cryptography.SHA256` and a bounded file stream, removing the module auto-loading dependency. Fresh verification:

- `tests/test_frontend_vitest_groups.py`
- `tests/test_windows_pytest_groups.py`
- `tests/test_interview_story_browser_harness.py`
- Result: `36 passed`.

### Application JD baseline gate: explicitly accepted external gate

`test_application_jd_implementation_scope_is_machine_checked` requires two release-orchestrator artifacts: `OFFERPILOT_APPLICATION_JD_BASELINE_FILE` and `OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE`. Neither artifact is present in this Context Projector worktree or current process. Generating an allowlist from the current diff would invalidate the gate's independent scope-control purpose, so no synthetic baseline or allowlist was created.

Acceptance decision for this branch: this external, Application-JD-specific scope gate is accepted as an unresolved release-orchestrator prerequisite rather than represented as passed. The prior Application JD release evidence remains in `docs/reports/2026-08-05-application-jd-versions-release-verification.md`. A release operator may rerun the gate with the recorded external baseline and allowlist before integration if repository policy requires a current aggregate run.

## Real-Provider verification

Command:

```powershell
uv run oc verify --profile real-ai --static-dir web/dist
```

Configuration was read from the existing local OfferPilot profile without printing or modifying its secret. Safe route summary: `openai_compatible`, model `deepseek-v4-flash`, endpoint host `api.deepseek.com`.

Result: passed. The gate completed health/settings/SPA checks, application/resume/event CRUD, Interview Preparation, Material Proposal, Opportunity Fit triage and deep review, Interview Review, Knowledge Capture, bounded Mock Interview, Chat write confirmation, pending cleanup, and final application cleanup.

## Browser acceptance

The built SPA was served from the isolated data directory:

`D:\Users\yuqi.chen\.offerpilot\verification\context-projector-20260820\browser-data`

The in-app browser completed these real UI journeys against `http://127.0.0.1:8765`:

1. Loaded the dashboard and applications board.
2. Created `Context QA / Projector Engineer` through the application form.
3. Opened workspace Pilot and queried the application; the response used the typed application read tools and returned the stored status and notes.
4. Requested a status change to `interview`, observed the HITL card, completed the two-step rejection UI, and observed the deterministic provider-free cancellation response.
5. Repeated the same write request, approved it, observed `保存成功`, and waited until the Pending card disappeared.
6. Opened the application detail and its Pilot drawer, verified `Context QA · Projector Engineer` as `context_type=application`, sent an application-scoped question, and received the persisted `interview` status.

Read-only database verification after the browser flow:

- Application status: `interview`.
- Conversations: one workspace conversation and one application-scoped conversation with `context_ref=1`.
- Pending actions: zero.
- Agent runs: four completed.
- Context snapshots: five V1 and four V2 snapshots.

Screenshot evidence:

`D:\Users\yuqi.chen\.offerpilot\verification\context-projector-20260820\browser-application-scoped.png`

The browser data and screenshot are isolated from the user's normal OfferPilot database. No formal Provider configuration or secret was changed.

## Verification summary

- `uv run ruff check --no-cache .`: passed before the acceptance-only portability change; rerun required before the final acceptance commit.
- `uv run mypy src`: passed before the acceptance-only portability change; source typing was unaffected, but the final gate will rerun it.
- Context Projector, Agent, Journal focused suite: `230 passed`.
- Chat API suite: `296 passed`.
- Frontend Vitest: `1,222 passed`; production build passed.
- Static smoke: passed.
- Comprehensive backend run before the portability fix: `2,465 passed`, `4 skipped`, with only four smoke regressions; those four were fixed and rerun `4 passed`.
- Windows hashing and browser-harness regression suite after the portability fix: `36 passed`.
- Final Windows/Context Projector acceptance suite after CR fixes: `74 passed`.
- Real-AI verification: passed.
- Browser real-AI/HITL/application-scope walkthrough: passed.

## Breaking changes and residual risk

- Model-visible history and tools are now deterministically projected; response wording and tool choice can differ from pre-Phase-4 behavior.
- Rejection is provider-free and returns a deterministic cancellation result.
- Manifest V2 is additive; V1 rows remain readable and are not backfilled.
- The Application JD baseline/allowlist aggregate gate remains an explicitly accepted external release prerequisite, not a passing local result.
- The browser evidence uses isolated local data but a real configured Provider; it is acceptance evidence, not a deterministic unit test.

No push or merge is authorized by this report.
