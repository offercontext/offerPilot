import copy
import unittest
from experiments.shadow_single_target.adapter import (
    AdapterError,
    FakeRecorder,
    MAX_INPUT_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    Request,
    VERSION,
    build_request,
    decode,
    encode,
    input_row,
    offline_roundtrip,
    parse_response,
    sha,
)


def fixture():
    target = "你选了蓝色。现在折船吧。"
    source = "记录：你选了蓝色。"
    return {
        "id": "item_0123456789abcdef",
        "target": target,
        "target_binding": {
            "draft": target,
            "start_codepoint": 0,
            "end_codepoint": len(target),
            "sha256_utf8": sha(target.encode()),
        },
        "source_context": {
            "origin": "synthetic_reference_record",
            "relative_date_anchor": "2026-10-30",
            "evaluation_time": "now",
            "addressee": "current_user",
        },
        "sources": [{"id": "S1", "text": source, "sha256_utf8": sha(source.encode())}],
        "candidate_citations": [{"source_id": "S1", "quote": "你选了蓝色。"}],
    }


def assessment(row):
    return {
        "id": row["id"],
        "fact_gate": "eligible",
        "surface_form_tags": ["declarative"],
        "speech_act_tags": [],
        "conditionality": "unspecified",
        "tag_uncertainty": "",
        "interpretation": "固定目标，待语义复核",
        "alternatives": [],
        "relation_expectation": "insufficient",
        "deliverable_relation": None,
        "decision_state": "decided",
        "reason": "人工复核材料范围",
        "citations": [
            {
                **q,
                "mechanical": "pass",
                "relevance": "待复核",
                "entailment": "待复核",
                "sufficiency": "待复核",
            }
            for q in row["candidate_citations"]
        ],
        "review_status": "unresolved",
        "children": [],
    }


def response(req, a=None):
    row = input_row(req.original_input)
    return {"version": VERSION, "request_id": req.request_id, "assessment": a or assessment(row)}


