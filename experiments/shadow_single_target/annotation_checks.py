"""Validate the frozen output field whitelist and structural state invariants, not semantics."""

from .mechanics import validate


def schema_check(value, schema):
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("enum")
    t = schema.get("type")
    if t == "string" and type(value) is not str:
        raise ValueError("string")
    if t == "integer" and (type(value) is not int or value < schema.get("minimum", value)):
        raise ValueError("integer")
    if t == "array":
        if type(value) is not list or not schema.get("minItems", 0) <= len(value) <= schema.get(
            "maxItems", len(value)
        ):
            raise ValueError("array")
        for x in value:
            schema_check(x, schema["items"])
    if t == "object":
        if type(value) is not dict or set(value) != set(schema["required"]):
            raise ValueError("fields")
        for k, v in value.items():
            schema_check(v, schema["properties"][k])


def validate_annotations(rows, inputs, schema):
    schema_check(rows, schema)
    if len({r["id"] for r in rows}) != len(rows) or {r["id"] for r in rows} != {
        r["id"] for r in inputs
    }:
        raise ValueError("ids")
    byid = {r["id"]: r for r in inputs}

    def state(r):
        gate = r["fact_gate"]
        ds = r["decision_state"]
        rel = r["relation_expectation"]
        delivery = r["deliverable_relation"]
        if gate != "eligible" and (rel is not None or delivery is not None):
            raise ValueError("noneligible_relation")
        if gate == "eligible" and ds not in {"decided", "blocked", "abstain"}:
            raise ValueError("eligible_state")
        if gate == "eligible" and ds == "decided" and rel is None:
            raise ValueError("decided_relation")
        expected = {
            "nonfactual": "not_executed",
            "ambiguous": "abstain",
            "out_of_scope": "abstain",
            "split_required": "split",
        }
        if gate in expected and ds != expected[gate]:
            raise ValueError("gate_state")
        if ds != "decided" or r["review_status"] != "resolved":
            if delivery is not None:
                raise ValueError("delivery_blocked")
        if delivery is not None:
            if rel != delivery or gate != "eligible":
                raise ValueError("delivery_relation")
            if any(c["mechanical"] != "pass" for c in r["citations"]):
                raise ValueError("mechanical_delivery")
            if delivery in {"supported", "contradicted"} and not r["citations"]:
                raise ValueError("evidence_required")
        if gate == "ambiguous" and len(r["alternatives"]) < 2:
            raise ValueError("alternatives")

    def check_mechanics(r, bound_input):
        mechanical = validate(bound_input)
        for index, c in enumerate(r["citations"]):
            m = (
                mechanical["citations"][index]
                if index < len(mechanical["citations"])
                else {"checks": {"unexecuted": "not_evaluated"}}
            )
            expected = (
                "fail"
                if "fail" in m["checks"].values()
                else ("not_evaluated" if "not_evaluated" in m["checks"].values() else "pass")
            )
            if c["mechanical"] != expected:
                raise ValueError("mechanical_claim")
        # Single-target adapter clarification: mechanical failure is blocked, not semantic abstention.
        if (
            r["fact_gate"] == "eligible"
            and not mechanical["protocol_complete"]
            and r["decision_state"] != "blocked"
        ):
            raise ValueError("mechanical_requires_blocked")
        if r["deliverable_relation"] is not None and not mechanical["protocol_complete"]:
            raise ValueError("mechanical_delivery")

    for r in rows:
        state(r)
        source = byid[r["id"]]
        t = source["target_binding"]
        quotes = [{"source_id": c["source_id"], "quote": c["quote"]} for c in r["citations"]]
        if quotes != source["candidate_citations"]:
            raise ValueError("candidate_quotes_changed")
        check_mechanics(r, source)
        if bool(r["children"]) != (r["fact_gate"] == "split_required"):
            raise ValueError("children_gate")
        for i, c in enumerate(r["children"], 1):
            a, b = c["target_span"]
            if (
                not t["start_codepoint"] <= a < b <= t["end_codepoint"]
                or t["draft"][a:b] != c["target"]
            ):
                raise ValueError("child_mapping")
            for a, b in c["shared_scope_spans"]:
                if not t["start_codepoint"] <= a < b <= t["end_codepoint"]:
                    raise ValueError("scope_mapping")
            if (
                c["assessment"]["id"] != r["id"] + "." + str(i)
                or c["assessment"]["fact_gate"] == "split_required"
            ):
                raise ValueError("child_id_or_nested_split")
            state(c["assessment"])
            child_quotes = [
                {"source_id": q["source_id"], "quote": q["quote"]}
                for q in c["assessment"]["citations"]
            ]
            if any(q not in source["candidate_citations"] for q in child_quotes):
                raise ValueError("child_quote_changed")
            child_input = {
                **source,
                "target": c["target"],
                "target_binding": {
                    **t,
                    "start_codepoint": c["target_span"][0],
                    "end_codepoint": c["target_span"][1],
                },
                "candidate_citations": child_quotes,
            }
            check_mechanics(c["assessment"], child_input)
    return {
        "status": "OUTPUT_STRUCTURE_ONLY",
        "semantic_assessment": "not_evaluated",
        "rows": len(rows),
    }
