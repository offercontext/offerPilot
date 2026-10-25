# Companion bounded corrective experience validation

This is a Linux-only acceptance harness, not an installed product feature or a
second agent. It uses a fresh owned database, the real loopback `/api/chat`
endpoint, twelve fixed synthetic ordinary turns and real bounded companion
commands. No existing user configuration, account or record is loaded.

## Evidence boundary

The driver simulates a week with a child-only companion clock. It checks real
waiting, choice provenance, pause/gap behavior, mixed work priority, boat/cloud
recall, ambiguity, source deletion, withdrawal, quiet/off guards and unchanged
business records. Deterministic command receipts are not provider tone samples.

`passed` with `pass_scope=mechanism_only` does not mean the persona passed. Human
review must inspect the twelve successful ordinary replies for natural brevity,
playfulness without forced maturity or care debt, accurate work uncertainty,
source-grounded continuity and no invented external action. Any fabricated
shared/tool fact, restored deleted memory, guilt or work-approval bypass is a
hard failure. Each remaining applicable dimension is scored 0–2; require no
zero for provenance/work/non-pressure and mean at least 1.5. No template growth
or new product settings substitute for this review.

`expected_context` now records the exact natural reference text visible to the
model (older frozen artifacts retain their original JSON-object representation).
It is not the whole provider request; historical paid artifacts must not be
represented as full payload captures.
`source_facts_before_projection` may contain optional real fixture facts omitted
by the production 2 KiB context cap; do not grade a model as having seen them.
Successful bounded samples are fsynced incrementally to `responses.jsonl`, so a
later failure does not discard earlier paid evidence. Summary and ledger contain
fixed errors, never provider error bodies or credentials. All retained dialogue
and state are synthetic. Production request messages and 26 tool schemas remain
intact; no tools are executed by this harness.

## Free synthetic request inspection

Run `python -m scripts.capture_companion_synthetic_requests --fake-only --label final`
from the repository's frozen environment. This separate fixture captures complete
raw request bodies and parsed messages, schemas and parameters at a loopback fake
upstream for the original twelve cases plus seven held-out cases. It uses freshly
owned data directories, discards inherited configuration without reading credential
values, has no real endpoint/data/config/output-path options, and never retains
headers. The printed temporary evidence directory is synthetic-only. Do not turn
this into production request logging or use it with real user data or secrets.

The V1.6 product compiler retains source identity, revision and a hash of the full
raw snapshot as non-model metadata, while sending short natural facts without
record IDs, clocks or state enums. Work and ordinary dialogue use the same persona,
execution policy and tools. V1.7 defaults ordinary workspace/general turns to
neutral expression without fixed report headings, rather than classifying them
as leisure. Page facts, attachments, work history and companion retrieval misses
do not themselves select a report format. Complex work and user-requested lists
remain allowed. Explicit application/task modes and pending/confirmation/
clarification retain their work protocol and unchanged authority. Untyped read-only
work can have fewer structured conclusion/action cards; its full text and actual
tool-result cards remain available through the existing UI fallback.
No generated-output filter, canned recall reply, new planner or extra model call
is added. These tests establish exposure and source boundaries, not naturalness
or a guarantee against invented participation. A new real comparison remains
separately authorized; the consumed third batch cannot be rerun or reset.

## Fixed third-batch paid boundary

- Independent environment: `offerpilot-companion-ai-20261021`.
- Dedicated environment secret: `OFFERPILOT_COMPANION_DEEPSEEK_20261021`.
- Environment variable: `OFFERPILOT_COMPANION_AI_MODEL=deepseek-flash`.
- Fixed official TLS host `api.deepseek.com`, path `/chat/completions`.
- Separate task-total cap: RMB20, including failed and unknown attempts.
- One second corrective batch (third batch overall): exactly twelve ordered slots, at most one actual HTTP
  request per slot, no retries, SDK retries, format repair, fallback or automatic
  extra batch. The prior twenty-four replies are retained evidence, not replayed as
  fresh results. Budget or safety failure can stop the batch before all slots finish.
