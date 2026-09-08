# Offer Comparison and Negotiation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Extend the existing Offer center with auditable comparison dimensions and a single-Offer negotiation-preparation flow while preserving user decision authority, strict evidence gating, and the existing Chat context contract.

**Architecture:** Add a small Offer-domain persistence layer for workspace comparison dimensions, per-Offer values, immutable negotiation Attempts/Proposals, and one-confirmation Briefs. The Offer center and Pilot call the same API and repository paths; Pilot only supplies an explicitly selected Offer context and never owns a second state machine. Existing Offer CRUD and comparison remain the source of current facts; historical Proposal/Brief rows retain ordinary IDs and frozen snapshots so Offer edits or deletion cannot rewrite history.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, Pydantic, existing provider client/JSON-contract helpers, React, TypeScript, Ant Design, TanStack Query, Vitest, pytest, PowerShell harnesses.

---

## Scope and fixed contracts

The implementation starts from main@14ec28b in D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260801-offer-negotiation after rebasing onto the current origin/main. The Application Journey task is abandoned and must not be edited. The root worktree's uncommitted tests/test_smoke.py is outside this task and must not be touched.

The following contracts are fixed before implementation:

1. A single visible Offer can start negotiation preparation without comparison. Multiple comparison requires at least two distinct visible IDs and preserves the submitted order.
2. Comparison has facts, user notes, custom dimensions, values, and missing-information markers only. There is no score, ranking, weight, average, best/optimal label, positive/negative color, or accept/decline recommendation.
3. Current comparison values may be edited or archived. Confirmed Briefs and Proposals retain frozen source data and ordinary offer_id/application_id integers; current physical Offer deletion never cascades into historical rows. There is no existing Offer soft-delete state and this task does not introduce one.
4. Generation input is exactly the selected Offer facts, the explicitly selected active comparison dimension IDs and their server-read values, and the user's current goal/concerns/scenario. It excludes full Chat history, other Offers, external sources, URLs, provider internals, and unrelated application data.
5. The Proposal JSON has exactly proposal_status, communication_goals, clarification_questions, talking_points, and preparation_checks. Normal items have exactly id, text, rationale, and evidence_refs; each evidence reference has exactly source, path, and excerpt. Evidence sources are offer_snapshot or user_brief. Every clarification question must have at least one validated evidence reference; there is no context-free question exemption or allowlist.
   The generation request has exactly `idempotency_key`, `dimension_ids`, `goal`, `concerns`, and `scenario`; `dimension_ids` is a user-selected, unique, positive array of 0 to 8 active dimension IDs. The server rejects archived, missing, duplicate, or ninth dimensions with `422` before creating an Attempt. The canonical stored snapshot is:

~~~json
{
  "snapshot_version": 1,
  "offer_snapshot": {
    "company_name": "...",
    "position_name": "...",
    "status": "...",
    "base_monthly": 0,
    "months_per_year": 12,
    "signing_bonus": 0,
    "equity": "...",
    "perks": "...",
    "deadline": "...",
    "notes": "...",
    "dimensions": [
      {"path_id": "dimension_001", "label": "通勤", "value_text": "地铁 35 分钟"},
      {"path_id": "dimension_002", "label": "成长空间", "value_text": null}
    ]
  },
  "user_brief": {"goal": "...", "concerns": "...", "scenario": "..."}
}
~~~

