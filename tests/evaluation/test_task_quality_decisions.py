"""Synthetic contract/decision tests; these fixtures are not human acceptance."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from offerpilot.evaluation.core_contracts import ProviderRunProfile, TrialResultV2
from offerpilot.evaluation.core_governance import (
    GatePolicyV2, compare_core, gate_core, product_hash, reviewed_trials,
)
from offerpilot.evaluation.core_registry import case_definition, required_rules, suite_case_version
from offerpilot.evaluation.core_runner import missing
from offerpilot.evaluation.core_task_quality import (
    GatePolicyV3, HumanQualityRecord, ScenarioAcceptance, TaskReviewBundle, TASK_RULES, quality_views,
)
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT
from offerpilot.evaluation.core_review import CURRENT_GOAL_RUBRIC
from offerpilot.evaluation.core_task_review import BUSINESS_QUALITY_RUBRIC
from tests.evaluation.test_core_governance import manifest as manifest, experiment, review


@pytest.fixture
def business(manifest, tmp_path, monkeypatch):
    from offerpilot.evaluation import core_planning as planning
    from offerpilot.evaluation import core_task_quality as quality

    profile = ProviderRunProfile(
        profile_id="synthetic-only", endpoint="https://synthetic.invalid/v1",
        model="synthetic-only", credential_ref="offline-test-only",
        input_cny_per_million=1, output_cny_per_million=1,
        price_reference="synthetic fixture, not price evidence",
        price_checked_at=datetime.now(timezone.utc),
    )
    path = tmp_path / "synthetic-profile.json"
    path.write_text(profile.model_dump_json(), encoding="utf-8")
    m = planning.create_plan(
        ROOT / "evals/suites/harness-core-business-v1.json",
        tmp_path / "variants.json", tmp_path / "business",
        mode="real", repeat=3, provider_path=path,
    )
    rows = []
    for p in m.planned_trials:
        row = missing(p, "synthetic_contract_fixture_only").model_dump(mode="json")
        for rule in row["rules"]:
            rule["status"] = "unknown" if rule["rule_id"] in TASK_RULES else "pass"
        row.update(
            execution_status="completed",
            verdict="incomplete" if any(v["status"] == "unknown" for v in row["rules"]) else "pass",
            evidence_hash="f"*64, consumption=dict(actual_models=["synthetic-only"]),
        )
        rows.append(TrialResultV2.model_validate(row))
    policy = GatePolicyV3(
        policy_id="synthetic-business-only", version=1, suite_hash=m.suite_hash,
        purposes=["initial_baseline", "release"],
        absolute=[
            dict(
                execution_item_id=i, mode="real", repeats=3,
                case_version=suite_case_version(m.suite_id, i),
                coverage_kind=case_definition(i)["coverage_kind"],
                rules=required_rules(i, "real", suite_case_version(m.suite_id, i)),
            )
            for i in dict.fromkeys(p.execution_item_id for p in m.planned_trials)
        ],
        quality_scenarios=[
            dict(
                execution_item_id=i, rule_id="quality.current_goal" if i=="E09.memory_override"
                else "quality.supported", rubric="current-goal-v2" if i=="E09.memory_override"
                else "business-quality-v2",
            )
            for i in ["E09.memory_override", "E09.memory_withdraw",
                      "E10.knowledge_dual_retrieval", "E10.evidence_injection"]
        ],
        rationale="Synthetic regression, not a release policy approval",
        review_reference="synthetic fixture only",
    )

    def inventory(row):
        outputs = [
            dict(
                sample_id=digest([*row.identity(), n]), trial_key=list(row.identity()),
                request_number=n, call_numbers=[n],
                answer_sha256=sha256(b"synthetic answer, not real output").hexdigest(),
                evidence_hash=row.evidence_hash, answer="synthetic answer, not real output",
                user_request="synthetic request",
            )
            for n in range(1, (2 if row.execution_item_id=="E09.memory_withdraw" else 1)+1)
        ]
        return dict(
            outputs=outputs, observed_outputs=outputs, gaps=[],
            expected_requests=len(outputs), execution_observed=True,
            subject_started_observed=True, request_accepted_observed=True,
        )

    monkeypatch.setattr(quality, "output_inventory", inventory)
    return m, rows, policy, inventory


def signed(value, tmp_path, name):
    path = tmp_path / (name+".json")
    path.write_text(
        json.dumps(value.model_dump(mode="json", exclude={"record_ref", "record_hash"})),
        encoding="utf-8",
    )
    return value.model_copy(update={"record_ref": str(path), "record_hash": file_hash(path)})


def bundle(business, tmp_path, *, task_failure=False, critical="pass",
           accepted=True, variants=("candidate",)):
    m, rows, policy, inventory = business
    now = datetime.now(timezone.utc)
    labels = []
    for row in rows:
        if row.variant_id not in variants or not any(r.rule_id in TASK_RULES for r in row.rules):
            continue
        for output in inventory(row)["outputs"]:
            target = row.execution_item_id=="E09.memory_override" and row.repeat_index==1
            pack = tmp_path/("synthetic-pack-"+output["sample_id"]+".json")
            pack.write_text(json.dumps(dict(
                schema_version=3,kind="task-quality-pack",
                samples=[dict(
                    sample_id=output["sample_id"], output_binding=output, answer=output["answer"],
                    answer_sha256=output["answer_sha256"], request_number=output["request_number"],
                    call_numbers=output["call_numbers"], user_request=output["user_request"],
                    rubric=CURRENT_GOAL_RUBRIC if row.execution_item_id=="E09.memory_override"
                    else BUSINESS_QUALITY_RUBRIC,
                )])),
                            encoding="utf-8")
            value = HumanQualityRecord(
                review_id="synthetic-"+output["sample_id"], sample_id=output["sample_id"],
                trial_key=row.identity(), evidence_hash=row.evidence_hash,
                request_number=output["request_number"], call_numbers=output["call_numbers"],
                answer_sha256=output["answer_sha256"], output_binding_hash=digest(output),
                review_pack_ref=str(pack), review_pack_hash=file_hash(pack),
                rubric="current-goal-v2" if row.execution_item_id=="E09.memory_override"
                else "business-quality-v2",
                task_decision="fail" if task_failure and target else "pass",
                critical_decision=critical if target else "pass",
                task_reason="Synthetic quality failure" if task_failure and target else "Synthetic pass",
                critical_reason="Synthetic critical check only", supporting_sentences=["synthetic quote"],
                expression_notes=["synthetic advisory note"], reviewer="synthetic-human-fixture",
                confirmation="confirmed", label_source="manual_human",
                created_at=now, expires_at=now+timedelta(days=1),
                record_ref="unused", record_hash="0"*64,
            )
            labels.append(signed(value, tmp_path, value.review_id))
    value = TaskReviewBundle(
        quality_reviews=labels, independent_reviews=review(m, tmp_path).independent_reviews
    )
    _, summaries = quality_views(m, rows, policy, value)
    acceptances = []
    for summary in summaries:
        if summary["variant_id"] not in variants or summary["reviewed_outputs_hash"] is None:
            continue
        acceptance = ScenarioAcceptance(
            acceptance_id="synthetic-"+digest([summary["variant_id"], summary["execution_item_id"]]),
            run_id=str(m.run_id), manifest_hash=digest(m.model_dump(mode="json")),
            candidate_snapshot_hash=product_hash(m, summary["variant_id"]),
            policy_hash=digest(policy.model_dump(mode="json")),
            execution_item_id=summary["execution_item_id"], variant_id=summary["variant_id"],
            reviewed_outputs_hash=summary["reviewed_outputs_hash"],
            decision="accepted" if accepted else "rejected",
            accepted_failure_ids=summary["task_failure_ids"], reason="Synthetic explicit risk decision",
            reviewer="synthetic-maintainer", role="maintainer", confirmation="confirmed",
            created_at=now, expires_at=now+timedelta(days=1),
            record_ref="unused", record_hash="0"*64,
        )
        acceptances.append(signed(acceptance, tmp_path, acceptance.acceptance_id))
    return value.model_copy(update={"scenario_acceptances": acceptances})


def test_noncritical_quality_failure_can_be_explicitly_accepted_without_rewriting_scores(
    business, tmp_path
):
    m, rows, policy, _ = business
    raw = [r.model_dump(mode="json") for r in rows]
    reviews = bundle(business, tmp_path, task_failure=True)
    result = gate_core([m], [rows], policy, reviews, "initial_baseline", "enforce")
    assert result["exit_code"] == 0 and result["gate_satisfied"]
    summary = next(s for s in result["task_quality"]
                   if s["variant_id"]=="candidate" and s["execution_item_id"]=="E09.memory_override")
    assert summary["task_fail"] == 1 and summary["task_pass"] == 2
    assert summary["end_to_end_task_completion"] == dict(numerator=2, denominator=3)
    assert summary["conditional_quality_success"] == dict(numerator=2, denominator=3)
    assert summary["failure_impact"][0]["supporting_sentences"] == ["synthetic quote"]
    assert raw == [r.model_dump(mode="json") for r in rows]
    assert not result["release_eligible"]


@pytest.mark.parametrize("critical,accepted,expected", [
    ("fail", True, 1), ("abstain", True, 3), ("pass", False, 1),
])
def test_critical_unknown_and_explicit_rejection_have_consistent_absolute_and_relative_results(
    business, tmp_path, critical, accepted, expected
):
    m, rows, policy, _ = business
    reviews = bundle(business, tmp_path, critical=critical, accepted=accepted)
    absolute = gate_core([m], [rows], policy, reviews, "initial_baseline", "enforce")
    assert absolute["exit_code"] == expected
    baseline = []
    for row in rows:
        rules = [r.model_copy(update={"status": "pass"}) for r in row.rules]
        baseline.append(row.model_copy(update={"rules": rules, "verdict": "pass"}))
    comparison = compare_core(
        m, baseline, m, reviewed_trials(m, rows, policy, reviews), experiment(m),
        task_quality_rules=TASK_RULES,
    )
    assert comparison["exit_code"] == expected


@pytest.mark.parametrize("mutation", ["missing_label", "wrong_hash", "wrong_call", "wrong_request"])
def test_scenario_acceptance_cannot_cover_unreviewed_or_misbound_outputs(business, tmp_path, mutation):
    m, rows, policy, _ = business
    reviews = bundle(business, tmp_path)
    if mutation == "missing_label":
        reviews.quality_reviews.pop()
    else:
        label = reviews.quality_reviews[-1]
        field, value = {
            "wrong_hash": ("answer_sha256", "0"*64),
            "wrong_call": ("call_numbers", [99]),
            "wrong_request": ("request_number", 99),
        }[mutation]
        reviews.quality_reviews[-1] = label.model_copy(update={field:value})
    result = gate_core([m], [rows], policy, reviews, "initial_baseline", "enforce")
    assert result["exit_code"] == 3
    assert not result["gate_satisfied"]


def test_legacy_policy_cannot_silently_take_new_case_or_new_labels(business):
    _, _, policy, _ = business
    old = json.loads((ROOT/"evals/policies/core-release-v6.json").read_bytes())
    target = next(v for v in old["absolute"] if v["execution_item_id"]=="E09.memory_override")
    target.update(case_version=3, rules=required_rules("E09.memory_override", case_version=3))
    with pytest.raises(ValidationError, match="legacy policy"):
        GatePolicyV2.model_validate(old)
    weak = policy.model_dump(mode="json")
    weak["quality_scenarios"].pop()
    with pytest.raises(ValidationError, match="cannot omit"):
        GatePolicyV3.model_validate(weak)


@pytest.mark.parametrize("condition", ["second_failed", "second_timeout", "second_not_submitted"])
def test_partial_r04_summary_keeps_first_reply_and_reports_unknown_second_slot(
    business, tmp_path, monkeypatch, condition
):
    from offerpilot.evaluation import core_task_quality as quality
    from offerpilot.evaluation.core_quality_outputs import output_inventory
    from tests.evaluation.test_quality_output_inventory import sealed_trial

    m, rows, policy, original = business
    target = next(r for r in rows if r.variant_id=="candidate"
                  and r.execution_item_id=="E09.memory_withdraw" and r.repeat_index==1)
    observed = sealed_trial(tmp_path, condition, row=target)
    inventory = output_inventory(observed)
    changed_rows = [observed if r.identity()==target.identity() else r for r in rows]
    def partial(row):
        return inventory if row.identity()==target.identity() else original(row)
    monkeypatch.setattr(quality,"output_inventory",partial)
    changed = (m, changed_rows, policy, partial)
    reviews = bundle(changed, tmp_path)
    _, summaries = quality_views(m, changed_rows, policy, reviews)
    summary = next(s for s in summaries if s["variant_id"]=="candidate"
                   and s["execution_item_id"]=="E09.memory_withdraw")
    assert summary["planned_trials"] == summary["observed_output_trials"] == 3
    assert summary["observed_outputs"] == summary["bound_outputs"] == summary["judged_outputs"] == 5
    assert summary["output_coverage"] == dict(numerator=5,denominator=6)
    assert summary["conditional_quality_success"] == dict(numerator=5,denominator=5)
    assert summary["end_to_end_task_completion"] == dict(numerator=2,denominator=3)
    assert summary["unobserved_output_slots"] == 1
    assert summary["disposition"] == "unknown"


def test_display_answer_replacement_is_rejected_even_after_rehashing_and_resigning(business, tmp_path):
    from offerpilot.evaluation.core_task_quality import confirmed_record

    reviews = bundle(business, tmp_path)
    label = reviews.quality_reviews[0]
    pack = json.loads(Path(label.review_pack_ref).read_bytes())
    pack["samples"][0]["answer"] = "A different displayed answer, with the original binding unchanged"
    path = Path(label.review_pack_ref)
    path.write_text(json.dumps(pack),encoding="utf-8")
    changed = label.model_copy(update={"review_pack_hash":file_hash(path)})
    changed = signed(changed,tmp_path,"rehashed-wrong-display-confirmation")
    assert not confirmed_record(changed,30)


def test_timeout_stays_in_the_planned_denominator_with_no_judged_reply_for_that_trial(
    business,tmp_path,monkeypatch
):
    from offerpilot.evaluation import core_task_quality as quality
    from offerpilot.evaluation.core_quality_outputs import output_inventory
    from tests.evaluation.test_quality_output_inventory import sealed_trial

    m,rows,policy,original=business
    target=next(r for r in rows if r.variant_id=="candidate"
                and r.execution_item_id=="E09.memory_override" and r.repeat_index==1)
    timeout=sealed_trial(tmp_path,"single_timeout",row=target)
    actual=output_inventory(timeout)
    changed_rows=[timeout if r.identity()==target.identity() else r for r in rows]
    def inventory(row):
        return actual if row.identity()==target.identity() else original(row)
    monkeypatch.setattr(quality,"output_inventory",inventory)
    reviews=bundle((m,changed_rows,policy,inventory),tmp_path)
    _,reports=quality_views(m,changed_rows,policy,reviews)
    report=next(s for s in reports if s["variant_id"]=="candidate"
                and s["execution_item_id"]=="E09.memory_override")
    assert report["planned_trials"]==report["execution_observed_trials"]==3
    assert report["observed_output_trials"]==report["observed_outputs"]==report["judged_outputs"]==2
    assert report["end_to_end_task_completion"]==dict(numerator=2,denominator=3)
    assert report["conditional_quality_success"]==dict(numerator=2,denominator=2)
    assert report["unobserved_output_slots"]==1 and report["disposition"]=="unknown"


@pytest.mark.parametrize("status,expected",[("fail",1),("unknown",3)])
def test_an_existing_scenario_acceptance_cannot_override_hard_failure_or_required_gap(
    business,tmp_path,status,expected
):
    m,rows,policy,_=business
    reviews=bundle(business,tmp_path)
    changed=[]
    for row in rows:
        if row.variant_id=="candidate" and row.execution_item_id=="E10.evidence_injection" and row.repeat_index==1:
            rules=[r.model_copy(update={"status":status}) if r.rule_id=="source.no_write_intent" else r
                   for r in row.rules]
            row=row.model_copy(update={"rules":rules,"verdict":"fail" if status=="fail" else "incomplete"})
        changed.append(row)
    result=gate_core([m],[changed],policy,reviews,"initial_baseline","enforce")
    assert result["exit_code"]==expected
    assert not result["gate_satisfied"]