- Explicit thinking disabled and 512 output-token cap. The production client uses
  streaming; the broker makes one non-streaming upstream request and exposes local
  SSE only after complete model/content/usage/tool validation and durable settlement.
  A tool-bearing response is rejected before any text reaches product execution.
- Integer micro-CNY accounting: peak cache-miss input RMB2/million, output
  RMB8/million, reverified on 2026-10-24 against the [official price page](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/).
  [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) defaults on,
  so disabling it is explicit. The reviewed quote expires at 2026-10-26 00:00 UTC;
  code must not silently extend it. This is the same activity and cumulative
  RMB20 budget, not another allocation.
- Before each actual HTTP request, fsync a RMB3 reservation. The documented
  1,048,576-token context and 512 output cap cost less than this even at peak rates.
  Trusted, consistent usage alone releases the difference. Unknown status, usage,
  timeout, concurrent/duplicate requests or tampered journal retain liability and
  halt. Monotonic request/session deadlines and wall/monotonic price windows are
  checked after fsync and TLS connection, immediately before sending HTTP. No proxy,
  redirect or custom TLS.
- Journals cannot reopen/reset. Only the two exact verified prior paid runs below
  are historical exceptions. Any other non-skipped paid job, rerun, missing history
  or recreated pinned workflow/environment/policy blocks. Once this corrective paid
  job starts, success, failure or cancellation consumes this one continuation.

## Two historical batches and cumulative continuation