The server constructs this snapshot from current rows, trims only for blank validation, preserves all stored/user text exactly, and assigns `dimension_001` onward after sorting selected dimensions by numeric dimension ID. A selected dimension with no current value is stored as `value_text: null`; it is displayed as missing information and is not converted to “无” or “未知”. The canonical JSON has fixed object/field order and compact separators. Therefore the same selected set in a different request order produces identical snapshot JSON and source fingerprint. The Provider projection omits `value_text` for missing dimensions and marks them only as missing; it receives no evidence path for that missing value. Provider evidence paths are exactly `/offer_snapshot/<fixed_field>`, `/offer_snapshot/dimensions/<path_id>/value_text` only when that dimension has a non-null value, and `/user_brief/<goal|concerns|scenario>`. There is no `/offer_snapshot/dimensions/<path_id>/label` evidence path and no path for a missing marker. The production validator accepts only these paths, and every excerpt must be a nonblank exact contiguous substring of the referenced value.
6. The terminal matrix is explicit: 422 invalid input creates no Attempt and clears the client draft; no available nonblank Offer/user evidence creates a locally validated safe-empty Proposal with 201 and no Provider call; a pure JSON/shape failure gets at most one repair, and a second pure shape failure creates the fixed safe-empty Proposal with 201, ready status, and same-key 200 replay; validated semantic failures (unknown source, invalid path, excerpt mismatch, over-limit, duplicate semantic IDs, forbidden decision language) create no Proposal, mark the Attempt invalidated with its failure reason, return stable 502 offer_negotiation_unverifiable, and clear the definite client key; Provider/network/timeout/response-loss/bare-5xx failures mark provider_unknown, return 502 offer_negotiation_provider_error, and retain the Attempt, key, and frozen input for same-key retry. A 202 remains generating/provider_unknown only. 409 means snapshot/idempotency/source conflict and never overwrites history.
7. Only explicit user confirmation creates one Brief. Confirmation is unique by Proposal, atomic, replayable, and never creates a Chat message, reminder, material, question, knowledge asset, application mutation, or external action.
8. Chat remains context_type=application plus context_ref=<application_id> for an associated Offer, or workspace context otherwise. No offer_id field is added to conversation persistence.
9. The existing Offer-card “谈薪教练” action remains available and unchanged: it continues to open the current Chat coaching behavior and does not create a negotiation Proposal or Brief. The new “开始谈薪准备” action is a separate, explicitly confirmed Proposal/Brief flow. Neither action replaces, silently redirects, or changes the other.

## File map

Create or modify only these functional areas unless a focused test proves an existing shared helper must be extended:

- Backend models and migration: src/offerpilot/models.py, src/offerpilot/db.py.
- Backend schemas/routes/repositories: src/offerpilot/schemas.py, src/offerpilot/api.py, src/offerpilot/repositories/offer_comparison.py, src/offerpilot/repositories/offer_negotiation.py.
- AI contract: src/offerpilot/ai/offer_negotiation.py and the smallest existing JSON-contract/provider helper required for shared parsing.
- Backend tests: tests/test_offer_comparison_dimensions.py, tests/test_offer_negotiation_api.py, tests/test_offer_negotiation_repository.py, tests/test_offer_negotiation_ai.py, tests/test_offer_negotiation_migration.py.
- Frontend contract/service: web/src/types/offer.ts, web/src/services/offers.ts.
- Frontend Offer center: web/src/components/OfferCenterView.tsx, web/src/components/OfferCard.tsx, web/src/components/OfferCompareDrawer.tsx, new web/src/components/OfferComparisonDimensions.tsx, new web/src/components/OfferNegotiationDrawer.tsx.
- Frontend Pilot integration: existing Pilot attachment/context files under web/src/features/pilot/, web/src/components/ChatPanel/, and web/src/layout/AppShell.tsx; do not add a Chat database field.
- Isolated verification: scripts/offer-negotiation-real-ai-browser-harness.ps1, tests/test_offer_negotiation_browser_harness.py, and existing smoke/verify entry points only where a task explicitly adds scoped coverage.

Every task below is test-first: write the named failing tests, run them to establish the failure, implement the smallest change, rerun the focused command, and make the listed small commit. Do not batch unrelated tasks into one commit.

## Task 1: Establish migration and source-of-truth safety

**Files:**
- Create: tests/test_offer_negotiation_migration.py
- Modify: src/offerpilot/models.py, src/offerpilot/db.py

- [ ] Step 1: Add migration contract tests before model changes. Cover a fresh database and a real pre-feature SQLite schema with the current Offer table plus no negotiation tables. Assert startup creates migration 0017_offer_comparison_negotiation, all four new tables and unique indexes exist, and running init_database twice does not duplicate or alter rows. Insert an Offer and verify its current fields are byte-equivalent after migration.
- [ ] Step 2: Run uv run pytest tests/test_offer_negotiation_migration.py -q; it must fail because the feature tables and migration marker do not exist.
- [ ] Step 3: Add OfferComparisonDimension, OfferComparisonValue, OfferNegotiationProposal, and OfferNegotiationBrief. Use these exact fields:
  - Dimension: id, nonblank label, nullable archived_at, created_at, updated_at.
  - Value: id, offer_id, dimension_id, value_text, created_at, updated_at, unique (offer_id, dimension_id).
  - Proposal/Attempt: ordinary immutable offer_id and nullable application_id, ASCII idempotency_key, attempt_status, source_fingerprint, input_snapshot_json, nullable proposal_json and proposal_hash, source_states_json, nullable lease_token, lease_expires_at, revision, invalidation_reason, created_at, ready_at; unique (offer_id, idempotency_key). Invalidated semantic Attempts retain only the frozen input, status, reason, and diagnostics; they do not contain a Proposal body or hash.
  - Brief: proposal_id unique, ordinary immutable offer_id, nullable origin_application_id, selected_blocks_json, edited_content_json, confirmed_at.
  Current-value rows may be cleaned with an Offer, but no historical table may use an FK that cascades or blocks Offer deletion.
