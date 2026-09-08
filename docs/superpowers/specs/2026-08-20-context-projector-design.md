# Context Projector Design

Status: **已复审通过**  
Baseline: `dc3d73ad259b9e7db84e6a3c8ae9508b3b085ede`  
Compatibility: 传输与业务安全兼容，模型输入允许受控破坏性变化。

## Scope and invariants

Context Projector separates the durable conversation from the exact model-visible surface. It may change answer text, tool choice, and provider attempt count, but it must preserve HTTP/SSE fields and ordering, recovery behavior, write-tool at-most-once terminal commit, HITL, Pending Action identity, Ledger fencing, capability checks, and delivery fencing. Terminal replay, deterministic actions, and delivery recovery remain provider-free.

This phase does not implement summaries, memory, or Knowledge retrieval. Their contributors are fixed disabled contributors and consume no tokens or storage. The implementation must not add provider retries, tool retries, legacy fallback paths, tables, APIs, or UI for those deferred features.

## Runtime boundary

Each request or confirmation continuation executes this boundary once:

```text
request / confirmation continuation
  -> ContextSourceLoader (one read snapshot)
  -> FrozenContextSources
  -> ModelSurfaceProjector (pure, once per model_call_id)
  -> FrozenModelSurface
  -> AgentProviderGatewaySession
  -> SingleCandidateAgentTransport
```

A confirmation continuation is a new segment and reloads sources after the domain transaction commits. The loader returns immutable primitive DTOs, canonical JSON, revisions, and fingerprints only; no ORM object, Session, or Repository crosses the rollback boundary. An agent read-tool loop reuses the frozen sources and creates a new surface per model call without querying business repositories.

The provider fallback chain is frozen once per segment. All candidates for one logical model call reuse the same surface. `ModelCallSurfaceBinding` binds the model call, surface fingerprint, and exposed tools. A provider response is accepted only as a `BoundProviderResponse` created at the network boundary; the dispatcher never accepts an unbound response and never reruns selection.

## Contributors

Contributors run in this exact order:

```text
static_policy
current_scope
active_control
request_page_context
request_attachments
conversation_history
current_request
confirmed_memory
knowledge_context
older_conversation_summary
```

Their closed states are `ready`, `not_applicable`, `disabled`, and `unavailable`. `disabled` is intentional product configuration; `unavailable` is an explicitly permitted missing source. Repository, revision, relationship, integrity, and implementation errors fail closed. Diagnostics contain only closed enums, booleans, and bounded counts.

`current_request` and protocol-required active control are mandatory and never truncated. Active control exposes only the tool name, paired identity, and fixed model-facing confirmation semantics; tokens, operation IDs, lease/owner data, CAS revisions, HMAC material, and internal failures are excluded. A valid Application scope without JD, analysis, or notes is `ready` with explicit absent leaf states. A missing attachment record may create an unavailable notice, while a present but corrupt revision or link fails closed.

The last three contributors always return `disabled` in this phase and perform no database, provider, network, or token-estimation work.

## Conversation projection

History is restricted to the current Conversation and current `context_type/context_ref`. A `TurnGroup` starts at a user message and ends before the next user message. Assistant tool calls, matching tool results, confirmations, previous read-tool chains, and chained Pending state are atomic.

Selection is deterministic:

```text
mandatory
  -> recent candidates, newest first
  -> relevant candidates by score descending and message id descending
  -> restore original message-id order
  -> integrity and budget final check
```

The two newest groups are recent candidates, not guaranteed inclusions. An optional group that does not fit is skipped while selection continues. Relevance uses bounded structural signals and versioned Chinese/English lexical rules only—never a model, randomness, wall-clock time, or Python `hash()`. Legacy orphan messages receive read-only compatibility projection and never fabricate a successful tool result. The final history cannot contain dangling tool calls, orphan tool messages, or duplicate confirmation results.

An optional history message over 1 MiB omits its complete TurnGroup; a mandatory group over the limit fails closed.

## Tool surface

