import hashlib
import json

import pytest


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def reviews(tmp_path):
    process, quality = tmp_path / "process", tmp_path / "quality"
    process.mkdir()
    quality.mkdir()
    parents = []
    samples = []
    for index in range(1, 5):
        outputs = [
            dict(
                request_number=n,
                call_numbers=[n],
                answer=f"answer-{index}-{n}",
                answer_sha256=hashlib.sha256(f"answer-{index}-{n}".encode()).hexdigest(),
            )
            for n in range(1, 3 if index == 4 else 2)
        ]
        parents.append(
            dict(
                sample_id=f"p-{index}",
                case_id=f"R{index:02}",
                parent_sample_id=f"old-{index}",
                answer=outputs[-1]["answer"],
                answer_sha256=outputs[-1]["answer_sha256"],
                runtime_outputs=outputs,
                execution_result="completed",
                raw_trial_verdict="incomplete",
                raw_rules=[dict(rule_id="quality.supported", status="unknown")],
            )
        )
        for output in outputs:
            samples.append(
                dict(
                    sample_id=f"q-{index}-{output['request_number']}",
                    case_id=f"R{index:02}",
                    parent_sample_id=f"p-{index}",
                    process_sample_id=f"p-{index}",
                    request_number=output["request_number"],
                    call_numbers=output["call_numbers"],
                    answer=output["answer"],
                    answer_sha256=output["answer_sha256"],
                    runtime_outputs=[output],
                )
            )
    for directory, kind, rows in [
        (process, "process_evidence", parents),
        (quality, "answer_quality", samples),
    ]:
        pack = dict(
            schema_version=3,
            kind="human-review-pack",
            review_kind=kind,
            review_pack_id=directory.name,
            source_commit="a" * 40,
            sample_count=len(rows),
            samples=rows,
        )
        pack_hash = save(directory / "review-pack.json", pack)
        labels = []
        for sample in rows:
            label = dict(
                sample_id=sample["sample_id"],
                case_id=sample["case_id"],
                parent_sample_id=sample["parent_sample_id"],
                review_pack_id=directory.name,
                pack_sha256=pack_hash,
                answer_sha256=sample["answer_sha256"],
                decision="pass",
                reason="Reviewed actual evidence",
                human_reviewed=True,
                complete=True,
            )
            if kind == "process_evidence":
                label["claims"] = [
                    dict(
                        claim_id="goal",
                        required=True,
                        decision="pass",
                        reason="bound",
                        human_reviewed=True,
                    ),
                    dict(
                        claim_id="archive",
                        required=False,
                        decision="abstain",
                        reason="not recorded",
                        human_reviewed=True,
                    ),
                ]
            else:
                label.update(
                    process_sample_id=sample["parent_sample_id"],
                    request_number=sample["request_number"],
                    call_numbers=sample["call_numbers"],
                    dimensions=dict(
                        relevance=2, grounding=3, actionability=2, interaction_burden=2
                    ),
                )
            labels.append(label)
        ai = dict(
            schema_version=3, review_pack_id=directory.name, pack_sha256=pack_hash, labels=labels
        )
        ai_hash = save(directory / "ai-review-for-human.json", ai)
        state_hash = save(directory / "state.json", dict(review_pack_id=directory.name))
        for label in labels:
            label["ai_draft_sha256"] = ai_hash
        human = dict(
            schema_version=3,
            kind="human-review-submission",
            review_kind=kind,
            review_pack_id=directory.name,
            pack_sha256=pack_hash,
            ai_draft_sha256=ai_hash,
            provided_ai_draft_sha256=None,
            source_state_file="state.json",
            source_state_sha256=state_hash,
            expected_sample_count=len(rows),
            included_sample_count=len(rows),
            confirmed_sample_count=len(rows),
            pending_sample_count=0,
            completion_status="complete",
            labels=labels,
        )
        save(directory / "human.json", human)
    return process / "human.json", quality / "human.json"


def validate(paths):
    from offerpilot.evaluation.core_acceptance import validate_reviews

    return validate_reviews(*paths)


def test_four_process_and_five_quality_outputs_remain_separate(reviews):
    result = validate(reviews)
    assert len(result["process"]) == 4 and len(result["quality"]) == 5
    r04 = [v for v in result["quality"] if v["case_id"] == "R04"]
    assert [v["request_number"] for v in r04] == [1, 2]
    assert [v["call_numbers"] for v in r04] == [[1], [2]]
    assert len({v["sample_id"] for v in r04}) == 2
    assert all(v["raw_trial_verdict"] == "incomplete" for v in result["process"])
    assert all(v["claims"][-1]["decision"] == "abstain" for v in result["process"])