- [ ] Step 4: Add an explicit 0017_offer_comparison_negotiation migration helper after Base.metadata.create_all. It must create missing tables/indexes for an old database, use INSERT OR IGNORE for the migration record, and leave existing Offer bytes and migration records unchanged. Run the fresh/old/repeated-startup tests until all pass.
- [ ] Step 5: Commit:
~~~powershell
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_offer_negotiation_migration.py
git commit -m "feat: AI add offer negotiation schema"
~~~

## Task 2: Implement custom comparison dimensions and values

**Files:**
- Create: src/offerpilot/repositories/offer_comparison.py, tests/test_offer_comparison_dimensions.py
- Modify: src/offerpilot/schemas.py, src/offerpilot/api.py, web/src/types/offer.ts, web/src/services/offers.ts

- [ ] Step 1: Add failing repository/API tests for nonblank labels, archive behavior, active-only listing, old archived values remaining readable, one value per Offer/dimension, exact user text preservation, missing/physically deleted Offer rejection, and stable Chinese 422 for blank labels/values. Assert no score/rating fields exist.
- [ ] Step 2: Run uv run pytest tests/test_offer_comparison_dimensions.py -q; it must fail before the endpoints/repository exist.
- [ ] Step 3: Add repository functions for visible Offer lookup, active dimension listing, value upsert, archive, and current comparison payload assembly. Keep the existing GET /api/offers/compare response as Offer[] for compatibility. Add these exact dimension routes:
  - GET /api/offers/comparison-dimensions
  - POST /api/offers/comparison-dimensions with {label}
  - PATCH /api/offers/comparison-dimensions/{dimension_id} with {label?, archived?}
  - GET /api/offers/{offer_id}/comparison-values
  - PUT /api/offers/{offer_id}/comparison-values/{dimension_id} with {value_text} for a nonblank value
  - DELETE /api/offers/{offer_id}/comparison-values/{dimension_id} to remove the current value idempotently
  Register GET /api/offers/comparison before the dynamic GET /api/offers/{offer_id} route. Add a separate structured read endpoint, GET /api/offers/comparison?ids=1,2&dimension_ids=3,4, with this fixed response shape:

~~~json
{
  "offers": ["OfferOut in the requested distinct ID order"],
  "dimensions": [{"id": 3, "label": "通勤", "values": [{"offer_id": 1, "value_text": "地铁 35 分钟"}, {"offer_id": 2, "value_text": null}]}],
  "missing": [{"offer_id": 2, "path": "offer_snapshot/perks", "label": "福利"}]
}
~~~

  The endpoint validates distinct positive visible Offer IDs, preserves Offer request order, accepts at most 8 unique active dimension IDs, sorts returned dimensions by numeric dimension ID, sorts each value list by Offer ID, and returns missing values explicitly as `null`/“尚未填写”. DELETE is the only way to clear a value; PUT with blank/null is still 422. It never changes the existing `/compare` response type. Validate positive IDs, visible Offer ownership, nonblank UTF-8 text, and reject score/rating fields. Add a direct request regression proving `/api/offers/comparison?ids=1,2` returns the structured object rather than being consumed by `/api/offers/{offer_id}` and returning a dynamic-ID 422.
- [ ] Step 4: Add typed OfferComparisonDimension, OfferComparisonValue, OfferComparisonRead, and request/response types and typed service functions in web/src/types/offer.ts and web/src/services/offers.ts. The comparison UI uses the new structured read endpoint and never interprets the legacy Offer[] response as dimension data. Do not add an offer_id Chat context field.
- [ ] Step 5: Run uv run pytest tests/test_offer_comparison_dimensions.py -q; run relevant web service tests; commit:
~~~powershell
git add src/offerpilot/repositories/offer_comparison.py src/offerpilot/schemas.py src/offerpilot/api.py tests/test_offer_comparison_dimensions.py web/src/types/offer.ts web/src/services/offers.ts
git commit -m "feat: AI add offer comparison dimensions"
~~~

## Task 3: Add the user-facing custom dimension workflow

**Files:**

