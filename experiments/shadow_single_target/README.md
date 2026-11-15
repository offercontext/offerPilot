# Single-target shadow v0.2

Isolated experimental protocol `single-target-shadow-v0.2-1`, not a product schema or
release acceptance. The prior offline adapter is imported with relative imports,
new version/mode text and a single-target live experiment wrapper. No old response
allowlist is reused. Exact original inputs and provider payloads are frozen in
`assets/`; raw transport envelopes are durably retained by the existing Broker.
The version plus the exact local input bytes bind an opaque request ID. Responses
echo the ID, not a model-calculated hash. A changed revision rejects stale answers.

## Preregistered evaluation (before any provider call)

These are previously seen/teaching synthetic protocol and gate smoke examples, not
unseen generalization or blind evaluation. Six targets are selected from the v0.2
additional-input set, without gold
labels in the provider payload or parser. IDs are opaque; operation order is fixed.

| Operation | Original synthetic item | Evaluation question |
|---|---|---|
| t01 | NEW05 | Is an existing conditional rule adjudicated without asserting its antecedent or consequence occurred? |
| t02 | NEW03 | Does an immediate conditional commitment preserve scope without asserting either event? |
| t03 | NEW04 | Is a reported past commitment distinguished from the truth of its embedded events? |
| t04 | NEW06 | Is a clear fact with absent evidence kept separate from ambiguity or contradiction? |
| t05 | NEW07 | Is an unrealized definite future prediction kept outside present factual adjudication? |
| t06 | NEW08 | Are mixed fact/invitation clauses faithfully split with original spans and scope? |

Mechanical protocol success requires six one-shot responses with valid envelopes,
matching correlation IDs, exact target/source binding, valid structural states,
and independently recomputed citation mechanics. It does not require any expected
semantic label. Different legal interpretations may both pass the parser; the separate
host-side conservative semantic stopping rule can nevertheless stop the batch.

Semantic review is a separate human/independent review of all six raw responses:
record the gate, interpretation, alternatives, tags, conditional scope, B relation,
evidence relevance/entailment/sufficiency and split fidelity. Report disagreements
and denominators; do not collapse old unknown into insufficient, or equate a valid
quote with support. Failed mechanics, no relation executed, abstention and evidence
insufficiency remain distinct. No claims of accuracy or product readiness follow
from six samples or from structurally valid output. Do not score fluent prose.

Each target permits exactly one deepseek-flash request, maximum 4096 output tokens,
with A/B in that same response. No repair, retry, continuation or tool invocation.
The existing protected workflow, lineage, price window and durable budget controls
are mandatory; this directory alone confers no provider authorization.

`semantic-preregistration.json` freezes host-only gate/relation expectations; no
expectations enter provider payloads. Split-child comparisons align exact preregistered target-span tuples, not response-array order.
For t06 only, two finite variants allow the separator comma in the first child or
in neither child: `[0,9]+[9,18]` and `[0,8]+[9,18]`. No factual word may be
dropped; no generic trim or fuzzy matching is performed;
raw output order and child IDs are retained unchanged. After structure validation, a mismatch is
`semantic_disagreement` and immediately stops remaining requests. This conservative
stopping rule is not a correctness oracle: the response remains structurally valid
and ungraded, and requires independent adjudication. No expectation may be loosened
after seeing results. Normal nonfactual, abstain and insufficient values are valid
states; only disagreement with that target's predeclared semantics stops execution.
Transport, finish reason, usage, schema/binding or citation-mechanics failure also
stops immediately. Neither a match nor all six matches establishes semantic quality.