class Tests(unittest.TestCase):
    def setUp(self):
        self.row = fixture()
        self.raw = encode(self.row)
        self.req = build_request(self.raw)
        self.out = response(self.req)

    def parse(self, value):
        return parse_response(encode(value), self.req)

    def rejected(self, change):
        change(self.out)
        with self.assertRaises(AdapterError):
            self.parse(self.out)

    def test_roundtrip_one_call_preserves_bytes(self):
        raw = encode(self.out)
        fake = FakeRecorder(raw)
        r = offline_roundtrip(self.raw, fake)
        self.assertEqual(r["status"], "structurally_valid")
        self.assertTrue(r["semantic_review_required"])
        self.assertEqual(fake.calls, [self.req.body])
        self.assertEqual(r["evidence"]["response_bytes"], raw)
        self.assertEqual(r["evidence"]["input_bytes"], self.raw)

    def test_request_has_exact_input_and_hash_no_gold(self):
        raw = b"  " + self.raw + b"\n"
        req = build_request(raw)
        r = decode(req.body, MAX_REQUEST_BYTES)
        self.assertEqual(req.original_input, raw)
        self.assertEqual(req.input_sha256, sha(raw))
        self.assertNotIn("original_input_utf8", r)
        self.assertNotIn("input_sha256_utf8", r)
        self.assertEqual(r["input"], self.row)
        self.assertNotIn("gold", r)
        self.assertNotIn("host", r)
        self.assertEqual(r["response_count"], 1)

    def test_different_semantic_claims_both_pass_not_gold_match(self):
        first = self.parse(self.out)
        self.out["assessment"].update(
            fact_gate="nonfactual",
            decision_state="not_executed",
            relation_expectation=None,
            interpretation="仅作提议的另一读法",
            speech_act_tags=["proposal"],
        )
        second = self.parse(self.out)
        self.assertNotEqual(first["reviewer_claims"], second["reviewer_claims"])
        self.assertTrue(second["semantic_review_required"])

    def test_valid_quote_does_not_force_supported(self):
        self.assertEqual(
            self.parse(self.out)["reviewer_claims"]["relation_expectation"], "insufficient"
        )

    def test_truncation_retained_no_retry(self):
        raw = encode(self.out)[:-3]
        fake = FakeRecorder(raw)
        r = offline_roundtrip(self.raw, fake)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(r["evidence"]["response_bytes"], raw)
        self.assertEqual(r["evidence"]["response_sha256"], sha(raw))

    def test_duplicate_nested_keys(self):
        raw = encode(self.out).replace(
            b'"fact_gate":"eligible"', b'"fact_gate":"eligible","fact_gate":"nonfactual"'
        )
        with self.assertRaisesRegex(AdapterError, "duplicate_key"):
            parse_response(raw, self.req)

    def test_wrong_id(self):
        self.rejected(lambda o: o["assessment"].update(id="item_ffffffffffffffff"))

    def test_extra_hash(self):
        self.rejected(lambda o: o.update(input_sha256_utf8="0" * 64))

    def test_wrong_target(self):
        self.rejected(lambda o: o.update(target="另一个目标"))

    def test_extra_binding(self):
        self.rejected(lambda o: o.update(target_binding=self.row["target_binding"]))

    def test_bool_offset(self):
        self.row["target_binding"]["start_codepoint"] = False
        with self.assertRaises(AdapterError):
            build_request(encode(self.row))

    def test_illegal_state(self):
        self.rejected(lambda o: o["assessment"].update(fact_gate="nonfactual"))

    def test_unknown_not_mapped(self):
        self.rejected(lambda o: o["assessment"].update(relation_expectation="unknown"))

    def test_extra_tool_field(self):
        self.rejected(lambda o: o.update(tool_calls=[]))

    def test_nested_extra_field(self):
        self.rejected(lambda o: o["assessment"]["citations"][0].update(tool="execute"))

    def test_quote_replacement(self):
        self.rejected(lambda o: o["assessment"]["citations"][0].update(quote="不存在"))

    def test_invalid_frozen_quote_false_pass_rejected_but_blocked_preserved(self):
        self.row["candidate_citations"][0]["quote"] = "不存在"
        req = build_request(encode(self.row))
        out = response(req)
        with self.assertRaises(AdapterError):
            parse_response(encode(out), req)
        out["assessment"].update(decision_state="blocked")
        out["assessment"]["citations"][0]["mechanical"] = "fail"
        result = parse_response(encode(out), req)
        self.assertFalse(result["mechanical_report"]["protocol_complete"])
        self.assertIsNone(result["reviewer_claims"]["deliverable_relation"])

    def split(self):
        child = assessment(self.row)
        child.pop("children")
        child["id"] = self.row["id"] + ".1"
        self.out["assessment"].update(
            fact_gate="split_required",
            decision_state="split",
            relation_expectation=None,
            children=[
                {
                    "target": self.row["target"][:6],
                    "target_span": [0, 6],
                    "shared_scope_spans": [],
                    "assessment": child,
                }
            ],
        )

    def test_valid_split(self):
        self.split()
        self.parse(self.out)

    def test_fake_child(self):
        self.split()
        self.rejected(lambda o: o["assessment"]["children"][0].update(target="虚构子项"))

    def test_child_wrong_id(self):
        self.split()
        self.rejected(lambda o: o["assessment"]["children"][0]["assessment"].update(id="fake"))

    def test_child_bad_scope(self):
        self.split()
        self.rejected(
            lambda o: o["assessment"]["children"][0].update(shared_scope_spans=[[0, 999]])
        )

    def test_nested_split(self):
        self.split()
        self.rejected(
            lambda o: o["assessment"]["children"][0]["assessment"].update(
                fact_gate="split_required", decision_state="split", relation_expectation=None
            )
        )

    def test_bad_utf8_and_surrogates(self):
        checked = 0
        for raw in [b"\xff", b'{"x":"\\ud800"}', b"NaN", b"1e999", b"[] []", b"\xef\xbb\xbf{}"]:
            with self.assertRaises(AdapterError):
                parse_response(raw, self.req)
            checked += 1
        self.assertEqual(checked, 6)

    def test_size_limits(self):
        for raw, fn in [
            (b" " * (MAX_INPUT_BYTES + 1), build_request),
            (b" " * (MAX_RESPONSE_BYTES + 1), lambda r: parse_response(r, self.req)),
        ]:
            with self.assertRaisesRegex(AdapterError, "size_limit") as ctx:
                fn(raw)
            self.assertEqual(ctx.exception.raw, raw)
            self.assertEqual(ctx.exception.sha256, sha(raw))

    def test_bad_input_no_call_retained(self):
        raw = b'{"a":1,"a":2}'
        fake = FakeRecorder(b"{}")
        r = offline_roundtrip(raw, fake)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(fake.calls, [])
        self.assertEqual(r["evidence"]["input_bytes"], raw)

    def test_bad_input_source_hash(self):
        self.row["sources"][0]["sha256_utf8"] = "0" * 64
        with self.assertRaises(AdapterError):
            build_request(encode(self.row))

    def test_fake_exchange_failure(self):
        fake = FakeRecorder(None, "simulated")
        r = offline_roundtrip(self.raw, fake)
        self.assertEqual(r["error_code"], "fake_exchange_failure")
        self.assertEqual(len(fake.calls), 1)
        self.assertIsNone(r["evidence"]["response_bytes"])

    def test_transport_rejected(self):
        with self.assertRaises(TypeError):
            offline_roundtrip(self.raw, lambda x: b"{}")

    def test_tampered_request(self):
        with self.assertRaisesRegex(AdapterError, "request_binding_mismatch"):
            parse_response(encode(self.out), Request(self.raw, b"changed"))

    def test_trusted_binding_is_local_not_model_echo(self):
        result = self.parse(self.out)
        self.assertEqual(
            result["trusted_target_context"]["target_binding"], self.row["target_binding"]
        )
        self.assertEqual(result["input_sha256"], sha(self.raw))

    def test_nonfactual_invalid_candidate_not_executed(self):
        self.row["candidate_citations"][0]["quote"] = "不存在"
        req = build_request(encode(self.row))
        out = response(req)
        out["assessment"].update(
            fact_gate="nonfactual", decision_state="not_executed", relation_expectation=None
        )
        out["assessment"]["citations"][0]["mechanical"] = "fail"
        result = parse_response(encode(out), req)
        self.assertFalse(result["mechanical_report"]["protocol_complete"])
        self.assertEqual(result["reviewer_claims"]["decision_state"], "not_executed")

    def test_same_id_changed_context_rejects_stale_response(self):
        for change in ("context", "source"):
            row = copy.deepcopy(self.row)
            if change == "context":
                row["source_context"]["evaluation_time"] = "later"
            else:
                row["sources"][0]["text"] += " 修订。"
                row["sources"][0]["sha256_utf8"] = sha(row["sources"][0]["text"].encode())
            revised = build_request(encode(row))
            self.assertNotEqual(revised.request_id, self.req.request_id)
            with self.assertRaisesRegex(AdapterError, "response_correlation"):
                parse_response(encode(self.out), revised)
            parse_response(encode(response(revised)), revised)

    def test_eligible_mechanical_failure_abstain_rejected(self):
        self.row["candidate_citations"][0]["quote"] = "不存在"
        req = build_request(encode(self.row))
        out = response(req)
        out["assessment"].update(decision_state="abstain", relation_expectation=None)
        out["assessment"]["citations"][0]["mechanical"] = "fail"
        with self.assertRaisesRegex(AdapterError, "mechanical_requires_blocked"):
            parse_response(encode(out), req)


if __name__ == "__main__":
    unittest.main()