- Create: web/src/components/OfferComparisonDimensions.tsx, web/src/components/OfferComparisonDimensions.test.tsx
- Modify: web/src/components/OfferCenterView.tsx, web/src/components/OfferCompareDrawer.tsx, web/src/types/offer.ts, web/src/services/offers.ts

- [ ] Step 1: Add mounted UI tests before implementation. Start with two visible Offers and no dimensions. Assert the Offer center can open a “管理比较维度” panel, create a nonblank dimension, edit an Offer-specific value, clear an accidentally entered value through “清除值/标记为尚未填写”, archive a dimension, and show archived dimensions only in the history/read-only view. Assert values are saved per Offer and a blank value renders “尚未填写”, never a negative label or a fake value such as “无” or “未知”.
- [ ] Step 2: Add selection tests. Render nine active dimensions and assert the comparison selector allows at most eight, disables the ninth with a Chinese explanation, preserves the selected IDs in the UI state, and passes exactly those IDs to GET /api/offers/comparison and to the negotiation drawer. Rerender with the same active dimensions in a different server order and assert the selection remains keyed by numeric ID.
- [ ] Step 3: Run from web: npm.cmd test -- --run src/components/OfferComparisonDimensions.test.tsx; expected failures are the missing panel, value editor, and selection contract.
- [ ] Step 4: Implement the panel with local selection state owned by OfferCenterView. Dimension creation/archive and per-Offer value edits call only the typed comparison-dimension/value services after the user's explicit save click. “清除值” calls DELETE and then refreshes the structured read; it never writes a sentinel string. Comparison selection is session-local, never an AI decision and never a database preference. Pass the selected numeric IDs to the structured comparison endpoint and later to OfferNegotiationDrawer; do not pass raw values from stale UI state.
- [ ] Step 5: Rerun the mounted UI test and the backend structured-read tests. Commit:
~~~powershell
git add web/src/components/OfferComparisonDimensions.tsx web/src/components/OfferComparisonDimensions.test.tsx web/src/components/OfferCenterView.tsx web/src/components/OfferCompareDrawer.tsx web/src/types/offer.ts web/src/services/offers.ts
git commit -m "feat: AI add offer comparison dimension UI"
~~~

## Task 4: Harden the existing multi-Offer comparison contract

**Files:**
- Modify: src/offerpilot/api.py, src/offerpilot/repositories/offers.py, web/src/components/OfferCenterView.tsx, web/src/components/OfferCard.tsx, web/src/components/OfferCompareDrawer.tsx
- Test: tests/test_offers_api.py, web/src/components/OfferCenterView.test.tsx, new web/src/components/OfferCompareDrawer.test.tsx

- [ ] Step 1: Add failing tests for duplicate IDs being removed before the minimum-two check, missing/invisible IDs returning stable errors, and two visible distinct IDs preserving request order. Mounted UI tests must reject score/rank/weight/average/best/recommend/accept/decline wording and show blank values as 尚未填写.
- [ ] Step 2: Run uv run pytest tests/test_offers_api.py -q and npm.cmd test -- --run src/components/OfferCenterView.test.tsx src/components/OfferCompareDrawer.test.tsx from web.
- [ ] Step 3: Keep comparison in Offer center only. Pass active dimension/value rows into the drawer without sorting user-selected Offer columns. Add explicit “用此 Offer 准备谈薪” callbacks carrying the exact Offer ID; do not infer an Offer from column position after rerender.
- [ ] Step 4: Rerun both focused commands and commit:
~~~powershell
git add src/offerpilot/api.py src/offerpilot/repositories/offers.py web/src/components/OfferCenterView.tsx web/src/components/OfferCard.tsx web/src/components/OfferCompareDrawer.tsx tests/test_offers_api.py web/src/components/OfferCenterView.test.tsx web/src/components/OfferCompareDrawer.test.tsx
git commit -m "feat: AI harden offer comparison"
~~~

## Task 5: Build the strict negotiation Proposal contract

**Files:**
- Create: src/offerpilot/ai/offer_negotiation.py, src/offerpilot/repositories/offer_negotiation.py, tests/test_offer_negotiation_ai.py, tests/test_offer_negotiation_repository.py
- Modify: src/offerpilot/api.py, src/offerpilot/schemas.py

