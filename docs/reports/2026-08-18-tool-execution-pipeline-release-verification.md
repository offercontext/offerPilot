# Tool Execution Pipeline Release Verification

Date: 2026-08-19

Branch: `feat/20260818-tool-execution-pipeline`

Fixed baseline: `30c944f3bda1d99b303f8e9875a170a552f79af7`

## Scope and verdict

This phase performs an intentionally destructive internal cutover from the model-visible dictionary registry and string-handler protocol to one Typed Catalog and two-stage Tool Execution Pipeline. The Provider, HTTP/SSE, HITL, Pending Action, CAS, business-write, and user-visible string contracts remain compatible with the fixed baseline.

The final pre-report scope assertion found `61` changed paths and zero paths outside the immutable allowlist. The allowlist is:

- `pyproject.toml`
- `uv.lock`
- `src/offerpilot/ai/agent.py`
- `src/offerpilot/ai/client.py`
- `src/offerpilot/ai/types.py`
- `src/offerpilot/ai/tools.py`
- `src/offerpilot/ai/tool_runtime/`
- `src/offerpilot/ai/tool_specs/`
- `src/offerpilot/api.py`
- `src/offerpilot/db.py`
- `src/offerpilot/models.py`
- `src/offerpilot/repositories/chat.py`
- `src/offerpilot/smoke.py`
- `tests/tool_pipeline/`
- `tests/fixtures/tool_pipeline/`
- `tests/test_ai_tools.py`
- `tests/test_ai_agent.py`
- `tests/test_ai_client.py`
- `tests/test_chat_api.py`
- `tests/test_chat_repository.py`
- `tests/test_knowledge_sources_api.py`
- `tests/test_litellm_client.py`
- `docs/superpowers/specs/2026-08-18-tool-execution-pipeline-design.md`
- `docs/superpowers/plans/2026-08-18-tool-execution-pipeline.md`
- `docs/reports/2026-08-18-tool-execution-pipeline-release-verification.md`

Release acceptance is green for the approved Phase 2 boundary. No code was pushed or merged.

## Delivered behavior

- Added `ProviderToolContract`, typed Args/result/Outcome/failure contracts, a closed generic `ToolCatalog`, and a composition-root Catalog containing exactly the 25 model-visible tools.
- Added strict JSON parsing and Draft 2020-12 Schema validation through exactly pinned `jsonschema==4.26.0`: top-level object only, duplicate-key and non-finite-number rejection, initialization-time `check_schema`, local-only `$ref`/`$dynamicRef`, and retrieval-disabled registries.
- Added capability short-circuiting, request-scoped execution context, and audit-only binding results with the fixed `matched` / `mismatched` / `unbound` / `unavailable` aggregation.
- Added the two-stage `prepare_call()` / `execute_prepared()` pipeline. Read-only preflight is side-effect free; mutable checks, confirmation claim/CAS, execution authorization, argument-digest matching, `tool.started`, and the single executor call occur in the approved order.
- Added typed compatibility rendering and separate typed HTTP/SSE and Journal projection. Renderer, transport, and Journal failures cannot change the ToolOutcome or repeat execution.
- Preserved the first-phase Journal schema and stage semantics: pre-executor failures do not fabricate started/terminal events, while an invoked executor produces `tool.started` followed by exactly one completed/failed projection.
- Added a persisted private confirmation claim with a bounded 15-minute lease, atomic single-winner stale-claim recovery, explicit empty-claim rejection, and idempotent upgrade from migration `0024` through the unreleased `0025` migration.
- Kept transient `ToolExecutionRecord`, `ToolOutcome`, runtime context, Catalog, binding/capability audit, and exceptions out of LangGraph checkpoint state.
- Preserved the exact multi-call rule in sync and stream: all-read calls execute in order; if any selected call is a write, only the original first call is processed.
- Deleted `src/offerpilot/ai/tools.py`, the old registry factories, model-visible legacy handlers, implicit fallback, shadow execution, dual execution, and string-status parsing from production dispatch.

## Exact 25/3 boundary

The Provider-visible Typed Catalog contains exactly these 25 tools, in this order:

1. `list_applications`
2. `get_application`
3. `create_application`
4. `update_application_status`
5. `list_application_events`
6. `get_application_event`
7. `create_application_event`
8. `update_application_event`
9. `delete_application_event`
10. `list_notes`
11. `add_note`
12. `update_note`
13. `delete_note`
14. `list_offers`
15. `get_offer`
16. `compare_offers`
17. `update_offer`
18. `save_offer_assessment`
19. `list_resumes`
20. `get_resume`
21. `resume_update_career_intent`
22. `resume_rewrite_highlight`
23. `list_resume_matches`
24. `list_jd_analyses`
25. `get_jd_analysis`

The only remaining Legacy Adapters are these three server-created deterministic actions:

- `save_application_jd_version`
- `create_application_submission_snapshot`
- `record_application_outcome`

They are absent from the Provider payload and model dispatcher. Confirmation resume loads the trusted server Pending Action before resolving one of these closed legacy names; a client-supplied name cannot route into Legacy.