Tool selection happens before history allocation:

```text
trusted structural signals
  -> bounded lexical domain signals
  -> domain union
  -> concrete dependency closure
  -> integrity check
  -> original full-catalog order
```

Only the 25 approved typed catalog tools may be exposed. The three deterministic legacy tools are never exposed. No signal and explicitly allowed ambiguity fall back to all 25 typed tools. Unknown/illegal page or attachment kind, unsupported signal version, selector exception, missing closure dependency, and catalog drift fail closed before provider or executor activity. A returned unexposed tool produces the fixed `validation_error / unknown_tool` failure with executor count zero.

The surface fingerprint covers full canonical provider envelopes, not names. Tool visibility is advisory only; execution still performs catalog lookup, schema validation, capability, binding, entity authorization, HITL, and Ledger checks.

## Budgets and chunking

Frozen policy `model-surface-budget-v1` uses:

| Constant | Value |
| --- | ---: |
| compatibility context window | 32,768 |
| default output reserve | 4,096 |
| provider framing reserve | 1,024 |
| product input cap | 65,536 estimator units |
| canonical messages byte cap | 256 KiB |
| provider tools byte cap | 64 KiB |
| combined surface byte cap | 320 KiB |
| adapter request body byte cap | 512 KiB |

For candidate `i`, `provider_input_limit[i] = context_window - output_reserve - framing_reserve`; the shared surface limit is the minimum candidate limit and product cap. Tool envelope, mandatory surface, and fixed assembly costs are subtracted first. Any negative result fails closed.

Each candidate must independently satisfy estimated input plus reserves within its context window, and canonical byte caps. Estimators are fixed-version candidate counters or one documented conservative counter covering every candidate. The adapter repeats token and byte preflight on the final request body without reprojecting.

Optional budget shares are scope 25%, attachments 35%, and history 40%, rounded down. Remainder enters a shared pool allocated in fixed request order. Incremental cost includes final canonical role/name/tool-call wrappers and tool framing. Structured large fields use deterministic leaf/section chunks carrying path, ordinal, total, truncated state, original byte count, and original codepoint count. Mandatory request/control/tool chains are never truncated.

## Runtime audit and journal manifest

`RuntimeSurfaceAudit` is a pure projector result, transient, independent of journal keys, and never persisted. `SafeRunRecorder` may project it to `JournalSurfaceManifestV2`. Canonicalization, domain-separated HMAC, and persistence are each fail-open and cannot change the surface or provider outcome.

The event order remains `context.captured -> model.requested -> model.completed|model.failed`. `model.requested` is recorded immediately before the first real network call; projection and adapter preflight failures do not record it. Provider fallback remains one logical `model_call_id` and one event group.

Migration `0027_context_projector_manifest_v2` adds Snapshot V2 with a 65,536-byte ceiling while V1 retains 16 KiB. Validators coexist and old rows are not backfilled. V2 limits are 8 providers, 10 contributors, 32 history groups, 25 tools, 8 sources, 32 signals, 32 chunks per source, and 64 chunks total. Boundary fixtures exercise 65,536 and 65,537 bytes without padding fields.

`AgentContextSnapshot.logical_input_fingerprint` remains the durable truth and events copy it; the manifest does not duplicate it. The journal never stores message/source bodies, attachment names, entity IDs, endpoints, model identities, prompts, tool arguments, or tool results. Candidate, source, and history identities use domain-separated HMAC. Trace validation checks schema, formats, key ID, and Snapshot/Event consistency and does not reconstruct identities.

Every present source records both `revision_identity` and `content_revision_fingerprint`; the latter hashes the complete authorized projection even if the database revision is unchanged.

## Source loader, deadlines, and coordination

SQLite stays in rollback-journal mode. The loader uses one Session-bound read unit of work, one connection, and one transaction. Repository helpers cannot create sessions. It copies small primitive batches with `fetchmany(32)`, rolls back, and only then canonicalizes, fingerprints, chunks, and assembles.

