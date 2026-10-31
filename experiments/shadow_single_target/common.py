import hashlib
import re


def sha(b):
    return hashlib.sha256(b).hexdigest()


def exact(obj, keys):
    if type(obj) is not dict or set(obj) != set(keys):
        raise ValueError("fields")


def public_row(x):
    exact(x, {"id", "target", "target_binding", "source_context", "sources", "candidate_citations"})
    if not isinstance(x["id"], str) or not re.fullmatch(r"item_[a-f0-9]{16}", x["id"]):
        raise ValueError("id")
    if type(x["target"]) is not str:
        raise ValueError("target")
    t = x["target_binding"]
    exact(t, {"draft", "start_codepoint", "end_codepoint", "sha256_utf8"})
    if (
        type(t["draft"]) is not str
        or type(t["start_codepoint"]) is not int
        or type(t["end_codepoint"]) is not int
    ):
        raise ValueError("binding_type")
    if not 0 <= t["start_codepoint"] < t["end_codepoint"] <= len(t["draft"]):
        raise ValueError("binding_range")
    if (
        t["draft"][t["start_codepoint"] : t["end_codepoint"]] != x["target"]
        or sha(t["draft"].encode()) != t["sha256_utf8"]
    ):
        raise ValueError("binding")
    c = x["source_context"]
    required = {"origin", "relative_date_anchor", "evaluation_time", "addressee"}
    optional = {"speaker", "utterance_time"}
    if (
        type(c) is not dict
        or not required <= set(c) <= required | optional
        or any(type(v) is not str for v in c.values())
    ):
        raise ValueError("context_fields")
    if type(x["sources"]) is not list or type(x["candidate_citations"]) is not list:
        raise ValueError("list")
    for s in x["sources"]:
        exact(s, {"id", "text", "sha256_utf8"})
        if (
            any(type(v) is not str for v in s.values())
            or sha(s["text"].encode()) != s["sha256_utf8"]
        ):
            raise ValueError("source_hash")
    for q in x["candidate_citations"]:
        exact(q, {"source_id", "quote"})
        if any(type(v) is not str for v in q.values()):
            raise ValueError("quote_type")
    return x
