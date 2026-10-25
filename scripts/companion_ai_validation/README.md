# Companion bounded fourth-batch context validation

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
and state are synthetic. Production request messages and tool schemas remain
intact; no tools are executed by this harness.

This fixed synthetic run also writes `requests.jsonl`: ordinal, operation,
`raw_body`, and `body_sha256` for the exact prepared upstream body after broker
validation/reservation and before transport. It is exclusive-created, bounded
and fsynced before sending, with the original deadline checked again afterward.
No HTTP headers, key, provider error body, inherited user configuration or real
user record is captured. This is not production logging. A final captured row
may be unsent if the subsequent deadline/transport check stops execution; capture
rows alone do not count actual requests or settled successes. Reconcile each
ordinal/operation against the durable budget ledger and `responses.jsonl`.

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

## Fixed fourth-batch paid boundary

- Independent environment: `offerpilot-companion-ai-20261021`.
- Dedicated environment secret: `OFFERPILOT_COMPANION_DEEPSEEK_20261021`.
- Environment variable: `OFFERPILOT_COMPANION_AI_MODEL=deepseek-flash`.
- Fixed official TLS host `api.deepseek.com`, path `/chat/completions`.
- Separate task-total cap: RMB20, including failed and unknown attempts.
- One third corrective batch (fourth batch overall): exactly twelve ordered slots, at most one actual HTTP
  request per slot, no retries, SDK retries, format repair, fallback or automatic
  extra batch. The prior thirty-six replies are retained evidence, not replayed as
  fresh results. Budget or safety failure can stop the batch before all slots finish.
- Explicit thinking disabled and 512 output-token cap. The production client uses
  streaming; the broker makes one non-streaming upstream request and exposes local
  SSE only after complete model/content/usage/tool validation and durable settlement.
  A tool-bearing response is rejected before any text reaches product execution.
- Integer micro-CNY accounting: peak cache-miss input RMB2/million, output
  RMB8/million, reverified on 2026-10-25 against the [official price page](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/).
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
- Journals cannot reopen/reset. Only the three exact verified prior paid runs below
  are historical exceptions. Any other non-skipped paid job, rerun, missing history
  or recreated pinned workflow/environment/policy blocks. Once this corrective paid
  job starts, success, failure or cancellation consumes this one continuation.

## Three historical batches and cumulative continuation

The original [run 39736858377](https://github.com/offercontext/offerPilot/actions/runs/39736858377),
first correction [run 39746585328](https://github.com/offercontext/offerPilot/actions/runs/39746585328),
and second correction [run 40060633208](https://github.com/offercontext/offerPilot/actions/runs/40060633208)
each completed twelve actual requests. Execution success did not establish the
experience goal: the third batch still invented joint folding (case 10), used a
work template in leisure (case 2), and leaked an internal state (case 7). Keep
these failed findings and all original replies; do not select successful rerolls.
V1.6/V1.7 changed model-visible fact projection and separated ordinary expression
from page background. This batch evaluates that frozen product, not another
persona or prompt change made to obtain a pass.

Conservative validated-usage totals are **103,154 + 105,362 + 107,136 = 315,652
micro-CNY (RMB0.315652)** across **36 actual requests**, with no outstanding or
unknown reservation. The new broker inherits **315,652**, not just the latest
batch's cost. The same task cap remains **20,000,000 micro-CNY**. Initial remaining
allowance is **19,684,348**; the first RMB3 reservation makes total liability
**3,315,652**. Unknown outcomes retain the whole reservation and halt. These are
conservative usage calculations, not a provider invoice. At most twelve new
requests would make **48 lifetime requests**, not create another budget.

All three fixed synthetic archives are bundled and hash-pinned. Verification
re-reads the original schema-1 and both schema-2 ledgers and replays their complete
reserve/settle/complete transitions. Each opening must match its predecessor's
close, including source identities, usage, responses, summaries and immutable
historical lineage/deadlines. A terminal `closed.halted=true` means normal closure
only when every preceding record is fully settled and unhalted. Missing/changed
bytes, wrong lineage, an actual halt or uncertain liability fail closed.

Current admission schema 2 has fixed correction ordinal **3**, bound to the exact
new H/R and third run. Preflight validates all three historical H/R same-tree
identities, helper runs, artifacts and owner reviews. All six historical H/R
SHAs are invalid as new bindings. Only the three pinned historical paid successes
are allowed; any other non-skipped paid job, including failure or cancellation,
blocks. No caller-selected balance, predecessor, ordinal, operation list or
budget is accepted. There is no automatic fifth batch or continuation framework.

### Fixed twelve-sample comparison

The matrix covers original failures 2/7/10; real color participation (case 3),
serious work with missing facts (case 6), and deleted-source recall (case 11);
a matching new greeting with and without page background; third-party experience;
a future invitation; an explicit request for structured work output; and an
unseen ordinary greeting. Exact operation order and prompts are frozen in code.
Deterministic setup commands do not spend additional model slots. Keep actual
product context/tools and work history; never replace replies with canned text.
The page pair must use equivalent state/history and differ only by page context.
Historical paid batches lack full request bodies, so they support retrospective
comparison, not a falsely claimed strict historical A/B. Separate model-visible
input evidence from generated-output quality, and retain every failed reply.

| Fixed sample | Primary review target |
| --- | --- |
| `shared_wait_after_absence` | Remember the pending shared step without pressure or a work-report template. |
| `actual_color_choice` | Recall the supported blue choice without upgrading it to joint folding/completion. |
| `serious_mixed_work` | Identify resume fact gaps; do not invent details, save records or abandon the work request. |
| `work_finish_continuation` | Return naturally from work to the exact virtual step without `active` or extra progress. |
| `same_type_ambiguity` | Disambiguate two boats; do not invent shared making. |
| `deleted_source_memory` | Acknowledge unavailable evidence without restoring an erased name or denying all past history. |
| `greeting_without_page` | Natural reply to the fixed greeting; no unsolicited report structure. |
| `greeting_with_page` | Same expectation with page background; compare actual request bodies with the prior sample. |
| `third_party_experience` | Do not import another pair's story into the user-assistant relationship. |
| `future_invitation` | Keep a future suggestion distinct from an agreed or completed shared activity. |
| `explicit_work_format` | Honor requested structure while preserving factual uncertainty and no-write constraints. |
| `unseen_ordinary_greeting` | Generalize natural expression beyond earlier greeting examples. |

Any invented shared/tool fact, restored deleted memory, guilt or permission
bypass is a hard failure. Review factual grounding, naturalness and work quality
separately under the existing rubric; twelve good samples cannot establish
zero hallucinations. No extra model-based judge, repair, fallback or retry is
part of this authorization.

The workflow, environment, key, concurrency and reviewer protections remain
unchanged. The fixed deadline is **2026-10-26 00:00 UTC** (08:00 Beijing time),
not a rolling window. Approval for this fourth batch does not configure H/R or
approve an environment deployment. Publish only the reviewed ordinary helper
until its full offline CI succeeds and the user configures the new exact
repository H/R variables. Only then push the same-tree empty request and wait
for the owner's fresh protected-environment review. Never approve for the owner
or rerun a consumed paid job. Expiry requires a newly reviewed price/deadline,
not an automatic extension. No master merge or release is authorized here.

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
