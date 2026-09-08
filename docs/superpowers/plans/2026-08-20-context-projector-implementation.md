# Context Projector Implementation Plan

Design: `docs/superpowers/specs/2026-08-20-context-projector-design.md`  
Baseline: `dc3d73ad259b9e7db84e6a3c8ae9508b3b085ede`

Every behavior task follows red → minimal implementation → focused green → regression gate. Mechanical/AST gates are added before moving provider paths. Commits use `type: AI English subject`.

## 1. Immutable contracts and canonicalization

1. Add failing contract tests for contributor/source states, frozen DTO immutability, closed diagnostics, canonical serialization, revision/content fingerprints, and sensitive-field rejection.
2. Implement `context_projector/contracts.py` and versioned canonical JSON helpers.
3. Add adversarial tests for ORM/session/repository objects, NaN/Infinity, arbitrary diagnostic strings, and mutation attempts.
4. Run the focused projector contract suite and mypy for the new package.

## 2. Tool surface selector and response binding

1. Add failing tests proving exact typed-catalog allowlist, deterministic legacy exclusion, stable original ordering, structural/lexical union, dependency closure, complete fallback, and fail-closed invalid signals/catalog drift.
2. Implement versioned selector rules and `ModelCallSurfaceBinding`.
3. Add failing runner tests for unexposed provider tool calls, bare responses, mismatched call/candidate/surface binding, and executor zero.
4. Implement `BoundProviderResponse` validation at dispatch without changing Phase 2 tool contracts.

## 3. Turn groups, relevance, and budgets

1. Add failing table tests for grouping tool-call/result/confirmation chains, orphan compatibility, duplicate confirmation rejection, stable recent/relevance ordering, optional skip-and-continue, and original-order restoration.
2. Implement pure history grouping and versioned bounded bilingual relevance scoring.
3. Add failing boundary/property tests for all frozen token and byte constants, negative remainder, 1 MiB history messages, canonical wrapper cost, share rounding, shared-pool allocation, and candidate-specific reserves.
4. Implement conservative estimator, deterministic allocator, structured chunk metadata, and final integrity check.

## 4. Pure projector and runtime audit

1. Add end-to-end pure projection fixtures covering all ten contributors, mandatory overflow, optional truncation, disabled deferred contributors, fingerprints, and repeatability across process/hash seed/time.
2. Implement `ModelSurfaceProjector.project()` returning `FrozenModelSurface`, `ModelCallSurfaceBinding`, and transient `RuntimeSurfaceAudit`.
3. Add failure-injection tests proving first-projection failure has provider/executor/write/message counts of zero.

## 5. Frozen provider gateway

1. Add failing endpoint normalization tests for schemes, userinfo, query, fragment, control/backslash/path, IPv6 zone, canonical ports/routes, and final-address mismatch.
2. Add failing transport/gateway tests for frozen config, one attempt per candidate, no retry, deterministic fallback order, shared surface, no fallback after visible stream delta, and bound response provenance.
3. Split Chat transport into `FrozenProviderExecutionChain`, `AgentProviderGatewaySession`, and `SingleCandidateAgentTransport`; retain non-Agent clients outside this boundary.
4. Add adapter request token/byte preflight immediately before all Chat network calls and assert no reprojection on failure.

## 6. Snapshot loader and concurrency controls

1. Add failing tests for one connection/transaction, rollback-before-processing, primitive-only freeze, `fetchmany(32)`, and no implicit sessions/lazy loads.
2. Add fake-clock and real-SQLite tests for reservation/checkout-inclusive 2,000 ms total and 1,800 ms work deadline, busy/progress interruption, generation-safe watchdog cleanup, invalidation, and `BaseException` propagation.
3. Implement normalized database identities, a writer-fair process coordinator, size-four warmed FIFO pool, generation-scoped leases, and `ContextSourceLoader`.
4. Route Phase 3 delivery heartbeat through the same coordinator writer gate and add starvation/fencing regression tests.

## 7. Journal Manifest V2 and migration 0027

1. Add failing schema tests for V1/V2 coexistence, every array ceiling, closed enums, forbidden privacy fields, HMAC shape/key ID, aggregate chunk limit, and omission of logical fingerprint from the manifest.
2. Add a maximal semantic fixture simultaneously reaching all maxima and assert canonical size `< 65,536`.
3. Add migration fixtures proving 65,536 accepts and 65,537 rejects with no padding field.
4. Implement migration, validator, fail-open `RuntimeSurfaceAudit` projection, and trace checks without backfill.
5. Verify journal failures cannot alter model surface or provider result.

## 8. Agent runner and API cutover

1. Add failing sync/SSE tests that loader executes once per segment, read-tool loops reuse sources, confirmation continuation reloads, and terminal replay/deterministic actions stay provider zero.
2. Cut runner model calls to projector + gateway and remove old prompt assembly, Chat-specific provider fallback, and duplicate construction paths rather than retaining dual routing.
3. Add confirmation tests for approve/modify transaction ordering, reject fast path exclusions, pre-loader delivery heartbeat, ownership loss, and late-bundle fencing.
4. Add `RuntimeSignalSink` state-machine tests and move title registration to sync/SSE transport drain/close after the first complete valid response.
5. Preserve API/SSE payload and event-order golden tests.

## 9. Network and source static gates

1. Commit the fixed AST manifest for 13 non-Agent functions, 18 high-level provider calls, and five raw network boundaries.
2. Add gates against dynamic import/importlib, LiteLLM re-export/direct CLI import, direct SDK calls, and generic model-endpoint HTTP calls outside the allowlist.
3. Add source gates proving no summary/memory/Knowledge retrieval tables, API, UI, provider calls, or writes were introduced.

## 10. Release verification and independent review

1. Run all focused Context Projector tests, then `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, `npm test -- --run`, `npm run build`, and `uv run oc smoke --static-dir web/dist`.
2. Run migration/local verification including 65,536/65,537 fixtures, deterministic seed/time repeats, untracked/allowlist audits, and clean-worktree check.
3. Run controlled real-AI verification and capture provider request/event evidence without secrets or model-visible content.
4. Use the built-in browser for sync Chat, SSE Chat, read tool, approve, modify, reject, Pending resume, cancellation, and delivery-recovery journeys.
5. Request an independent sub-agent code review for security, correctness, compatibility, privacy, concurrency, and test gaps; fix findings or record accepted residual risk.
6. Commit the final verification report only after fresh evidence. Do not push or merge.