- [ ] Step 1: Add failing AI/repository tests for the exact generation payload and snapshot above: same dimension set in different order has the same canonical JSON/fingerprint and `/dimensions/dimension_001/...` paths; an accidentally entered value can be deleted, then the structured read returns null and the generation snapshot stores null without creating a value evidence path; a model reference to that missing value is rejected as unknown_evidence_ref; archived/ninth/duplicate dimensions return 422 without an Attempt; and Provider payload excludes database IDs. Add positive numeric evidence tests for `base_monthly=28000` with excerpt `28000`, `months_per_year=12` with excerpt `12`, and `signing_bonus=0` with excerpt `0`; add negative tests for comma/currency/unit/Chinese numeral rewrites and partial numeric excerpts. Also cover duplicate JSON keys, fenced JSON, non-object roots, missing/extra fields, wrong types, blank id/text/rationale/excerpt, non-finite values, concrete array limits, duplicate item IDs across all arrays, invalid source/path/excerpt, and forbidden decision/law/market/company-policy assertions. Assert the terminal matrix exactly: no-evidence returns locally generated safe_empty 201 with zero Provider calls; pure shape failure then valid repair makes exactly two calls and returns normal; pure shape failure twice makes exactly two calls, stores safe_empty, and same-key replay makes zero calls; semantic failure makes one call, stores no Proposal, returns 502 offer_negotiation_unverifiable, and same-key replay makes zero calls; Provider/network failure stores provider_unknown, returns 502 offer_negotiation_provider_error, and same-key retry uses the original frozen input.
- [ ] Step 2: Run uv run pytest tests/test_offer_negotiation_ai.py tests/test_offer_negotiation_repository.py -q; expected failure is the absent parser/validator/repository behavior.
- [ ] Step 3: Define parse_offer_negotiation_json, validate_offer_negotiation, classify_offer_negotiation_failure, and a server-generated SAFE_EMPTY_OFFER_NEGOTIATION. Parse with duplicate-key rejection, reject fenced text/non-finite values, enforce exact fields, and codify these concrete limits: at most 8 items in each of the four arrays, at most 4 evidence_refs per item, at most 64 Unicode characters for id, at most 600 characters for text/rationale, at most 400 characters for excerpt, and at most 8 active comparison dimensions in one snapshot. Validate each reference against the fixed canonical paths and snapshot values. Every clarification_questions item must include at least one valid evidence reference; there is no context-free allowlist. Only pure shape failures trigger one repair; after a second shape failure the server creates safe_empty. Fake source/path/excerpt, over-limit, duplicate semantic IDs, and forbidden decision language are terminal semantic failures: mark the Attempt invalidated, return 502 offer_negotiation_unverifiable, and never create a Proposal.
- [ ] Step 4: Implement prepare_or_replay, claim_generation, mark_provider_unknown, complete_ready, and invalidate. First transaction inserts the row with generating, revision 1, random lease token and unexpired lease before Provider. Same key/different fingerprint is 409. Ready is immutable. Provider-unknown returns 202 while lease is live and can be claimed only after expiry. Pure shape second failure transitions to ready/safe_empty; semantic failure transitions to invalidated/contract_failed and is stable for same-key replay without Provider. Writes match status/revision/lease token; a late owner cannot overwrite ready. A definite frontend failure clears its key after the server has returned the stable failure; an unknown Provider result preserves key and freezes the draft.
- [ ] Step 5: Add these typed routes: POST /api/offers/{offer_id}/negotiation/proposals for generation, GET /api/offers/{offer_id}/negotiation/proposals for current-Offer history, GET /api/offer-negotiation/proposals/{proposal_id} for immutable history after Offer changes, and POST /api/offer-negotiation/proposals/{proposal_id}/confirm for HITL handoff. Validate visible Offer/current input before creating an Attempt; historical reads return frozen content with source_changed after Offer edit/delete. Use distinct 201/200/202/409/422/502 envelopes and safe Chinese mapping. Diagnostics contain only category, HTTP status, timeout, elapsed time, repair count, and hashed Provider request ID.
- [ ] Step 6: Run uv run pytest tests/test_offer_negotiation_ai.py tests/test_offer_negotiation_repository.py tests/test_offer_negotiation_api.py -q; commit:
~~~powershell
git add src/offerpilot/ai/offer_negotiation.py src/offerpilot/repositories/offer_negotiation.py src/offerpilot/api.py src/offerpilot/schemas.py tests/test_offer_negotiation_ai.py tests/test_offer_negotiation_repository.py tests/test_offer_negotiation_api.py
git commit -m "feat: AI add offer negotiation proposals"
~~~

## Task 6: Add atomic HITL Brief confirmation and historical reads

**Files:**
- Modify: src/offerpilot/repositories/offer_negotiation.py, src/offerpilot/api.py, src/offerpilot/schemas.py
- Test: tests/test_offer_negotiation_api.py, tests/test_offer_negotiation_repository.py