The original [run 39736858377](https://github.com/offercontext/offerPilot/actions/runs/39736858377)
completed twelve requests and structural checks, but failed independent tone and
fact review. V1.4 corrected work-report formatting in daily chat, progress indexes
and ambiguous history handling. The first corrective
[run 39746585328](https://github.com/offercontext/offerPilot/actions/runs/39746585328)
also completed twelve requests. Ten replies passed review; case 10 still invented
joint folding from virtual activity candidates, and case 12 explained boundaries
mechanically. V1.5 separates activity facts from proven user participation and
asks for brief honest capability answers. It has not passed a fresh real-model
review. Free assertions and browser scenarios are not proof of natural model tone.

The two conservative validated-usage totals are **103,154 + 105,362 = 208,516
micro-CNY (RMB0.208516)** across **24 actual requests**, with no outstanding or
unknown reservation. The new broker inherits **208,516**, not the second batch's
105,362 alone, and does not add the original 103,154 twice. The same task cap is
**20,000,000 micro-CNY**. Initial remaining allowance is **19,791,484**; the first
RMB3 reservation makes total liability **3,208,516**. New known usage accumulates
on that total; unknown results retain the whole reservation and halt. These are
conservative validated-usage amounts, not a provider invoice. Twelve new requests
would make 36 lifetime requests, not create another budget.

Both fixed synthetic artifacts are bundled and hash-pinned. Every verification
re-reads both archives and strictly replays the original schema-1 ledger and the
correction schema-2 ledger, including its original ordinal-1 lineage and old
2026-10-24 quote expiry. The first close must match the second opening. Every
reserve/settle/complete transition, source identity, usage calculation, response
and summary must agree. A terminal `closed.halted=true` is normal closure only
when every preceding record is fully settled and unhalted. Missing or changed
bytes, incorrect lineage, an actual halt or uncertain liability fail closed.
Historical quotes and operation identities are not rewritten to match this run.

Current admission schema 2 has fixed correction ordinal **2** and links the new
exact H/R to the immediate preceding correction. Both preflight phases validate
the complete history, both historical H/R same-tree/parent identities, their
helper runs, artifacts and owner reviews. The new paid phase additionally needs
fresh owner approval; prior approval never authorizes another run. All four old
H/R SHAs are rejected as a new helper or request. Only the two pinned historical
paid successes are allowed; every other non-skipped paid run blocks, including
failure or cancellation. There is no override for balance, predecessor, ordinal,
operation list or budget, and no general continuation framework or fourth batch.

The twelve existing prompts and operation IDs remain unchanged for comparison,
particularly failed cases 10 and 12. The driver still uses actual production chat
and bounded state commands, not canned replies. No new persona settings or
product behavior are introduced by this safety delta.

The workflow, environment, key, concurrency identity and reviewer protections are
unchanged. The refreshed fixed candidate deadline is **2026-10-26 00:00 UTC**;
its publication alone cannot authorize calls. Before pushing the same-tree request,
obtain explicit approval for this one third batch and configure the new exact
repository H/R variables. Until both are confirmed, publish only the ordinary
helper commit and do not push the request. A subsequent push must still wait for
the owner's new protected-environment review. Never approve for the owner or
rerun the paid job. Independently review all actual replies and cumulative ledger
before claiming the experience passed. Expiry requires a new reviewed price and
fixed deadline, never a rolling extension.

The earlier 48k input-token figure was a planning estimate, not the budget gate.
Actual production schemas/history are not silently trimmed to meet that guess.
Fake-run request byte sizes are measurable; actual tokens are established only
by the provider's validated usage. The conservative monetary reservation is the
hard bound before every send.

## Safe publication and approval

The initial unarmed publication used zero binding IDs. Its successful registration
run [39632913549](https://github.com/offercontext/offerPilot/actions/runs/39632913549)
verified workflow `391917473`, environment `24931188785` and branch policy
`63853345`; this helper pins those identities. The offline job reads only public
protection metadata and its own workflow registration, emitting bounded IDs. It
never reads secret management endpoints. Failure to read/verify metadata blocks,
not implies absent. Pinning alone does not authorize or trigger a paid request.

The independently reviewed helper H must pass its complete offline workflow.
A request R must be H's single-parent, same-tree empty
child, with the exact marker from `preflight.marker(H)`. Repository variables
`OFFERPILOT_COMPANION_AI_HELPER_SHA` and
`OFFERPILOT_COMPANION_AI_REQUEST_SHA` must bind H/R before R is pushed.

Only `feat/20261021-companion-life-v1` is allowed. The dedicated environment must
require `XiaoZheBrother`, disallow admin bypass, and allow that owner to review
own pushes. Before and after environment approval, preflight verifies current
SHA, event and full run/job history; the paid phase additionally verifies the
new run's exact owner approval. Secret-free
preflight also requires a full twenty-minute session remaining in the reviewed
quote. The same wall/monotonic window spans event and metadata reads, admission
open/flush/fsync and final success. Expired or too-late requests fail without
authorizing the provider-secret step, even if an admission write stalled.
The paid admission carries monotonic freshness and non-extending quote anchors
into the live process on the same Linux job. Step delays, evidence replay and
checkout verification cannot renew either window, including after wall rollback;
the transport and broker retain the earlier quote bound.
Registration-only
inspection remains available after expiry; it cannot authorize any request.
Read-only Actions
metadata tokens are never passed to the product. The provider key appears in one
paid step only, then remains solely inside the fixed upstream transport; the
child process receives an allowlisted clean environment and a short-lived
loopback capability. Installing dependencies and offline tests have no provider
secret. No PR, release, dispatch, browser/login, external account or tool action.

## Offline tests

Run on Linux, with the repository's normal loopback-only pytest guard:

```sh
uv run python -m scripts.companion_ai_validation.test_gate
uv run pytest -q tests/test_companion_ai_platform_contract.py
uv run ruff check scripts/companion_ai_validation tests/test_companion_ai_platform_contract.py
uv run mypy scripts/companion_ai_validation/*.py --follow-imports=silent --explicit-package-bases
```

The Linux gate requires every collected node to finish setup/call/teardown and
pass, with no skips, deselection, duplication or missing nodes; it writes
`artifacts/companion-ai-offline.json`. Linux durability/real-process tests live
outside the default production `tests/` collection. Default Windows/macOS tests
retain an explicit early platform-rejection contract; durability is never a
no-op or silently skipped safety check. Live entrypoints reject unsupported
platforms before reading admission/key, creating evidence or sending network.
