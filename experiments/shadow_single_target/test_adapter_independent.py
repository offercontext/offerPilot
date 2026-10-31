import copy
import unittest
from experiments.shadow_single_target.adapter import (
    AdapterError,
    FakeRecorder,
    MAX_RESPONSE_BYTES,
    build_request,
    encode,
    offline_roundtrip,
    parse_response,
    sha,
)
from experiments.shadow_single_target.test_adapter import fixture, assessment, response


class Independent(unittest.TestCase):
    def parse(self, row, a=None):
        req = build_request(encode(row))
        out = response(req, a)
        return parse_response(encode(out), req)

    def test_nonfactual_bad_quote(self):
        row = fixture()
        row["candidate_citations"][0]["quote"] = "absent"
        a = assessment(row)
        a.update(fact_gate="nonfactual", decision_state="not_executed", relation_expectation=None)
        a["citations"][0]["mechanical"] = "fail"
        r = self.parse(row, a)
        self.assertFalse(r["mechanical_report"]["protocol_complete"])

    def test_zero_quote_insufficient(self):
        row = fixture()
        row["candidate_citations"] = []
        a = assessment(row)
        a.update(review_status="resolved", deliverable_relation="insufficient")
        self.parse(row, a)

    def test_zero_quote_supported_rejected(self):
        row = fixture()
        row["candidate_citations"] = []
        a = assessment(row)
        a.update(
            review_status="resolved",
            relation_expectation="supported",
            deliverable_relation="supported",
        )
        with self.assertRaises(AdapterError):
            self.parse(row, a)

    def test_source_limits(self):
        checked = 0
        for text in ["", " ", "x" * 4097]:
            row = fixture()
            row["sources"][0].update(text=text, sha256_utf8=sha(text.encode()))
            with self.assertRaises(AdapterError):
                build_request(encode(row))
            checked += 1
        self.assertEqual(checked, 3)

    def test_duplicate_source(self):
        row = fixture()
        row["sources"] *= 2
        with self.assertRaises(AdapterError):
            build_request(encode(row))

    def test_5_citations(self):
        row = fixture()
        row["candidate_citations"] *= 5
        with self.assertRaises(AdapterError):
            build_request(encode(row))

    def test_bad_quotes_report_fail(self):
        checked = 0
        for text, quote in [
            ("aaaa", "aa"),
            ("e\u0301", "e"),
            ("abc", " " * 2),
            ("x" * 129, "x" * 129),
            ("é", "e\u0301"),
        ]:
            row = fixture()
            row["sources"][0].update(text=text, sha256_utf8=sha(text.encode()))
            row["candidate_citations"][0]["quote"] = quote
            a = assessment(row)
            a["decision_state"] = "blocked"
            a["citations"][0]["mechanical"] = "fail"
            self.assertFalse(self.parse(row, a)["mechanical_report"]["protocol_complete"])
            checked += 1
        self.assertEqual(checked, 5)

    def test_parent_citation_drop_rejected(self):
        row = fixture()
        a = assessment(row)
        a["citations"] = []
        with self.assertRaises(AdapterError):
            self.parse(row, a)

    def test_failure_evidence(self):
        row = fixture()
        raw = encode(row)
        for r in [
            b"\xff",
            b'{"x":1,"x":2}',
            b" " * (MAX_RESPONSE_BYTES + 1),
            b'{"x":"\\udfff"}',
            b"[" * 100 + b"]" * 100,
        ]:
            f = FakeRecorder(r)
            out = offline_roundtrip(raw, f)
            self.assertEqual(out["status"], "failed")
            self.assertEqual(out["evidence"]["response_bytes"], r)
            self.assertEqual(out["evidence"]["response_sha256"], sha(r))
            self.assertEqual(len(f.calls), 1)

    def test_extra_input_gold_rejected(self):
        row = fixture()
        row["gold"] = "nonfactual"
        with self.assertRaises(AdapterError):
            build_request(encode(row))

    def test_ambiguous_two_alternatives(self):
        row = fixture()
        a = assessment(row)
        a.update(
            fact_gate="ambiguous",
            decision_state="abstain",
            relation_expectation=None,
            alternatives=["one"],
        )
        with self.assertRaises(AdapterError):
            self.parse(row, a)
        a["alternatives"].append("two")
        self.parse(row, a)

    def test_blocked_cannot_deliver(self):
        row = fixture()
        a = assessment(row)
        a.update(
            decision_state="blocked", review_status="resolved", deliverable_relation="insufficient"
        )
        with self.assertRaises(AdapterError):
            self.parse(row, a)

    def test_same_id_revision_stale(self):
        row = fixture()
        req = build_request(encode(row))
        old = encode(response(req))
        row["source_context"]["evaluation_time"] = "later"
        new = build_request(encode(row))
        with self.assertRaises(AdapterError):
            parse_response(old, new)

    def test_child_nonzero_parent_mapping_and_subset(self):
        row = fixture()
        target = row["target"]
        draft = "prefix:" + target + ":suffix"
        start = 7
        row["target_binding"].update(
            draft=draft,
            start_codepoint=start,
            end_codepoint=start + len(target),
            sha256_utf8=sha(draft.encode()),
        )
        a = assessment(row)
        child = assessment(row)
        child.pop("children")
        child["id"] = row["id"] + ".1"
        a.update(
            fact_gate="split_required",
            decision_state="split",
            relation_expectation=None,
            children=[
                {
                    "target": target[:6],
                    "target_span": [7, 13],
                    "shared_scope_spans": [[7, 8]],
                    "assessment": child,
                }
            ],
        )
        self.parse(row, a)
        checked = 0
        for key, val in [
            ("target_span", [0, 6]),
            ("target_span", [True, 13]),
            ("shared_scope_spans", [[6, 8]]),
            ("target", "fabricated"),
        ]:
            bad = copy.deepcopy(a)
            bad["children"][0][key] = val
            with self.assertRaises(AdapterError):
                self.parse(row, bad)
            checked += 1
        self.assertEqual(checked, 4)
        child["citations"][0]["quote"] = "new citation"
        with self.assertRaises(AdapterError):
            self.parse(row, a)

    def test_eligible_bad_mechanics_must_block(self):
        row = fixture()
        row["candidate_citations"][0]["quote"] = "absent"
        a = assessment(row)
        a.update(decision_state="abstain", relation_expectation=None)
        a["citations"][0]["mechanical"] = "fail"
        with self.assertRaises(AdapterError):
            self.parse(row, a)

    def test_top_level_duplicate_input(self):
        raw = encode(fixture())
        raw = raw.replace(b"{", b'{"id":"item_ffffffffffffffff",', 1)
        with self.assertRaisesRegex(AdapterError, "duplicate_key"):
            build_request(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