## Compatibility goldens

The immutable synthetic golden assets were captured independently from baseline `30c944f` and are read-only in tests. Tests never regenerate, overwrite, or accept a changed asset.

| Asset | Coverage | SHA-256 |
| --- | --- | --- |
| `provider_manifest_30c944f.json` | 25 complete Provider envelopes and 25 Schema fingerprints | `46930fbeb2713e528b1cdfc8aeea58557bc2ad37989253975c9c03fb9789c3ee` |
| `tool_outcomes_30c944f.json` | 67 success/failure visible-result and side-effect cases | `6054aa8ca654cb89be8aa4e6d7466e3659662e5ebe7930f06c9216f3ef2ce620` |
| `journal_sequences_30c944f.json` | 6 first-phase Journal sequence cases | `6478042d87016d9922cfe7d7fe7e67a8cdfd5c6a3de36f046190ff6d31bb30fa` |

Canonical comparison fixes UTF-8, sorted keys, compact separators, non-finite rejection, and no Unicode normalization. Provider boundary tests spy on the complete payload sent to the adapter without network access and verify the final list, order, envelope fields, descriptions, and parameter Schemas.

The assets contain only synthetic canonical JSON. They contain no SQLite database, user content, credential, key, prompt, answer, resume/JD source text, or unstable timestamp.

## Commits

- `10656d9 docs: AI add tool execution pipeline design`
- `438e8a1 docs: AI resolve tool pipeline design review`
- `cffa290 docs: AI approve tool pipeline design`
- `edda840 docs: AI add tool execution pipeline plan`
- `a724910 test: AI freeze tool pipeline compatibility goldens`
- `c8ff063 feat: AI add typed tool runtime contracts`
- `5a44d95 feat: AI add tool capability and binding audit`
- `df36a04 feat: AI add typed tool execution pipeline`
- `c971141 feat: AI migrate application tool specs`
- `4fd1b29 feat: AI migrate note and offer tool specs`
- `8ae93fe feat: AI migrate resume and JD tool specs`
- `8ad9b55 feat: AI assemble typed tool catalog`
- `96499c7 refactor: AI isolate deterministic legacy tools`
- `5223088 refactor: AI cut over typed tool execution pipeline`
- `c53ec75 test: AI enforce typed pipeline cutover`
- `02e9689 docs: AI include smoke model in pipeline scope`
- `cec34dd test: AI stabilize typed pipeline release gate`
- `ecfb6b3 fix: AI close typed pipeline review findings`
- `2701b73 docs: AI align tool pipeline scope evidence`
- `5174804 test: AI stabilize confirmation timeout gate`
- `7c29725 fix: AI harden typed confirmation boundaries`
- `642e911 test: AI await deterministic journal convergence`
- `11eb3ff fix: AI recover stale confirmation claims`
- `b2ea27f test: AI stabilize journal convergence gate`
- `f08ff37 test: AI isolate journal timing assertions`

## Focused verification

- `tests/tool_pipeline`: `156 passed`.
- Confirmation claim/migration/repository focus: `23 passed`.
- Agent focus: `108 passed`.
- Complete `tests/test_chat_api.py`: `297 passed`.
- Golden, Schema, Catalog, capability, binding, prepare/execute, projector, checkpoint, legacy-isolation, source/AST, and HTTP/SSE matrices all passed.
- The source gates prove the old dictionary registry/handler protocol is deleted, the dispatcher cannot reference Legacy, compatibility string inspection is confined to its exact allowlist, no hidden feature flag/shadow/dual/fallback path exists, and `jsonschema==4.26.0` is fixed in both project and lock files.

## Complete backend gate

A fresh post-fix collection contained `2468` unique node IDs and zero duplicate node IDs. The grouped aggregate covered the same `2468` tests:

| Group | Collected | Tests | Skipped | Failures | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| agent | 497 | 497 | 0 | 0 | 0 |
| domain | 131 | 131 | 0 | 0 | 0 |
| knowledge | 659 | 659 | 4 | 0 | 0 |
| proposals | 434 | 434 | 0 | 0 | 0 |
| misc | 747 | 747 | 0 | 0 | 0 |
| **Total** | **2468** | **2468** | **4** | **0** | **0** |

The four skips are the fixed Windows symlink-permission infrastructure cases. No Tool Pipeline test is skipped. The misc group used a mechanically expanded copy of the prior Application-JD allowlist as external gate evidence; the immutable Phase 2 allowlist itself was not changed.

## Complete frontend gate

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

All `1222` frontend tests passed. The aggregate reported no failed, skipped, pending, or todo test. The exact aggregate test-ID SHA-256 was `7575ee7cf021567cf424ef3dc3145ce9b6943791e529519f641bee6c8f2a4c9f`.

## Static, build, smoke, and controlled AI gates

- `uv run ruff check .`: passed.
- `uv run mypy src`: passed for 102 source files.
- `npm run build`: passed.
- `uv run oc smoke --static-dir web/dist`: passed.
- `scripts/local-smoke.ps1 -Port 18766`: passed.
- `uv run oc verify --profile local --static-dir web/dist`: passed.
- `uv run oc verify --profile real-ai --static-dir web/dist`: passed on its single authorized run, including interview preparation, material proposal, fit review, mock-interview, Chat write confirmation, Pending Action clearing, and process cleanup.