The total deadline is 2,000 ms: 1,800 ms work plus 200 ms cleanup. Timing starts before process-gate reservation and pool checkout. A dedicated warmed pool has size four, no overflow, and non-blocking FIFO reservation. Checkout sets `busy_timeout` from remaining budget. A SQLite progress handler checks every 100 VM instructions.

Each checkout creates a generation-scoped `ConnectionLease` and strong-reference watchdog. The owner waits with `finished_event.wait(max(0, work_deadline - monotonic()))`, sets finished, waits for watchdog exit, restores connection state, then returns it. Failure to prove watchdog exit or restore state invalidates/closes the connection. Cancellation and all other `BaseException` values clean up and propagate unchanged; deadline, busy, and ordinary database exceptions map to `source_load_failed`.

A writer-fair process coordinator is shared by normalized database identity. Loaders are readers and Phase 3 delivery heartbeats are writers. External processes remain governed by SQLite busy behavior and Ledger fencing.

## Provider execution and endpoint security

The frozen segment chain contains ordered candidates, normalized endpoint/route, capabilities, budgets, estimator version, credential handles, and a chain fingerprint. Credentials are transient `repr=False` fields and never enter surfaces, journal, trace, or logs. Gateway sessions do not reread configuration. A single-candidate transport performs exactly one provider call with no retry, fallback, or dynamic configuration lookup; only the gateway advances through the frozen chain. Streaming fallback stops permanently after the first visible delta.

Endpoint normalization is strict and versioned. Only HTTP(S) is accepted. Userinfo, query, fragment, empty host, backslashes, control characters, malformed paths, and IPv6 zone IDs are rejected. The adapter compares its final destination with the frozen normalized destination immediately before network access.

This boundary applies only to Chat/LangGraph. Other AI features retain a read-only AST manifest of 13 functions and 18 high-level calls. The five permitted raw network boundaries are:

```text
ConfiguredAIClient._complete_with_provider
ConfiguredAIClient._stream_with_provider
LiteLLMKnowledgeBriefProviderClient.complete_once
SingleCandidateAgentTransport.complete_one
SingleCandidateAgentTransport.stream_one
```

The CLI reaches LiteLLM only through the Knowledge provider factory. Static gates reject dynamic imports, `importlib`, re-exports, direct SDK use, and generic HTTP requests to model endpoints outside the allowlist. An unconfigured AI chain is a valid application state and returns the existing Chat error only when Chat is invoked.

## Confirmation, delivery, and title signal

Approve/modify performs prepare, `BEGIN IMMEDIATE`, mutable recheck, claim/authorization, execution, and terminal commit before starting a new continuation segment. Reject validates only token/Pending/Ledger identity and performs rejection CAS plus terminal rejected commit; it must not run schema, capability, binding, entity lookup, preflight, or executor.

After confirmation continuation obtains delivery ownership, its heartbeat starts before loading and remains active through loader, projector, provider, and read-tool loops. Loss of ownership prevents result delivery; late bundles are dropped by generation fencing.

Title generation becomes eligible after the first complete valid Agent response. The runner owns no FastAPI `BackgroundTasks`; it writes once to a capacity-one transient `RuntimeSignalSink`. `try_emit()` returns `emitted`, `duplicate`, `closed`, `full`, or `degraded`, and every outcome except emitted is fail-open. Sync and SSE transport owners drain and close on all exits. Registration state is `not_attempted`, `registered`, `registration_failed`, or `closed`; failed registration is not retried and cancellation never registers.

## Failure and completion semantics

The first projection safety failure guarantees provider zero, executor zero, Pending/Ledger/domain writes zero, and assistant/tool messages zero. Persistence of the original user ChatMessage and existing API failure record follows the current API contract.

The precise safety claim is: Context Projector preserves Phase 3 operation-scoped at-most-once terminal commit, idempotent terminal replay, commit-unknown reconciliation, delivery generation/owner fencing, and explicit compensation Operation boundaries. It does not claim exactly-once behavior across requests, processes, or external systems.