- [ ] Step 1: Add failing tests binding Proposal, Offer, application_id, selected block IDs, edited content, and source fingerprint in one transaction. No Brief may exist before confirmation. Successful confirmation creates exactly one Brief. Concurrent two-session confirmations create one row and replay the same Brief. Changed source returns 409 with no write. Deleted Offer leaves Proposal/Brief history readable and marked changed.
- [ ] Step 2: Run uv run pytest tests/test_offer_negotiation_api.py -k "confirm or brief or source_changed" -q.
- [ ] Step 3: Implement confirm_proposal with the repository transaction pattern and UNIQUE(proposal_id). Validate selected blocks against immutable Proposal and store user edits only as derived content with Proposal evidence retained. A uniqueness hit returns the original Brief.
- [ ] Step 4: Return immutable Proposal/Brief content, source_states, source_changed, offer_id, and application_id from history. Never rewrite hashes or rebind to a new Offer. Map 404/409/422 to safe Chinese messages.
- [ ] Step 5: Run uv run pytest tests/test_offer_negotiation_api.py tests/test_offer_negotiation_repository.py -q; commit:
~~~powershell
git add src/offerpilot/repositories/offer_negotiation.py src/offerpilot/api.py src/offerpilot/schemas.py tests/test_offer_negotiation_api.py tests/test_offer_negotiation_repository.py
git commit -m "feat: AI add offer negotiation confirmation"
~~~

## Task 7: Add Offer-center single-Offer negotiation flow

**Files:**
- Create: web/src/components/OfferNegotiationDrawer.tsx, web/src/components/OfferNegotiationDrawer.test.tsx
- Modify: web/src/types/offer.ts, web/src/services/offers.ts, web/src/components/OfferCard.tsx, web/src/components/OfferCenterView.tsx

- [ ] Step 1: Add failing mounted UI tests using mocked typed services, not source-string assertions. Cover both existing and new entries: clicking “谈薪教练” still invokes the existing onCoach callback and does not call negotiation generation; clicking the separate “开始谈薪准备” entry opens the new drawer. Then cover required goal/concerns/scenario, explicit generation confirmation, exact frozen-source display, editable four-block result, selection/edit/confirm, and zero unrelated write calls. Test the stable error-code branches separately: 202 generating retains key and freezes input; 502 offer_negotiation_provider_error retains key, freezes input, and exposes same-key retry; 502 offer_negotiation_unverifiable clears the definite key, does not expose retry of the invalidated Attempt, and requires a new Attempt; 422 clears a draft with no Attempt; 409 source_changed shows read-only history.
- [ ] Step 2: Run from web: npm.cmd test -- --run src/components/OfferNegotiationDrawer.test.tsx src/components/OfferCenterView.test.tsx; record the expected failures.
- [ ] Step 3: Add typed service functions for generation, history, retry, and Brief confirmation. Keep attempt state keyed by offerId in the durable parent/state pattern so unmount/reopen retains provider-unknown key and frozen input. Branch on stable error_code, never on HTTP status alone: offer_negotiation_provider_error and 202 retain/freeze/retry; offer_negotiation_unverifiable clears the key and starts only with a new user-generated key; 422 and definite 409 follow their fixed cleanup semantics. Render only Chinese safe messages and never raw provider/Axios/snapshot text.
- [ ] Step 4: Add the separate card entry and center integration. Preserve existing Offer CRUD/layout, the current “谈薪教练” callback and Chat behavior, and pass exact Offer IDs from compare columns. Do not rename, remove, or redirect “谈薪教练”.
- [ ] Step 5: Rerun focused tests and commit:
~~~powershell
git add web/src/components/OfferNegotiationDrawer.tsx web/src/components/OfferNegotiationDrawer.test.tsx web/src/types/offer.ts web/src/services/offers.ts web/src/components/OfferCard.tsx web/src/components/OfferCenterView.tsx
git commit -m "feat: AI add offer negotiation UI"
~~~

## Task 8: Integrate explicit Offer context into Pilot

**Files:**
- Modify: existing Pilot attachment/context files under web/src/features/pilot/, web/src/components/ChatPanel/, and web/src/layout/AppShell.tsx only where the current attachment contract requires it.
- Test: new web/src/components/OfferPilotNegotiation.test.tsx, existing Pilot attachment/context tests, tests/test_chat_api.py, tests/test_chat_repository.py if backend context behavior needs a regression.

