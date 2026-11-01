"""Offline structural checks only. No semantic inference, provider, or admission gate."""

from .common import sha
from .locator import (
    locate_quote,
    LocatorError,
    _text,
    _ID,
    MAX_SOURCES,
    MAX_SOURCE_CODEPOINTS,
    MAX_QUOTE_CODEPOINTS,
)


def validate(row, projections=None):
    checks = {
        k: "not_evaluated"
        for k in ["source_profile", "source_hash", "target_mapping", "evidence_count"]
    }
    citations = []
    errors = []

    def ck(k, yes):
        checks[k] = "pass" if yes else "fail"
        if not yes:
            errors.append(k)

    sources = row.get("sources")
    profile = type(sources) is list and 1 <= len(sources) <= MAX_SOURCES
    if profile:
        ids = []
        for s in sources:
            if type(s) is not dict or set(s) != {"id", "text", "sha256_utf8"}:
                profile = False
                break
            k = s["id"]
            ids.append(k)
            if (
                type(k) is not str
                or not _ID.fullmatch(k)
                or not _text(s["text"], MAX_SOURCE_CODEPOINTS)
            ):
                profile = False
                break
        if profile:
            profile = len(set(ids)) == len(ids)
    ck("source_profile", profile)
    if profile:
        ck("source_hash", all(sha(s["text"].encode()) == s["sha256_utf8"] for s in sources))
    t = row.get("target_binding", {})
    d = t.get("draft")
    a = t.get("start_codepoint")
    b = t.get("end_codepoint")
    mapping = (
        type(d) is str
        and not any(0xD800 <= ord(c) <= 0xDFFF for c in d)
        and type(a) is int
        and type(b) is int
        and 0 <= a < b <= len(d)
        and d[a:b] == row.get("target")
    )
    if mapping:
        mapping = sha(d.encode()) == t.get("sha256_utf8")
    ck("target_mapping", mapping)
    evidence = row.get("candidate_citations")
    count = type(evidence) is list and len(evidence) <= 4
    ck("evidence_count", count)
    if count:
        for i, q in enumerate(evidence):
            c = {
                k: "not_evaluated"
                for k in [
                    "fields",
                    "length_check",
                    "source_id",
                    "exact",
                    "unique",
                    "grapheme",
                    "projection",
                ]
            }
            loc = None
            if type(q) is not dict or set(q) != {"source_id", "quote"}:
                c["fields"] = "fail"
            else:
                c["fields"] = "pass"
                c["length_check"] = "pass" if _text(q["quote"], MAX_QUOTE_CODEPOINTS) else "fail"
                if profile and c["length_check"] == "pass":
                    c["source_id"] = (
                        "pass"
                        if type(q["source_id"]) is str
                        and q["source_id"] in {s["id"] for s in sources}
                        else "fail"
                    )
                    if c["source_id"] == "pass":
                        try:
                            loc = locate_quote(
                                [{"id": s["id"], "text": s["text"]} for s in sources],
                                q["source_id"],
                                q["quote"],
                            )
                            c.update(exact="pass", unique="pass", grapheme="pass")
                            source = next(s["text"] for s in sources if s["id"] == q["source_id"])
                            projected = loc if projections is None else projections[i]
                            # Validate proposed projection against actual locator and original text.
                            c["projection"] = (
                                "pass"
                                if projected == loc
                                and source[loc["start"] : loc["end"]] == q["quote"]
                                else "fail"
                            )
                        except LocatorError as exc:
                            code = str(exc)
                            if code == "quote_not_found":
                                c["exact"] = "fail"
                            elif code == "quote_not_unique":
                                c.update(exact="pass", unique="fail")
                            elif code == "quote_splits_grapheme":
                                c.update(exact="pass", unique="pass", grapheme="fail")
                            else:
                                raise
                        except (IndexError, TypeError):
                            c["projection"] = "fail"
            if "fail" in c.values():
                errors.append("citation_" + str(i))
            citations.append({"checks": c, "located": loc})
    complete = all(v == "pass" for v in checks.values()) and all(
        all(v == "pass" for v in c["checks"].values()) for c in citations
    )
    return {
        "checks": checks,
        "citations": citations,
        "protocol_complete": complete,
        "errors": errors,
        "scope": "mechanics_only_semantics_not_evaluated",
    }