@pytest.mark.parametrize(
    "damage",
    [
        "pack_hash",
        "ai_hash",
        "missing_label",
        "duplicate_id",
        "answer_hash",
        "r04_request",
        "r04_calls",
        "r04_answer",
        "missing_first_output",
        "weaken_claim",
    ],
)
def test_rejects_broken_hashes_reviews_and_output_bindings(reviews, damage):
    process_path, quality_path = reviews
    path = quality_path
    human = json.loads(path.read_bytes())
    if damage == "pack_hash":
        human["pack_sha256"] = "0" * 64
    elif damage == "ai_hash":
        human["ai_draft_sha256"] = "0" * 64
    elif damage == "missing_label":
        human["labels"].pop(0)
    elif damage == "duplicate_id":
        human["labels"][-1]["sample_id"] = human["labels"][-2]["sample_id"]
    elif damage == "answer_hash":
        human["labels"][-1]["answer_sha256"] = "0" * 64
    elif damage == "r04_request":
        human["labels"][-1]["request_number"] = 1
    elif damage == "r04_calls":
        human["labels"][-1]["call_numbers"] = [1]
    elif damage == "r04_answer":
        human["labels"][-1]["answer_sha256"] = human["labels"][-2]["answer_sha256"]
    elif damage == "missing_first_output":
        human["labels"].pop(-2)
    else:
        path = process_path
        human = json.loads(path.read_bytes())
        human["labels"][0]["claims"][0]["required"] = False
    save(path, human)
    with pytest.raises(ValueError):
        validate(reviews)


def test_rehashed_pack_cannot_rebind_r04_to_another_parent_output(reviews):
    p, q = reviews
    folder = q.parent
    pack = json.loads((folder / "review-pack.json").read_bytes())
    pack["samples"][-1]["runtime_outputs"][0]["answer"] = "another answer"
    pack["samples"][-1]["runtime_outputs"][0]["answer_sha256"] = hashlib.sha256(
        b"another answer"
    ).hexdigest()
    new_hash = save(folder / "review-pack.json", pack)
    human = json.loads(q.read_bytes())
    human["pack_sha256"] = new_hash
    for row in human["labels"]:
        row["pack_sha256"] = new_hash
    ai = json.loads((folder / "ai-review-for-human.json").read_bytes())
    ai["pack_sha256"] = new_hash
    for row in ai["labels"]:
        row["pack_sha256"] = new_hash
    new_ai = save(folder / "ai-review-for-human.json", ai)
    human["ai_draft_sha256"] = new_ai
    for row in human["labels"]:
        row["ai_draft_sha256"] = new_ai
    save(q, human)
    with pytest.raises(ValueError):
        validate(reviews)


def test_append_only_receipts_cannot_overwrite_or_promote_raw_results(
    reviews, tmp_path, monkeypatch
):
    from offerpilot.evaluation import core_acceptance as acceptance

    monkeypatch.setattr(acceptance, "verify_raw", lambda *args: [])
    audit = tmp_path / "private-audit" / "audit.json"
    audit.parent.mkdir()
    save(audit, {})
    before = {
        p: p.read_bytes()
        for folder in (reviews[0].parent, reviews[1].parent)
        for p in folder.iterdir()
    }
    out = tmp_path / "new-receipts"
    result = acceptance.append_summary(*reviews, audit, out)
    assert result["release_eligible"] is False and result["network_requests"] == 0
    assert len(result["records"]) == 9
    assert all(p.read_bytes() == raw for p, raw in before.items())
    saved = (out / "summary.json").read_bytes()
    with pytest.raises(ValueError, match="new and separate"):
        acceptance.append_summary(*reviews, audit, out)
    assert (out / "summary.json").read_bytes() == saved


def test_cli_rejects_invalid_review_before_creating_output(reviews, tmp_path):
    from typer.testing import CliRunner
    from offerpilot.evaluation.cli import app

    human = json.loads(reviews[1].read_bytes())
    human["pack_sha256"] = "0" * 64
    save(reviews[1], human)
    out = tmp_path / "invalid-output"
    result = CliRunner().invoke(
        app,
        [
            "acceptance-summary",
            "--process-review",
            str(reviews[0]),
            "--quality-review",
            str(reviews[1]),
            "--audit",
            str(tmp_path / "none"),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 2 and not out.exists()


@pytest.mark.parametrize("damage", ["parent", "index_path", "index_hash"])
def test_raw_audit_rejects_wrong_parent_and_unrelated_index(reviews, tmp_path, damage):
    from offerpilot.evaluation.core_acceptance import verify_raw

    accepted = validate(reviews)
    private = tmp_path / "sealed"
    private.mkdir()
    index = private / "evidence-index.json"
    index_hash = save(index, {})
    entries = [
        dict(
            sample_id=sample["sample_id"],
            parent_sample_id=sample["parent_sample_id"],
            private_directory=str(private),
            evidence_hash="0" * 64,
            index=dict(path=str(index), sha256=index_hash),
            trial_result={},
        )
        for sample in accepted["process_pack"]["samples"]
    ]
    if damage == "parent":
        entries[0]["parent_sample_id"] = entries[1]["parent_sample_id"]
    elif damage == "index_path":
        unrelated = tmp_path / "unrelated.json"
        save(unrelated, {})
        entries[0]["index"]["path"] = str(unrelated)
    audit = tmp_path / "audit.json"
    save(
        audit,
        dict(
            source_commit=accepted["source_commit"],
            pack_sha256=accepted["process"][0]["sources"]["pack"]["sha256"],
            samples=entries,
        ),
    )
    with pytest.raises(ValueError, match="audit " + damage.replace("_", " ") + " mismatch"):
        verify_raw(accepted, audit)