- [ ] Step 1: Add failing integration tests for the trigger boundary: with no selected Offer and no user action, Pilot renders no Offer card and makes no Offer list, Chat, or Provider request; when the user explicitly clicks the existing “准备谈薪” action, Pilot opens a local selector; before selection it still sends no message, creates no Chat row, and calls no Provider; after explicit selection it renders the static card. Also cover associated context_type=application/context_ref, workspace context for an unassociated Offer, no persisted offer_id payload, the same service functions for Pilot/UI, and no auto-send/provider call before confirmation.
- [ ] Step 2: Run from web: npm.cmd test -- --run src/components/OfferPilotNegotiation.test.tsx src/features/pilot/PilotAttachmentContext.render.test.tsx.
- [ ] Step 3: Reuse explicit Offer attachment. Add no new Chat discriminator, API route, prompt, repository, retry state, or auto-approval path. The passive Pilot view does not list Offers. Only the user's explicit “准备谈薪” action opens a selector; selecting one Offer creates only local attachment state, then renders the static card and opens the same drawer after the user confirms the action. The UI and Pilot call the same generation, history, and confirmation service functions.
- [ ] Step 4: Rerun focused tests, Chat tests if changed, and commit:
~~~powershell
git add web/src/features/pilot web/src/components/ChatPanel web/src/layout/AppShell.tsx web/src/components/OfferPilotNegotiation.test.tsx tests/test_chat_api.py tests/test_chat_repository.py
git commit -m "feat: AI connect offer negotiation to Pilot"
~~~

## Task 9: Complete error handling, diagnostics, and isolated browser harness

**Files:**
- Create or modify: scripts/offer-negotiation-real-ai-browser-harness.ps1, tests/test_offer_negotiation_browser_harness.py
- Modify only scoped diagnostic/error mapping paths in src/offerpilot/api.py, src/offerpilot/ai/offer_negotiation.py, and web/src/components/OfferNegotiationDrawer.tsx.

- [ ] Step 1: Add failing harness/error tests for redacted Chinese mapping, scoped diagnostics, 422 cleanup, 404 history/source_changed, 409 freeze, and code-specific retry semantics. Assert 202 generating retains the key; 502 offer_negotiation_provider_error retains the key and same-key retry; 502 offer_negotiation_unverifiable clears the key, leaves the Attempt invalidated, and rejects same-key retry without another Provider call. The harness must fail if it classifies either 502 by status alone.
- [ ] Step 2: Implement an isolated temporary data directory and a silent byte-for-byte copy of the real config.json, including the configured Provider secret. Do not print, parse into logs, archive, or report the copied config; delete it in finally. Record only local browser /api requests and the actual configured Provider scheme+host+port. Fail closed on any unapproved host or server egress. Never emit keys, Offer/JD/resume text, evidence excerpts, raw model output, snapshots, or secrets.
- [ ] Step 3: Add the real sequence: create two Chinese Offers; add 通勤 and 成长空间; compare in user order without ranking; select one Offer; fill Chinese goal/concerns/scenario; confirm AI; inspect evidence; edit/select blocks; confirm Brief; close/reopen history; explicitly trigger Pilot “准备谈薪”, select the same Offer, and open the same flow. Assert no application/event/resume/material/question/knowledge/reminder or Chat row/message write beyond explicit Offer/Proposal/Brief operations. Snapshot the Chat row/message count before and after both UI and Pilot paths. If the Provider returns offer_negotiation_unverifiable, record it as a definite invalidated Attempt requiring a new key; if it returns offer_negotiation_provider_error, verify frozen input, retained key, and same-key retry. Never convert one code into the other based on HTTP 502.
- [ ] Step 4: Run uv run pytest tests/test_offer_negotiation_browser_harness.py -q and the Offer-specific isolated real-AI verify command; do not substitute another AI flow.
- [ ] Step 5: Commit:
~~~powershell
git add scripts/offer-negotiation-real-ai-browser-harness.ps1 tests/test_offer_negotiation_browser_harness.py src/offerpilot/api.py src/offerpilot/ai/offer_negotiation.py web/src/components/OfferNegotiationDrawer.tsx
git commit -m "test: AI verify offer negotiation boundaries"
~~~

## Task 10: Independent review and complete release gates

**Files:**
- Review all changed files.
- Create: docs/reports/2026-08-01-offer-negotiation-release-verification.md

