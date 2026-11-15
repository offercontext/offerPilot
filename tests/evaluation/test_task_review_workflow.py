"""Review/export/import component tests with explicitly synthetic sealed records."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from offerpilot.evaluation import core_task_quality as quality
from offerpilot.evaluation import core_task_review as workflow
from offerpilot.evaluation.cli import app
from offerpilot.evaluation.core_governance import gate_core, load_review_bundle
from offerpilot.evaluation.core_quality_outputs import output_inventory
from tests.evaluation.test_quality_output_inventory import sealed_trial
from tests.evaluation.test_task_quality_decisions import business as business
from tests.evaluation.test_core_governance import manifest as manifest, review


@pytest.fixture
def review_flow(business, tmp_path, monkeypatch):
    m, rows, policy, _ = business
    actual = []
    for row in rows:
        if row.variant_id=="candidate" and any(r.rule_id in quality.TASK_RULES for r in row.rules):
            folder = tmp_path/("io-"+str(len(actual)))
            folder.mkdir()
            row = sealed_trial(folder,"complete",row=row)
        actual.append(row)
    monkeypatch.setattr(workflow,"read_run",lambda _: (m,actual))
    monkeypatch.setattr(quality,"output_inventory",output_inventory)
    directory = tmp_path/"run-source"
    directory.mkdir()
    (directory/"run-manifest.json").write_text(m.model_dump_json(),encoding="utf-8")
    policy_path = tmp_path/"real-policy.json"
    policy_path.write_text(policy.model_dump_json(),encoding="utf-8")
    folder = tmp_path/"pack"
    result = workflow.export_pack([directory],policy_path,folder)
    return m, actual, policy, folder, result


def human_submission(folder):
    submitted = json.loads((folder/"human-submission-template.json").read_bytes())
    now = datetime.now(timezone.utc)
    submitted.update(reviewer="synthetic-human-only",created_at=now.isoformat(),
                     expires_at=(now+timedelta(days=1)).isoformat())
    for label in submitted["labels"]:
        label.update(confirmation="confirmed", task_decision="pass", critical_decision="pass",
                     task_reason="Synthetic I/O decision", critical_reason="Synthetic fixture only",
                     supporting_sentences=["synthetic answer 1"])
    submitted["labels"][0].update(task_decision="fail",task_reason="Synthetic ordinary quality risk")
    path = folder/"synthetic-confirmed-submission.json"
    path.write_text(json.dumps(submitted),encoding="utf-8")
    return path


def test_export_keeps_fifteen_outputs_and_both_r04_requests_with_versioned_rubrics(review_flow):
    _,_,_,folder,result = review_flow
    pack = json.loads((folder/"review-pack.json").read_bytes())
    assert result["samples"] == len(pack["samples"]) == 15
    r04 = [s for s in pack["samples"] if s["execution_item_id"]=="E09.memory_withdraw"]
    assert len(r04)==6 and {s["request_number"] for s in r04}=={1,2}
    assert len({s["sample_id"] for s in r04})==6
    assert {s["rubric"]["rubric"] for s in pack["samples"]}=={"current-goal-v2","business-quality-v2"}
    assert all(s["old_confirmation_inherited"] is False for s in pack["samples"])
    assert all(s["model_context"]["status"]=="captured" for s in pack["samples"])


def test_pending_draft_cannot_be_imported_as_human_confirmation(review_flow, tmp_path):
    *_,folder,_ = review_flow
    with pytest.raises(ValueError,match="unconfirmed"):
        workflow.import_labels(folder/"review-pack.json",folder/"human-submission-template.json",
                               tmp_path/"not-created")
    assert not (tmp_path/"not-created").exists()


def test_new_review_cli_imports_labels_then_separate_explicit_scenario_risk_decisions(
    review_flow,tmp_path
):
    m,rows,policy,folder,_ = review_flow
    source = human_submission(folder)
    runner = CliRunner()
    imported = runner.invoke(app,["task-review-import","--pack",str(folder/"review-pack.json"),
                                  "--submission",str(source),"--output",str(tmp_path/"labels")])
    assert imported.exit_code==0, imported.output
    result = json.loads(imported.output)
    assert result["confirmed_output_records"]==15 and result["scenario_acceptances"]==0
    request_path = Path(result["scenario_request"])
    pending = json.loads((request_path.parent/"scenario-decisions-template.json").read_bytes())
    now = datetime.now(timezone.utc)
    pending.update(reviewer="synthetic-maintainer-only",created_at=now.isoformat(),
                   expires_at=(now+timedelta(days=1)).isoformat())
    for decision in pending["decisions"]:
        decision.update(confirmation="confirmed",decision="accepted",reason="Synthetic acknowledged risk")
    decision_path = tmp_path/"synthetic-scenario-confirmation.json"
    decision_path.write_text(json.dumps(pending),encoding="utf-8")
    accepted = runner.invoke(app,["task-scenario-import","--request",str(request_path),
                                  "--submission",str(decision_path),"--output",str(tmp_path/"accepted")])
    assert accepted.exit_code==0, accepted.output
    bundle = load_review_bundle(json.loads(
        Path(json.loads(accepted.output)["review_bundle"]).read_bytes()
    ))
    assert isinstance(bundle,quality.TaskReviewBundle)
    assert len(bundle.scenario_acceptances)==4
    assert any(q.task_decision=="fail" for q in bundle.quality_reviews)
    bundle = bundle.model_copy(update={"independent_reviews":review(m,tmp_path).independent_reviews})
    gate = gate_core([m],[rows],policy,bundle,"initial_baseline","enforce")
    assert gate["exit_code"]==0
    assert sum(s["task_fail"] for s in gate["task_quality"] if s["variant_id"]=="candidate")==1
    assert not gate["release_eligible"]


def test_changed_display_answer_cannot_reuse_unchanged_binding_in_import(review_flow,tmp_path):
    _,_,_,folder,_ = review_flow
    pack_path = folder/"review-pack.json"
    pack = json.loads(pack_path.read_bytes())
    pack["samples"][0]["answer"]="Changed only in the review display"
    pack_path.write_text(json.dumps(pack),encoding="utf-8")
    submission = human_submission(folder)
    raw = json.loads(submission.read_bytes())
    raw["pack_sha256"]=workflow.file_hash(pack_path)
    submission.write_text(json.dumps(raw),encoding="utf-8")
    with pytest.raises(ValueError,match="differs from sealed"):
        workflow.import_labels(pack_path,submission,tmp_path/"must-not-create")