The build retained the existing approximately 1.5 MiB large-chunk warning. Installing the locked frontend dependencies with `npm ci` reported 13 existing audit findings: 3 moderate, 7 high, and 3 critical. Dependency and bundle remediation are outside this phase.

## Local browser Chat acceptance

One isolated real local-browser session exercised the approved closures through the in-app Browser:

1. One model response invoked `list_applications` followed by `list_offers`; the UI reported two completed read steps in order with no write.
2. A streamed `create_application` paused for confirmation and created synthetic application Alpha only after approval.
3. A second create was edited from the proposed synthetic role to `Edited Role`; the effective approved Args reached the executor and database.
4. A third create was rejected. It produced no `tool.started`/completed event and no database row.
5. Streamed read and streamed write-confirmation paths both completed.

Network inspection observed `200 text/event-stream` for both `/api/chat/stream` and `/api/chat/confirm/stream`. The final isolated database contained exactly the two approved synthetic applications, the edited role/status, no rejected application, and an empty Pending tool/claim state. The six Agent Runs were terminal.

The write runs retained the structurally valid `proposed -> approval.requested -> waiting -> approval.decided -> resumed -> started -> completed -> run.completed` progression when their fail-open recorder retained all facts. The rejected run had no false started/completed events.

All six real-provider browser runs latched first-phase `recording_status=degraded`; their reconstructed traces were terminal but carried `model_call_incomplete` plus `recording_degraded`. Read-run tool events could therefore be omitted after the first-phase 150 ms fail-open segment budget elapsed across provider latency. This is the already-shipped Durable Journal fail-open contract, not a Phase 2 execution-path failure. Stable-recorder integration and golden tests prove the complete Phase 2 Journal projection independently, while the real session proves that Journal degradation does not alter Provider, Tool, HITL, transport, or business outcomes.

The isolated browser database and copied local configuration were removed after acceptance. The retained report and gate artifacts contain no credential or raw user data.

## Independent review

Independent review examined the complete baseline-to-branch diff and all mandatory Provider, Schema, capability/binding, prepare/execute, confirmation, checkpoint, projector, Journal, HTTP/SSE, HITL, CAS, and source-deletion targets.

Review findings resolved during implementation included confirmation-claim authorization boundary hardening, explicit empty-claim rejection, stale claim recovery, unreleased migration upgrade/idempotency, Journal convergence timing, and keeping precise projection tests independent of the production fail-open timing budget.

The final independent review reported no remaining P0, P1, or P2 finding. It specifically confirmed that the last test-only convergence commits did not mask a production issue, that the 15-minute lease and atomic single-winner recovery are bounded, that `0025` upgrades old `0024` databases idempotently, and that the 25/3 boundary and Provider payload remain unchanged.

The reviewer classified the real-provider Journal degradation as the first-phase fail-open behavior rather than a Phase 2 severity finding.

## Breaking changes

Internal APIs are deliberately breaking:

- the model-visible dictionary registry and string-handler protocol are gone;
- Provider builders accept only `ProviderToolContract` sequences;
- Agent/API callers consume typed records and outcomes;
- the 25 model-visible executors exist only behind ToolSpecs and the Pipeline;
- old tests and imports targeting `offerpilot.ai.tools` were removed or migrated.

No intended breaking change exists in Provider-visible envelopes, tool name/order/description/Schema, user-visible result text, HTTP/SSE payloads, HITL/Pending Action behavior, CAS, call counts, or domain side effects.

## Remaining risks and explicit non-goals

- A single `execute_prepared()` invokes an executor at most once. Phase 2 does not promise cross-request or cross-process exactly-once. A process that runs an executor longer than the 15-minute confirmation-claim lease can overlap a later stale-claim recovery. Global write idempotency belongs to Phase 3 Write Operation Ledger.
- The Durable Journal remains fail-open diagnostic data. Real-provider latency can latch degradation and omit later diagnostic facts without blocking business behavior, as observed in browser acceptance.
- The three deterministic Legacy Adapters remain intentionally isolated for a later migration.
- Binding mismatches/unbound/unavailable results are audit-only in compatibility mode; existing API/Repository ownership checks remain authoritative. Stricter ID pre-binding and entity isolation are deferred.
- Existing frontend dependency audit findings and the large-bundle warning remain unresolved outside the allowlist.
- This phase does not implement Write Operation Ledger, Context Projector, SSE replay, UI diagnostics, multi-Agent behavior, or a new user-visible error protocol.

## Final verdict

The destructive internal cutover is complete for all 25 model-visible tools; exactly three trusted deterministic actions remain behind explicit Legacy Adapters. Provider/tool/transport/business compatibility, confirmation safety, deletion gates, complete backend/frontend gates, static/build/smoke/local/real-AI verification, browser Chat closure, and independent review all passed. Phase 2 Tool Execution Pipeline is complete within its approved boundary.