- [ ] Step 1: From the Offer worktree root run:
~~~powershell
uv run pytest tests/test_offer_comparison_dimensions.py tests/test_offer_negotiation_migration.py tests/test_offer_negotiation_ai.py tests/test_offer_negotiation_repository.py tests/test_offer_negotiation_api.py tests/test_offer_negotiation_browser_harness.py -q
uv run ruff check src tests
uv run mypy src
Set-Location web
npm.cmd test -- --run src/components/OfferCenterView.test.tsx src/components/OfferCompareDrawer.test.tsx src/components/OfferNegotiationDrawer.test.tsx src/components/OfferPilotNegotiation.test.tsx
npm.cmd run build
Set-Location ..
$featureBase = '14ec28b'
git diff --check "$featureBase..HEAD"
~~~
Record actual exit codes and counts; a timeout is not a pass.
- [ ] Step 2: Obtain independent code review for history retention, duplicate IDs, duplicate JSON, evidence validation, lease/CAS, confirmation uniqueness, Pilot leakage, cross-domain writes, and raw-content logging. Fix P0/P1 with regression tests; name any accepted P2 in the report.
- [ ] Step 3: Run grouped backend gate rather than one timeout-prone pytest. Verify collected node-id manifest equals a disjoint group union, no node appears twice, and only the four pre-approved Windows symlink-permission skips occur with exact reasons. Run frontend full tests in stable groups, TypeScript/build, Ruff, Mypy, local smoke, local verify, and Offer-specific real-AI verify.
- [ ] Step 4: Run isolated browser flow with the silent exact config copy and temporary OFFERPILOT_DATA. Verify local-only browser traffic, exact Provider endpoint egress, no recruitment access, HITL confirmations, source-changed history, and zero unrelated writes including zero Chat writes. Stop services, browser targets, and proxy; remove temp data and copied config; compare the original user data directory before/after.
- [ ] Step 5: Write docs/reports/2026-08-01-offer-negotiation-release-verification.md with commit/base, commands, exit codes, counts, skips, focused/AI/browser results, cleanup comparison, review findings, and risks. Do not include secrets, Offer/JD/resume text, evidence excerpts, raw model output, or raw request IDs.
- [ ] Step 6: Scan changed files for placeholder markers, placeholder error mappings, decision language, and offer_id Chat context fields. From the repository root set `$featureBase = '14ec28b'`, run `git diff --check "$featureBase..HEAD"`, confirm `git status --short` is empty, verify that the report is Git-tracked, then separately stage and commit the report:
~~~powershell
git add -f docs/reports/2026-08-01-offer-negotiation-release-verification.md
git commit -m "docs: AI record offer negotiation release verification"
~~~

## Plan self-review

- [x] Single-Offer negotiation and optional multi-Offer comparison are separate.
- [x] Feature base is current 14ec28b after rebase; all feature diff checks use that current base.
- [x] Custom dimensions preserve user text, support archive, and never become scores.
- [x] Dimension management has a mounted UI task for create, archive, per-Offer values, eight-item selection, compare reads, and negotiation snapshot input.
- [x] Legacy GET /api/offers/compare remains Offer[]; structured comparison is a separate endpoint with fixed order and missing-value fields.
- [x] Dimension selection is explicit, bounded at eight, canonically sorted, and represented by stable provider paths in the frozen snapshot.
- [x] All clarification questions require validated evidence; no unbounded context-free allowlist exists.
- [x] 202 and the two 502 codes have separate UI, Attempt, key, replay, and browser-harness assertions; no branch uses HTTP 502 alone.
- [x] New migration is unused 0017_offer_comparison_negotiation; fresh and real-old-database upgrades are tested.
- [x] Proposal snapshot, fingerprint, hash, idempotency, lease/CAS, unknown result, one repair, safe-empty, and source-change history are assigned to tasks.
- [x] Brief confirmation has transaction-level ownership validation and UNIQUE(proposal_id) replay.
- [x] UI and Pilot share APIs; Pilot requires explicit Offer selection and no offer_id Chat persistence.
- [x] Pilot is passive until the user explicitly triggers negotiation; the selector and static card create no Chat write or Provider call.
- [x] Current Offers are physically deleted by existing behavior; no soft-delete assumption or new soft-delete state is introduced.
- [x] No auto-decision, auto-send, external recruitment access, cross-domain write, or Offer-state mutation is introduced.
- [x] Existing “谈薪教练” remains a separate unchanged entry; “开始谈薪准备” has separate mounted regression coverage.
- [x] Browser verification is isolated, Chinese, redacted, and checks actual local/Provider boundaries.
- [x] Paths, route names, types, statuses, and error semantics are consistent with the approved design; no placeholder instruction is required.
