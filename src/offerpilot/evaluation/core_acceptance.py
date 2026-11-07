"""Append-only acceptance views, retaining each output and the original raw verdict."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from offerpilot.evaluation.core_contracts import TrialResultV2
from offerpilot.evaluation.core_storage import load
from offerpilot.evaluation.registry import digest, file_hash

DECISIONS = {"pass", "fail", "abstain"}
DIMENSIONS = {"relevance", "grounding", "actionability", "interaction_burden"}


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def record(path: Path, expected: str | None = None) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "record unavailable")
    require(expected is None or file_hash(path) == expected, "record hash mismatch")
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), "record must be an object")
    return dict(value)


def reference(path: Path) -> dict[str, str]:
    return dict(path=str(path.resolve()), sha256=file_hash(path))


def unique(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        name = row.get(key)
        require(
            isinstance(name, str) and name and name not in result, "missing or duplicate identity"
        )
        result[str(name)] = row
    return result


def outputs(sample: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result = {}
    for output in sample["runtime_outputs"]:
        number, calls = output["request_number"], output["call_numbers"]
        require(
            type(number) is int and number > 0 and number not in result, "duplicate output request"
        )
        require(
            isinstance(calls, list)
            and calls
            and all(type(v) is int and v > 0 for v in calls)
            and calls == sorted(set(calls)),
            "invalid output calls",
        )
        require(
            output["answer_sha256"] == sha256(output["answer"].encode()).hexdigest(),
            "output hash mismatch",
        )
        result[number] = output
    require(result, "missing outputs")
    return result


def _review(path: Path, kind: str) -> dict[str, Any]:
    human = record(path)
    folder = path.parent
    pack_path, ai_path = folder / "review-pack.json", folder / "ai-review-for-human.json"
    pack, ai = record(pack_path, human["pack_sha256"]), record(ai_path, human["ai_draft_sha256"])
    require(
        human["schema_version"] == pack["schema_version"] == ai["schema_version"] == 3,
        "unsupported review schema",
    )
    require(
        human["kind"] == "human-review-submission"
        and human["review_kind"] == pack["review_kind"] == kind,
        "review kind mismatch",
    )
    require(
        human["review_pack_id"] == pack["review_pack_id"] == ai["review_pack_id"]
        and ai["pack_sha256"] == human["pack_sha256"],
        "AI pack binding mismatch",
    )
    state = human["source_state_file"]
    require(isinstance(state, str) and Path(state).name == state, "invalid review state path")
    record(folder / state, human["source_state_sha256"])
    sources = dict(
        pack=reference(pack_path),
        human=reference(path),
        ai=reference(ai_path),
        state=reference(folder / state),
    )
    if human.get("provided_ai_draft_sha256") is not None:
        original = folder / "ai-review-draft.json"
        record(original, human["provided_ai_draft_sha256"])
        sources["original_ai"] = reference(original)
    samples = unique(pack["samples"], "sample_id")
    labels, initials = unique(human["labels"], "sample_id"), unique(ai["labels"], "sample_id")
    require(samples.keys() == labels.keys() == initials.keys(), "review/sample coverage mismatch")
    require(
        pack["sample_count"] == len(samples)
        and human["pending_sample_count"] == 0
        and human["completion_status"] == "complete",
        "incomplete review",
    )
    require(
        all(
            type(human[k]) is int and human[k] == len(samples)
            for k in ("expected_sample_count", "included_sample_count", "confirmed_sample_count")
        ),
        "review counts mismatch",
    )
    for name, sample in samples.items():
        label = labels[name]
        require(
            label["complete"] is True
            and label["human_reviewed"] is True
            and label["decision"] in DECISIONS
            and bool(label["reason"]),
            "unconfirmed review",
        )
        require(
            label["review_pack_id"] == pack["review_pack_id"]
            and label["pack_sha256"] == human["pack_sha256"]
            and label["ai_draft_sha256"] == human["ai_draft_sha256"],
            "label source mismatch",
        )
        require(
            label["case_id"] == sample["case_id"]
            and label["parent_sample_id"] == sample["parent_sample_id"],
            "sample parent mismatch",
        )
        require(
            label["answer_sha256"]
            == sample["answer_sha256"]
            == sha256(sample["answer"].encode()).hexdigest(),
            "answer hash mismatch",
        )
        outputs(sample)
    return dict(pack=pack, samples=samples, labels=labels, initials=initials, sources=sources)


def validate_reviews(process_path: Path, quality_path: Path) -> dict[str, Any]:
    process, quality = (
        _review(process_path, "process_evidence"),
        _review(quality_path, "answer_quality"),
    )
    require(
        process["pack"]["source_commit"] == quality["pack"]["source_commit"],
        "review snapshot mismatch",
    )
    source = process["sources"]["pack"]
    for value in [quality["pack"], *quality["samples"].values(), *quality["labels"].values()]:
        binding = value.get("source_process_pack", {})
        require(
            isinstance(binding, dict)
            and binding.get("sha256") == source["sha256"]
            and isinstance(binding.get("path"), str)
            and Path(binding["path"]).resolve() == Path(source["path"]).resolve(),
            "quality process pack binding mismatch",
        )
    parents = process["samples"]
    process_rows, quality_rows = [], []
    expected = {(name, n) for name, sample in parents.items() for n in outputs(sample)}
    for name, sample in parents.items():
        label = process["labels"][name]
        claims = unique(label["claims"], "claim_id")
        original = unique(process["initials"][name]["claims"], "claim_id")
        require(claims.keys() == original.keys(), "claim coverage mismatch")
        for key, claim in claims.items():
            require(
                type(claim["required"]) is bool and claim["required"] == original[key]["required"],
                "required claim weakened",
            )
            require(
                claim["human_reviewed"] is True
                and claim["decision"] in DECISIONS
                and bool(claim["reason"]),
                "unconfirmed claim",
            )
        required = [c["decision"] for c in claims.values() if c["required"]]
        require(required, "required claims missing")
        decision = "fail" if "fail" in required else "abstain" if "abstain" in required else "pass"
        require(label["decision"] == decision, "process aggregation mismatch")
        process_rows.append(
            dict(
                sample_id=name,
                case_id=sample["case_id"],
                raw_execution_status=sample["execution_result"],
                raw_trial_verdict=sample["raw_trial_verdict"],
                raw_rules=sample["raw_rules"],
                human_decision=decision,
                reason=label["reason"],
                claims=label["claims"],
                sources=process["sources"],
            )
        )
    observed: set[tuple[str, int]] = set()
    for name, sample in quality["samples"].items():
        label, parent = quality["labels"][name], sample["process_sample_id"]
        require(
            parent in parents
            and sample["parent_sample_id"] == label["process_sample_id"] == parent,
            "quality process parent mismatch",
        )
        number, calls = sample["request_number"], sample["call_numbers"]
        output_key = (parent, number)
        require(
            output_key in expected and output_key not in observed,
            "duplicate or foreign output review",
        )
        require(
            label["request_number"] == number and label["call_numbers"] == calls,
            "quality request/call binding mismatch",
        )
        actual, own = outputs(parents[parent])[number], outputs(sample)
        require(
            set(own) == {number}
            and own[number] == actual
            and calls == actual["call_numbers"]
            and sample["answer_sha256"] == actual["answer_sha256"]
            and sample["case_id"] == parents[parent]["case_id"],
            "quality output bound to wrong request",
        )
        dims = label["dimensions"]
        require(
            set(dims) == DIMENSIONS and all(type(v) is int and 0 <= v <= 3 for v in dims.values()),
            "invalid dimensions",
        )
        quality_rows.append(
            dict(
                sample_id=name,
                process_sample_id=parent,
                case_id=sample["case_id"],
                request_number=number,
                call_numbers=calls,
                answer_sha256=sample["answer_sha256"],
                human_decision=label["decision"],
                reason=label["reason"],
                dimensions=dims,
                sources=quality["sources"],
            )
        )
        observed.add(output_key)
    require(observed == expected, "unreviewed output remains")
    return dict(
        process=process_rows,
        quality=quality_rows,
        source_commit=process["pack"]["source_commit"],
        process_pack=process["pack"],
    )


def verify_raw(accepted: dict[str, Any], audit_path: Path) -> list[dict[str, Any]]:
    from offerpilot.evaluation.core_review import model_context

    audit, pack = record(audit_path), accepted["process_pack"]
    require(
        audit["pack_sha256"] == accepted["process"][0]["sources"]["pack"]["sha256"]
        and audit["source_commit"] == pack["source_commit"],
        "audit binding mismatch",
    )
    indexed = unique(audit["samples"], "sample_id")
    require(
        set(indexed) == {s["sample_id"] for s in pack["samples"]}, "audit sample coverage mismatch"
    )
    raw_records = []
    for sample in pack["samples"]:
        entry = indexed[sample["sample_id"]]
        require(entry["parent_sample_id"] == sample["parent_sample_id"], "audit parent mismatch")
        ref, index = entry["trial_result"], entry["index"]
        require(
            Path(index["path"]).resolve()
            == (Path(entry["private_directory"]) / "evidence-index.json").resolve(),
            "audit index path mismatch",
        )
        actual_index = record(Path(index["path"]), index["sha256"])
        require(digest(actual_index) == entry["evidence_hash"], "audit index hash mismatch")
        raw = TrialResultV2.model_validate(record(Path(ref["path"]), ref["sha256"]))
        evidence = load(Path(entry["private_directory"]), entry["evidence_hash"])
        require(
            evidence["errors"] == {} and raw.evidence_hash == entry["evidence_hash"],
            "raw evidence invalid",
        )
        expected_key = raw.model_dump(
            mode="json",
            include={"run_id", "variant_id", "execution_item_id", "mode", "repeat_index"},
        )
        require(
            evidence["trial_key"] == expected_key
            and Path(raw.private_directory).resolve() == Path(entry["private_directory"]).resolve(),
            "raw trial identity mismatch",
        )
        _, captured = model_context(Path(entry["private_directory"]), evidence)
        require(captured == entry["model_input_bindings"], "audit input binding mismatch")
        require(
            raw.execution_status == sample["execution_result"]
            and raw.verdict == sample["raw_trial_verdict"]
            and [v.model_dump(mode="json") for v in raw.rules] == sample["raw_rules"],
            "raw trial changed",
        )
        rows = outputs(sample)
        require(len(rows) == len(entry["output_bindings"]), "raw output coverage mismatch")
        inputs = {b["number"]: b for b in entry["model_input_bindings"]}
        for number, binding in enumerate(entry["output_bindings"], 1):
            item = rows[number]
            db = binding["database"]
            path = Path(db["path"])
            require(
                not path.is_symlink() and file_hash(path) == db["sha256"], "database hash mismatch"
            )
            require(binding["content_sha256"] == item["answer_sha256"], "raw output hash mismatch")
            with sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
            ) as connection:
                message = connection.execute(
                    "select role, content from chat_messages where id=?",
                    (binding["database_message_id"],),
                ).fetchone()
            require(
                message is not None and message[0] == "assistant" and message[1] == item["answer"],
                "database message mismatch",
            )
            terminal = [
                h for h in evidence["http"] if digest(h) == binding["terminal_observation_hash"]
            ]
            require(
                len(terminal) == 1
                and terminal[0]["response"]["turn_id"] == binding["turn_id"]
                and terminal[0]["response"]["terminal"]["response"]["message"] == item["answer"],
                "Runtime terminal mismatch",
            )
            require(
                all(
                    inputs[c]["identity"]["execution"]["request_id"] == binding["request_id"]
                    and inputs[c]["identity"]["execution"]["turn_id"] == binding["turn_id"]
                    for c in item["call_numbers"]
                ),
                "raw call bound to wrong output",
            )
        raw_records.append(
            dict(
                sample_id=sample["sample_id"],
                trial=ref,
                evidence_hash=raw.evidence_hash,
                trial_key=list(raw.identity()),
                raw_verdict=raw.verdict,
                execution_status=raw.execution_status,
                evidence_index=index,
            )
        )
    return raw_records


def append_summary(process: Path, quality: Path, audit: Path, output: Path) -> dict[str, Any]:
    accepted = validate_reviews(process, quality)
    raw = verify_raw(accepted, audit)
    require(
        not output.exists()
        and all(
            not output.resolve().is_relative_to(p.parent.resolve())
            for p in (process, quality, audit)
        ),
        "output must be new and separate",
    )
    output.mkdir(parents=True, exist_ok=False)
    refs = []
    for kind in ("process", "quality"):
        for row in accepted[kind]:
            item = dict(
                schema_version=3,
                kind="reviewed-acceptance-record",
                review_kind=kind,
                **row,
                raw_trial_modified=False,
                release_eligible=False,
                calibration_eligible=False,
            )
            path = output / (kind + "-" + digest(item) + ".json")
            _append(path, item)
            refs.append(reference(path))
    summary = dict(
        schema_version=3,
        kind="directed-acceptance-summary",
        evidence_source_commit=accepted["source_commit"],
        created_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        sources=dict(
            audit=reference(audit),
            process_review=reference(process),
            quality_review=reference(quality),
        ),
        process=accepted["process"],
        quality=accepted["quality"],
        raw_trials=raw,
        records=refs,
        counts=dict(
            process_samples=len(accepted["process"]),
            quality_outputs=len(accepted["quality"]),
            required_claims_supported=sum(
                c["required"] and c["decision"] == "pass"
                for r in accepted["process"]
                for c in r["claims"]
            ),
        ),
        governance_scope="Independent output-bound review receipts; not a trial-only Adjudication or release bundle. R04 scores stay separate.",
        acceptance_implementation=reference(Path(__file__)),
        raw_trial_modified=False,
        release_eligible=False,
        calibration_eligible=False,
        network_requests=0,
    )
    _append(output / "summary.json", summary)
    return summary


def _append(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
