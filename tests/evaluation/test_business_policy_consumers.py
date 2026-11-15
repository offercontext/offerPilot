"""Schema3 consumer regressions. All approvals, stores and transports are synthetic."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from offerpilot.evaluation import core_governance as gov, core_release as release
from offerpilot.evaluation.core_contracts import RunManifestV2
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_refresh import refresh_case as refresh_case, bytes_at
from tests.evaluation.test_core_release import setup_release
from tests.evaluation.test_task_quality_decisions import business as business, bundle


def release_case(business, tmp_path, monkeypatch, condition):
    m, rows, policy, inventory = business
    m, paths, args = setup_release(m, tmp_path, monkeypatch)
    paths["suite"].write_bytes((ROOT/"evals/suites/harness-core-business-v1.json").read_bytes())
    credential = tmp_path/"synthetic-not-a-provider-key.txt"
    credential.write_text("synthetic-only-no-send", encoding="utf-8")
    profile = m.provider_profile.model_copy(update={"credential_ref":str(credential)})
    m = RunManifestV2.model_validate({
        **m.model_dump(mode="json"), "suite_hash":file_hash(paths["suite"]),
        "provider_profile":profile.model_dump(mode="json"),
        "provider_profile_hash":digest(profile.model_dump(mode="json")),
    })
    paths["policy"].write_text(policy.model_dump_json(),encoding="utf-8")
    base_folder, candidate_folder = tmp_path/"base-human", tmp_path/"candidate-human"
    base_folder.mkdir()
    candidate_folder.mkdir()
    changed = (m,rows,policy,inventory)
    old_review = bundle(changed,base_folder)
    current_review = bundle(
        changed,candidate_folder,task_failure=condition=="accepted_risk",
        critical="fail" if condition=="critical" else "pass",
        accepted=condition!="rejected",
    )
    if condition=="unreviewed":
        current_review.quality_reviews.pop()
    paths["review"].write_text(current_review.model_dump_json(),encoding="utf-8")
    baseline_directory = tmp_path/"baseline"
    base_gate = gov.gate_core([m],[rows],policy,old_review,"initial_baseline","enforce")
    assert base_gate["gate_satisfied"], json.dumps(base_gate)
    base_gate["sources"][0]["directory"]=str(baseline_directory)
    base_path = tmp_path/"baseline-gate.json"
    base_path.write_text(json.dumps(base_gate),encoding="utf-8")
    original = release.load_baseline(tmp_path/"store","synthetic")
    original.update(gate_path=str(base_path),gate_hash=digest(base_gate),sources=[
        dict(directory=str(baseline_directory),manifest_hash=digest(m.model_dump(mode="json")))
    ])
    (tmp_path/"store/records/synthetic.json").write_text(json.dumps(original),encoding="utf-8")
    monkeypatch.setattr(release,"read_run",lambda *_args,**_kwargs: (m,rows))
    monkeypatch.setattr(release,"read_plan",lambda *_args,**_kwargs: m)
    monkeypatch.setattr(release,"validate_grant",lambda *_args: None)
    monkeypatch.setattr(release,"evaluator_hash",lambda: m.evaluator_hash)
    provider_path, grant_path = tmp_path/"synthetic-provider.json",tmp_path/"synthetic-grant.json"
    provider_path.write_text(profile.model_dump_json(),encoding="utf-8")
    grant_path.write_text('{"synthetic_reuse_only":true}',encoding="utf-8")
    return SimpleNamespace(
        manifest=m,rows=rows,policy=policy,review=current_review,paths=paths,args=args,
        baseline=baseline_directory,provider=provider_path,grant=grant_path,
    )


@pytest.mark.parametrize("condition,expected", [
    ("accepted_risk",0),("critical",1),("unreviewed",3),("rejected",1),
])
def test_schema3_release_execute_and_receipt_regrade_quality_and_scenario_decisions(
    business,tmp_path,monkeypatch,condition,expected
):
    case = release_case(business,tmp_path,monkeypatch,condition)
    release.preflight(*case.args,provider_path=case.provider,grant_path=case.grant,reuse=[case.baseline])
    path = case.args[3]/"release-preflight.json"
    report = release.execute(path)
    assert report["schema_version"]==3 and report["exit_code"]==expected
    assert not report["release_eligible"]
    if expected==0:
        assert release.verify_receipt(path)["evaluation_satisfied"]
        assert sum(s["task_fail"] for s in report["task_quality"]
                   if s["variant_id"]=="candidate")==1
    else:
        receipt_path=path.parent/"release-evaluation.json"
        receipt=json.loads(receipt_path.read_bytes())
        receipt["evaluation_gate"].update(gate_satisfied=True,exit_code=0)
        receipt["gate_hash"]=digest(receipt["evaluation_gate"])
        receipt_path.write_text(json.dumps(receipt),encoding="utf-8")
        with pytest.raises(ValueError,match="no longer satisfies"):
            release.verify_receipt(path)


@pytest.mark.parametrize("condition,allowed", [
    ("accepted_risk",True),("critical",False),("unreviewed",False),
])
def test_schema3_promotion_recomputes_reviews_in_an_isolated_synthetic_store(
    business,tmp_path,monkeypatch,condition,allowed
):
    case = release_case(business,tmp_path,monkeypatch,condition)
    m,rows=case.manifest,case.rows
    monkeypatch.setattr(gov,"read_run",lambda *_args,**_kwargs:(m,rows))
    report=gov.gate_core([m],[rows],case.policy,case.review,"initial_baseline","enforce")
    report["sources"][0]["directory"]=str(case.baseline)
    if not allowed:
        # An internally hash-consistent claimed zero still has to regrade.
        report.update(gate_satisfied=True,exit_code=0)
    gate_path=tmp_path/"promotion-gate.json"
    gate_path.write_text(json.dumps(report),encoding="utf-8")
    source=tmp_path/"synthetic-promotion-attestation.txt"
    source.write_text("Synthetic maintainer record; never a real baseline authorization.",encoding="utf-8")
    now=datetime.now(timezone.utc)
    approval=gov.PromotionApproval(
        baseline_id="synthetic-task",gate_hash=digest(report),
        run_manifest_hash=digest(m.model_dump(mode="json")),reviewer="synthetic-only",role="maintainer",
        created_at=now,expires_at=now+timedelta(days=1),record_ref=str(source),record_hash=file_hash(source),
    )
    approval_path=tmp_path/"synthetic-promotion-approval.json"
    approval_path.write_text(approval.model_dump_json(),encoding="utf-8")
    store=tmp_path/"synthetic-promotion-store"
    if allowed:
        record=gov.promote_baseline(store,case.baseline,gate_path,approval_path,"synthetic-task",None)
        assert record["baseline_id"]=="synthetic-task"
        assert any(r.verdict=="incomplete" for r in rows)
    else:
        with pytest.raises(ValueError,match="no longer satisfies"):
            gov.promote_baseline(store,case.baseline,gate_path,approval_path,"synthetic-task",None)
        assert not (store/"records/synthetic-task.json").exists()


@pytest.mark.parametrize("refresh_case",["business"],indirect=True)
def test_business_refresh_prepare_is_readonly_and_uses_the_new_exact_canonical_policy(refresh_case):
    c=refresh_case
    before=bytes_at(c.store)
    request=c.prepare()
    assert bytes_at(c.store)==before
    value=json.loads(request.read_bytes())["request"]
    assert value["activatable"] is True and not value["release_eligible"]
    assert Path(value["policy"]["path"]).name=="core-business-initial-baseline-v1.json"
    frozen=json.loads(Path(value["gate"]["path"]).read_bytes())
    assert frozen["schema_version"]==3 and frozen["frozen_review"]["schema_version"]==3
    assert not (c.store/"evaluator-refreshes").exists()


@pytest.mark.parametrize("refresh_case",["business"],indirect=True)
@pytest.mark.parametrize("mutation",["policy_changed","missing_hard_rule","old_review_version"])
def test_business_refresh_prepare_rejects_policy_weakening_and_implicit_old_review_migration(
    refresh_case,tmp_path,mutation
):
    c=refresh_case
    policy=json.loads((ROOT/"evals/policies/core-business-initial-baseline-v1.json").read_bytes())
    if mutation=="policy_changed":
        policy["rationale"]+=" altered"
    elif mutation=="missing_hard_rule":
        policy["absolute"][0]["rules"].pop()
    else:
        current=json.loads(c.review.read_bytes())
        current["schema_version"]=2
        c.review.write_text(json.dumps(current),encoding="utf-8")
    altered=tmp_path/"altered-policy.json"
    altered.write_text(json.dumps(policy),encoding="utf-8")
    before=bytes_at(c.store)
    with pytest.raises(ValueError):
        c.refresh.qualification(c.run,c.original,altered,c.review,tmp_path/"experiment.json",c.now)
    assert bytes_at(c.store)==before


@pytest.mark.parametrize("version,expected",[(2,0),(3,1)])
def test_review_dependency_discovery_only_follows_fields_of_the_declared_version(
    monkeypatch,version,expected
):
    from offerpilot.evaluation.core_release_reads import DependencyClosure

    closure=DependencyClosure()
    authorities,refs=[],[]
    monkeypatch.setattr(closure,"_authority",lambda item: authorities.append(item))
    monkeypatch.setattr(closure,"_bound",lambda ref: refs.append(ref) or Path("."))
    value=dict(
        record_ref="synthetic-unread",record_hash="a"*64,
        review_pack_ref="synthetic-pack-unread",review_pack_hash="b"*64,
        confirmation_source=dict(path="synthetic-confirmation-unread",sha256="c"*64),
    )
    closure.review(dict(schema_version=version,quality_reviews=[value]))
    assert len(authorities)==expected and len(refs)==2*expected
