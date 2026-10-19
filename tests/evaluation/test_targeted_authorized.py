"""Formal controller with temporary authority/account and loopback-only product I/O.

No test reads the real registry, credential or shared account. Simulated grants and
gate/price receipts are explicitly simulation-only and cannot pass production.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import csv
import time

import pytest
from typer.testing import CliRunner

from offerpilot.evaluation import core_authorized as auth
from offerpilot.evaluation import core_targeted as offline
from offerpilot.evaluation.cli import app
from offerpilot.evaluation.core_contracts import RunGrant
from offerpilot.evaluation.core_planning import read_plan
from offerpilot.evaluation.core_storage import load, seal
from offerpilot.evaluation.core_targeted_account import (
    AuthorityRegistry, FileBudgetAccount, paid_lock,
)
from offerpilot.evaluation.core_targeted_ledger import stage_trial, TargetedLedger
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.reporting import atomic_json
from offerpilot.evaluation.runner import ROOT
from scripts.evaluation_subject.process_tree import ProcessTree


class LoopbackBackend(offline.OfflineBackend):
    report_filename = "targeted-report.json"
    require_owned_exit = True


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    original = Path.open
    def guarded(path, *args, **kwargs):
        normalized = str(path).replace("\\", "/").lower()
        if "harness-policy-binding-fix" in normalized or normalized.endswith("desktop/key.txt"):
            raise AssertionError("test attempted a real credential/account file")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path,"open",guarded)
    with offline.loopback_only():
        yield


@pytest.fixture
def setup(tmp_path, monkeypatch, request, frozen_inputs):
    private_fixture(tmp_path)
    variants = tmp_path / "variants.json"
    atomic_json(variants, [dict(variant_id="candidate", checkout=str(ROOT), interpreter=sys.executable)])
    dummy = tmp_path / "dummy"
    dummy.mkdir()
    for name in ("dummy-profile.json","simulation-grant.json"):
        (dummy / name).write_bytes((frozen_inputs[0] / name).read_bytes())
    parent = frozen_inputs[1]
    if "full_controller" not in request.node.name:
        # The measured frozen interpreter/dependencies are cached only in policy
        # unit tests. Full controller integrations keep the actual probe/handshake.
        from offerpilot.evaluation import core_planning
        monkeypatch.setattr(core_planning,"target_environment",lambda variant:parent.variants["candidate"]["runtime"])
    registry_root = tmp_path / "trusted"
    registry_root.mkdir()
    registry = AuthorityRegistry(registry_root)
    account_path = tmp_path / "account/task-budget.json"
    historic = tmp_path / "history/unknown-reservations.jsonl"
    historic.parent.mkdir()
    historic.write_text(json.dumps(dict(allowed=True, reserve_cny="7.605790", billing="unknown")) + "\n")
    atomic_json(account_path, dict(authorized_cny="10", retained_cny="7.605790", allocated_cny="0",
        refund_cny="0", unallocated_cny="2.394210", closed_runs=[dict(path=str(historic),
        hash=file_hash(historic), retained_cny="7.605790", allowed_requests=1, state="closed")]))
    evidence = tmp_path / "simulation-authorization.txt"
    evidence.write_text("TEST ONLY; not a human grant or price quotation", encoding="utf-8")
    config = dict(schema_version=3, authority_id="test-authority", account_path=str(account_path),
                  genesis_account_sha256=file_hash(account_path), authorized_cny="10",
                  max_session_cny="2.394210", authorization_evidence=auth.ref(evidence))
    atomic_json(registry_root / "targeted-authorities/test-authority.json", config)
    price = tmp_path / "price.json"
    profile = parent.provider_profile
    assert profile
    atomic_json(price, dict(schema_version=3, profile_hash=parent.provider_profile_hash,
        automatic_retries=0, currency="CNY", source_url=profile.price_reference,
        checked_at=profile.price_checked_at.isoformat(),
        valid_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        source_capture=auth.ref(evidence), simulation_only=True))
    gate = tmp_path / "gate.json"
    source = {k:parent.variants["candidate"][k] for k in ("commit","diff_hash","files_hash","lock_hash")}
    command = tmp_path / "simulated-gate-command.json"
    atomic_json(command, dict(exit_code=0, source_snapshot=source, native_process_returned=True,
        execution_complete=True, simulation_only=True, source_after=source, source_unchanged=True,
        cwd=str(ROOT),argv=["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(ROOT / "scripts/release-gate.ps1")]))
    atomic_json(gate, dict(schema_version=3, source_snapshot=source, exit_code=0,
        native_process_returned=True, execution_complete=True, simulation_only=True,
        command_receipt=auth.ref(command)))
    ports = auth.Assembly(registry, LoopbackBackend({}))
    monkeypatch.setattr(auth, "assembly", lambda: ports)
    return dict(root=tmp_path, dummy=dummy, variants=variants, ports=ports, config=config,
                account=account_path, price=price, gate=gate, evidence=evidence)


def private_fixture(tmp_path):
    if os.name == "nt":
        # Only the brand-new empty pytest fixture root. Never change global ACLs.
        assert tmp_path.is_dir() and not tmp_path.is_symlink() and not list(tmp_path.iterdir())
        identity = subprocess.check_output(["whoami","/user","/fo","csv","/nh"],text=True)
        sid = next(csv.reader([identity.strip()]))[1]
        subprocess.run(["icacls",str(tmp_path),"/inheritance:r","/grant:r",
            f"*{sid}:(OI)(CI)F","*S-1-5-18:(OI)(CI)F","*S-1-5-32-544:(OI)(CI)F"],
            check=True,capture_output=True)


@pytest.fixture(scope="module")
def frozen_inputs(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("measured-inputs")
    private_fixture(tmp_path)
    variants = tmp_path / "variants.json"
    atomic_json(variants, [dict(variant_id="candidate", checkout=str(ROOT), interpreter=sys.executable)])
    dummy = tmp_path / "dummy"
    offline.prepare(ROOT / "evals/suites/harness-core-v1.json", variants, dummy, list(offline.SELECTED))
    parent = read_plan(dummy / "parent/run-plan.json")
    return dummy,parent


def prepare(setup, *, max_requests=27, cap=1, cli=False, authorization="test-authorization"):
    dummy, root = setup["dummy"], setup["root"]
    grant = RunGrant.model_validate_json((dummy / "simulation-grant.json").read_bytes())
    grant = grant.model_copy(update={"max_requests":max_requests, "max_cny":float(cap)})
    grant_path = root / "test-grant.json"
    atomic_json(grant_path, grant.model_dump(mode="json"))
    output = root / "session"
    args = [ROOT / "evals/suites/harness-core-v1.json", setup["variants"], dummy / "dummy-profile.json",
            grant_path, setup["price"], setup["gate"], output, list(offline.SELECTED),
            "test-authority", authorization]
    if cli:
        cli_args = ["targeted-plan"]
        for flag, value in zip(["suite","variants","profile","grant","price-audit","gate-receipt","output"],args[:7]):
            cli_args += ["--"+flag, str(value)]
        cli_args += ["--authority","test-authority","--authorization",authorization]
        for item in offline.SELECTED:
            cli_args += ["--select",item]
        result = CliRunner().invoke(app, cli_args)
        (root / "plan-cli.txt").write_text(result.output, encoding="utf-8")
        assert result.exit_code == 0, (result.output, result.exception)
    else:
        auth.prepare(*args)
    path = output / "targeted-authorized-plan.json"
    envelope = offline.read(path)
    atomic_json(setup["ports"].registry.root / f"targeted-approvals/{authorization}.json", dict(
        schema_version=3, authorization_id=authorization, authority_id="test-authority",
        plan_hash=envelope["plan_hash"], grant_hash=envelope["plan"]["grant_hash"],
        grant_id=str(grant.grant_id), simulation_only=True, expires_at=grant.expires_at.isoformat(),
        authorization_evidence=auth.ref(setup["evidence"])))
    return path


def account(setup, **kwargs):
    return FileBudgetAccount(setup["ports"].registry, setup["config"], **kwargs)


def test_preflight_is_read_only_and_freezes_full13(setup):
    path = prepare(setup)
    before = {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}
    report = auth.preflight(path)
    assert report["ready"] and report["max_requests_including_probe"] == 27
    assert report["network_requests"] == 0 and report["new_reservation_cny"] == "0"
    assert before == {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}
    assert not account(setup).lock_path.exists()
    assert len(read_plan(path.parent / "parent/run-plan.json").planned_trials) == 13


@pytest.mark.parametrize("field", ["plan_hash","grant_hash","grant_id","authority_id","simulation_only","expires_at"])
def test_changed_approval_refuses_before_claim_allocation_or_network(setup, field):
    path = prepare(setup)
    approval_path = setup["ports"].registry.root / "targeted-approvals/test-authorization.json"
    value = offline.read(approval_path)
    value[field] = False if field == "simulation_only" else "1970-01-01T00:00:00+00:00" if field == "expires_at" else "wrong"
    atomic_json(approval_path, value)
    before = setup["account"].read_bytes()
    with pytest.raises((ValueError, OSError)):
        auth.execute(path)
    assert setup["account"].read_bytes() == before
    assert not (setup["ports"].registry.root / "grant-claims").exists()
    assert not account(setup).lock_path.exists()


@pytest.mark.parametrize("damage", ["history_hash","history_amount","outside_account","insufficient_balance"])
def test_account_identity_and_legacy_history_fail_closed(setup, damage):
    path = prepare(setup)
    saved = offline.read(setup["account"])
    if damage == "history_hash":
        Path(saved["closed_runs"][0]["path"]).write_text("changed")
    elif damage == "history_amount":
        saved["closed_runs"][0]["retained_cny"] = "0"
        atomic_json(setup["account"],saved)
    elif damage == "outside_account":
        saved["unallocated_cny"] = "10"
        atomic_json(setup["account"],saved)
    else:
        # An existing allocation is never treated as new free budget.
        account(setup).allocate("another-session", Decimal("2"))
    with pytest.raises((ValueError,OSError)):
        auth.execute(path)
    assert not (setup["ports"].registry.root / "grant-claims").exists()


def test_relative_account_binding_rejected_before_plan_or_allocation(setup):
    config_path = setup["ports"].registry.root / "targeted-authorities/test-authority.json"
    value = offline.read(config_path)
    value["account_path"] = "account/task-budget.json"
    atomic_json(config_path,value)
    with pytest.raises(ValueError,match="absolute"):
        prepare(setup)
    assert not (setup["root"] / "session").exists()
    assert not account(setup).lock_path.exists()


@pytest.mark.parametrize("point", ["before_intent","after_intent","after_account_replace","after_receipt"])
def test_allocation_crash_windows_never_reset_or_regrant(setup, point):
    def fault(name):
        if name == point:
            raise OSError("injected durable boundary")
    with pytest.raises(OSError):
        account(setup,fault=fault).allocate("first",Decimal("1"))
    restored = account(setup)
    if point == "before_intent":
        assert restored.inspect()[0]["allocated_cny"] == "0"
    elif point == "after_receipt":
        assert restored.allocate("first",Decimal("1")) == "allocate"
        with pytest.raises(ValueError,match="open"):
            restored.allocate("second",Decimal("1"))
    else:
        with pytest.raises(ValueError,match="transaction"):
            restored.inspect()


def test_unknown_reservations_retained_and_close_is_once_only(setup):
    acc = account(setup)
    with paid_lock(acc.lock_path):
        acc.allocate("test-binding",Decimal("1"))
        ledger = setup["root"] / "unknown.jsonl"
        ledger.write_text(json.dumps(dict(allowed=True,reserve_cny="0.25",usage=None))+"\n")
        with pytest.raises(ValueError,match="not proven"):
            acc.close("test-binding",Decimal("1"),ledger,exit_proven=False)
        acc.close("test-binding",Decimal("1"),ledger,exit_proven=True)
        saved = acc.inspect()[0]
        assert saved["retained_cny"] == "7.855790" and saved["unallocated_cny"] == "2.144210"
        assert saved["refund_cny"] == "0" and saved["closed_runs"][0]["retained_cny"] == "7.605790"
        with pytest.raises(ValueError):
            acc.close("test-binding",Decimal("1"),ledger,exit_proven=True)


def test_claim_binds_authorization_and_grant_not_just_uuid(setup):
    registry = setup["ports"].registry
    binding = dict(plan_hash="a",run_id="one")
    path = registry.claims("auth-one","01234567-0000-4000-8000-000000000001",binding)
    assert registry.claims("auth-one","01234567-0000-4000-8000-000000000001",binding) == path
    for authorization,grant in [("auth-one","01234567-0000-4000-8000-000000000002"),
                                ("auth-two","01234567-0000-4000-8000-000000000001")]:
        with pytest.raises(ValueError):
            registry.claims(authorization,grant,dict(plan_hash="b",run_id="two"))


def test_partial_claim_stays_blocked(setup):
    registry = setup["ports"].registry
    incomplete = registry.root / "targeted-claims/auth-one"
    incomplete.mkdir(parents=True)
    with pytest.raises(ValueError,match="partial"):
        registry.claims("auth-one","01234567-0000-4000-8000-000000000001",{})


@pytest.mark.parametrize("inherit_only",[False,True])
def test_mutable_authority_child_with_other_writer_is_rejected(setup,inherit_only):
    if os.name != "nt":
        target = setup["ports"].registry.root / "unsafe"
        target.mkdir()
        target.chmod(0o777)
    else:
        target = setup["ports"].registry.root / "unsafe"
        target.mkdir()
        permissions = "(OI)(CI)(IO)(M)" if inherit_only else "(M)"
        subprocess.run(["icacls",str(target),"/grant","*S-1-1-0:"+permissions],check=True,capture_output=True)
    with pytest.raises(ValueError,match="writ"):
        setup["ports"].registry.check(target / "future-claim" / "anchor.json")
    assert not (target / "future-claim").exists()


def test_empty_ledger_initialization_failure_keeps_allocation_for_audit(setup,monkeypatch):
    path = prepare(setup)
    def fail(*a,**kw):
        raise OSError("initial durable ledger creation failed")
    with monkeypatch.context() as patch:
        patch.setattr(auth,"TargetedLedger",fail)
        with pytest.raises(OSError):
            auth.execute(path)
    assert account(setup).inspect()[0]["allocated_cny"] == "1.0"
    assert (path.parent / "session-state.json").exists()
    with pytest.raises(ValueError,match="ledger/anchor missing"):
        auth.execute(path)
    assert account(setup).inspect()[0]["allocated_cny"] == "1.0"
    assert not (path.parent / "simulation-private").exists()


@pytest.mark.parametrize("point",["after_intent","after_account_replace"])
def test_close_transaction_failure_cannot_release_unknown_reservation(setup,point):
    acc = account(setup)
    acc.allocate("first",Decimal("1"))
    ledger = setup["root"] / "unknown-send.jsonl"
    ledger.write_text(json.dumps(dict(allowed=True,reserve_cny="0.25"))+"\n")
    def fault(name):
        if name == point:
            raise OSError("injected close commit interruption")
    with pytest.raises(OSError):
        account(setup,fault=fault).close("first",Decimal("1"),ledger,exit_proven=True)
    with pytest.raises(ValueError,match="transaction"):
        account(setup).allocate("new-session",Decimal("1"))
    assert ledger.read_bytes() and offline.read(setup["account"])["refund_cny"] == "0"


@pytest.mark.parametrize("damage",["price","gate","grant","probe","selection","parent"])
def test_frozen_prerequisites_tampering_never_allocates(setup,damage):
    path = prepare(setup)
    if damage in {"probe","selection"}:
        value = offline.read(path)
        if damage == "probe":
            value["plan"]["probe"]["message"] = "different diagnostic"
        else:
            value["plan"]["selected"][-1] = value["plan"]["selected"][0]
        value["plan_hash"] = digest(value["plan"])
        atomic_json(path,value)
    else:
        target = {"price":"price-audit.json","gate":"gate-receipt.json","grant":"run-grant.json",
                  "parent":"parent/run-plan.json"}[damage]
        with (path.parent / target).open("ab") as stream:
            stream.write(b" ")
    before = setup["account"].read_bytes()
    with pytest.raises((ValueError,OSError)):
        auth.execute(path)
    assert setup["account"].read_bytes() == before and not account(setup).lock_path.exists()


def test_production_backend_probe_has_independent_parameter_without_offline_profile(monkeypatch):
    calls = []
    def trial(*args,**kwargs):
        calls.append(kwargs)
        return None
    monkeypatch.setattr(auth,"run_trial",trial)
    backend = auth.RuntimeBackend()
    backend.run("probe",None,None,None,None)
    backend.run("E01.runtime_read",None,None,None,None)
    assert calls == [dict(diagnostic_probe=True),dict(diagnostic_probe=False)]


def test_successful_non_gate_command_cannot_stand_in_for_full_gate(setup):
    command_path = setup["root"] / "simulated-gate-command.json"
    command = offline.read(command_path)
    command["argv"] = [sys.executable,"-m","pytest"]
    atomic_json(command_path,command)
    gate = offline.read(setup["gate"])
    gate["command_receipt"] = auth.ref(command_path)
    atomic_json(setup["gate"],gate)
    with pytest.raises(ValueError,match="gate command"):
        prepare(setup)
    assert not account(setup).lock_path.exists()


@pytest.mark.parametrize("damage",["none","missing","content","hash","partial_tmp","closed_tmp"])
def test_export_seal_never_overwrites_and_requires_exact_closed_refs(tmp_path,damage):
    # Deliberately unimportable toy payloads: no real parent manifest/trial result
    # is generated by a simulation or by this persistence unit test.
    payloads = {"run-manifest.json":{"unimportable_simulation_unit":True},
                "trial-results.json":[{"unimportable_simulation_unit":True}]}
    if damage == "partial_tmp":
        path = tmp_path / "run-manifest.json.tmp"
        path.write_bytes(b"partial-attempt")
        with pytest.raises(ValueError,match="partial"):
            auth.export_files(tmp_path,payloads,write=True)
        assert path.read_bytes() == b"partial-attempt"
        assert not (tmp_path / "run-manifest.json").exists()
        return
    refs = auth.export_files(tmp_path,payloads,write=True)
    if damage == "missing":
        (tmp_path / "trial-results.json").unlink()  # this fixture's own new file only
    elif damage == "content":
        atomic_json(tmp_path / "trial-results.json",[dict(conflicting=True)])
    elif damage == "hash":
        refs["trial-results.json"]["sha256"] = "0"*64
    elif damage == "closed_tmp":
        (tmp_path / "run-manifest.json.tmp").write_bytes(b"partial-closed-attempt")
    before = {str(p):file_hash(p) for p in tmp_path.iterdir() if p.is_file()}
    if damage == "none":
        assert auth.export_files(tmp_path,payloads,write=False,expected_refs=refs) == refs
    else:
        with pytest.raises(ValueError,match="export"):
            auth.export_files(tmp_path,payloads,write=False,expected_refs=refs)
        if damage == "content":
            with pytest.raises(ValueError,match="conflicting"):
                auth.export_files(tmp_path,payloads,write=True)
    assert before == {str(p):file_hash(p) for p in tmp_path.iterdir() if p.is_file()}


@pytest.mark.parametrize("changed",["stage_states","matrix","selected_status","exit_code",
                                   "transport_observation","reserved_cny"])
def test_closed_report_tampering_refuses_even_when_stage_list_is_empty(setup,monkeypatch,changed):
    from offerpilot.evaluation.core_targeted_kernel import SessionOutcome
    path = prepare(setup)
    def no_send(manifest,grant,directory,binding,selected,backend,make_ledger):
        ledger = make_ledger(True)
        report = dict(schema_version=3,stage_states={},matrix=[],selected_status="incomplete",
                      exit_code=3,reserved_cny="0",simulation_only=True,allowed_requests=0)
        atomic_json(directory / backend.report_filename,report)
        return SessionOutcome(report,ledger)
    monkeypatch.setattr(auth,"run_session",no_send)
    report = auth.execute(path)
    assert report["allocation_state"] == "closed"
    result_path = path.parent / "targeted-report.json"
    value = offline.read(result_path)
    value[changed] = [] if changed == "matrix" else {} if changed == "stage_states" else "forged"
    if value == offline.read(result_path):
        value[changed] = {"foreign":{}} if changed == "stage_states" else [{"foreign":True}]
    atomic_json(result_path,value)
    before = setup["account"].read_bytes()
    with pytest.raises(ValueError):
        auth.execute(path)
    assert setup["account"].read_bytes() == before


def test_shared_account_exact_lock_excludes_other_process(setup):
    path = account(setup).lock_path
    code = "from pathlib import Path; import sys,time; from offerpilot.evaluation.core_targeted_account import paid_lock;\nwith paid_lock(Path(sys.argv[1])):\n print('held',flush=True); time.sleep(30)"
    tree = ProcessTree([sys.executable,"-B","-c",code,str(path)],stdout=subprocess.PIPE,env=os.environ.copy(),text=True)
    proc = tree.process
    try:
        assert proc.stdout and proc.stdout.readline().strip() == "held"
        with pytest.raises(OSError):
            with paid_lock(path):
                pytest.fail("account lock overlap")
    finally:
        tree.kill()
        tree.wait(timeout=10)
        tree.close()
        if proc.stdout:
            proc.stdout.close()
    with paid_lock(path):
        pass


def test_trusted_anchor_failure_keeps_local_reservation_and_open_allocation(setup, monkeypatch):
    path = prepare(setup)
    def checkpoint(self, name):
        if name == "before_spend_anchor":
            raise OSError("external anchor failed before ACK")
    monkeypatch.setattr(FileBudgetAccount,"checkpoint",checkpoint)
    def session(manifest,grant,directory,binding,selected,backend,make_ledger):
        ledger = make_ledger(not (directory / "run-budget.jsonl").exists())
        ledger.stage = "probe"
        child = stage_trial(manifest,"probe",True)
        key = child.model_dump(mode="json",include={"run_id","variant_id","execution_item_id","mode","repeat_index"})
        ledger.reserve(dict(purpose="provider_send",trial_key=key,epoch=1,seq=1,input_bytes=100,output_cap=10))
        pytest.fail("ACK must not be permitted")
    monkeypatch.setattr(auth,"run_session",session)
    with pytest.raises(OSError):
        auth.execute(path)
    ledger = path.parent / "run-budget.jsonl"
    rows = [json.loads(line) for line in ledger.read_bytes().splitlines()]
    assert len(rows) == 1 and rows[0]["allowed"] is True
    assert account(setup).inspect()[0]["allocated_cny"] == "1.0"
    with pytest.raises(ValueError,match="anchor"):
        auth.execute(path)
    assert len(ledger.read_bytes().splitlines()) == 1


def synthetic_saved(manifest, directory, stage, *, tree=True, reader=True, status="completed", attempts=None):
    """Synthetic control facts for controller policy tests, never real capability evidence."""
    from offerpilot.evaluation.core_graders import grade
    child = stage_trial(manifest,stage,True)
    key = child.model_dump(mode="json",include={"run_id","variant_id","execution_item_id","mode","repeat_index"})
    private = directory / "simulation-private" / stage
    private.mkdir(parents=True)
    atomic_json(private / ".eval-trial.json",key)
    variant = manifest.variants["candidate"]
    handshake = dict(checkout_sha=variant["commit"],interpreter=variant["interpreter"],
        **variant["runtime"],product_file=str(ROOT / "src/offerpilot/__init__.py"),database_file=str(private / "data.db"))
    evidence = dict(trial_key=key,provider_attempts=attempts or [],input_observations=[dict(category="provider_response",status=200)],
        facts=dict(simulation_only=True,control_closed=True,owned_subjects_exited=tree,control_readers_exited=reader,
                   terminal_state="completed",handshakes=[handshake]))
    evidence_hash = seal(private,evidence)
    result = grade(child,load(private,evidence_hash),"completed",evidence_hash,str(private))
    result_path = private / "trial-result.json"
    atomic_json(result_path,result.model_dump(mode="json"))
    parent = next(p for p in manifest.planned_trials if p.execution_item_id == child.execution_item_id)
    return dict(status=status,parent_trial_key=list(parent.identity()),child_trial_key=list(child.identity()),
        result=result.model_dump(mode="json"),result_file=str(result_path),result_file_hash=file_hash(result_path),
        probe_ok=True,control_records_closed=True,owned_subjects_exited=tree)


def test_closed_unknown_control_eof_keeps_partial_intents_on_readonly_resume(setup,monkeypatch):
    from offerpilot.evaluation.core_targeted_kernel import SessionOutcome
    from offerpilot.evaluation.core_targeted_evidence import check_request_bindings
    from offerpilot.evaluation.core_runner import missing
    path = prepare(setup)
    def recovered_unknown(manifest,grant,directory,binding,selected,backend,make_ledger):
        ledger = make_ledger(True)
        stage = offline.SELECTED[0]
        child = stage_trial(manifest,stage,True)
        key = child.model_dump(mode="json",include={"run_id","variant_id","execution_item_id","mode","repeat_index"})
        attempts = [dict(trial_key=key,epoch=1,seq=5,input_bytes=100,output_cap=10,purpose="provider_send")]
        saved = synthetic_saved(manifest,directory,stage,status="interrupted_unknown",attempts=attempts)
        saved["request_observation_binding"] = check_request_bindings(stage,dict(provider_attempts=attempts),[],complete=False)
        matrix = [dict(parent_trial_key=list(p.identity()),selected=p.execution_item_id in offline.SELECTED,
                       result=saved["result"] if p.execution_item_id == stage else missing(p,"not_run").model_dump(mode="json"))
                  for p in manifest.planned_trials]
        return SessionOutcome(dict(schema_version=3,stage_states={stage:saved},matrix=matrix,selected_status="incomplete",
            exit_code=3,reserved_cny="0",allowed_requests=0,simulation_only=True),ledger)
    monkeypatch.setattr(auth,"run_session",recovered_unknown)
    report = auth.execute(path)
    assert report["allocation_state"] == "closed" and report["selected_status"] == "incomplete"
    assert report["stage_states"][offline.SELECTED[0]]["request_observation_binding"]["coverage"] == "partial"
    before = {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}
    assert auth.execute(path) == report
    assert before == {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}


@pytest.mark.parametrize("tree",[None,False])
def test_target_unknown_worker_exit_stops_all_later_targets_after_successful_probe(setup,tree):
    from offerpilot.evaluation.core_targeted_kernel import run_session
    path = prepare(setup)
    envelope = offline.read(path)
    directory,binding = path.parent,envelope["plan_hash"]
    manifest = read_plan(directory / "parent/run-plan.json")
    grant = RunGrant.model_validate_json((directory / "run-grant.json").read_bytes())
    ledger = TargetedLedger(manifest,grant,directory,binding,initialize=True,simulation_only=True)
    probe = synthetic_saved(manifest,directory,"probe")
    target = synthetic_saved(manifest,directory,offline.SELECTED[0],tree=tree,status="started")
    atomic_json(directory / "session-state.json",dict(schema_version=3,simulation_only=True,plan_hash=binding,
        ledger=ledger.receipt(),stages={"probe":probe,offline.SELECTED[0]:target}))
    backend = setup["ports"].backend
    def must_not_run(*a,**kw):
        pytest.fail("unknown owned worker exit allowed later target")
    backend.run = must_not_run
    outcome = run_session(manifest,grant,directory,binding,list(offline.SELECTED),backend,
        lambda initialize: TargetedLedger(manifest,grant,directory,binding,initialize=initialize,simulation_only=True))
    assert outcome.report["exit_code"] == 3 and outcome.ledger.requests == 0
    assert len(outcome.report["stage_states"]) == 2
    assert outcome.report["stage_states"][offline.SELECTED[0]]["status"] == "interrupted_unknown"


@pytest.mark.parametrize("reader",[None,False])
def test_functional_pass_with_unproven_control_exit_keeps_account_open_and_nonzero(setup,monkeypatch,reader):
    from offerpilot.evaluation.core_targeted_kernel import SessionOutcome
    path = prepare(setup)
    def no_send(manifest,grant,directory,binding,selected,backend,make_ledger):
        ledger = make_ledger(True)
        saved = synthetic_saved(manifest,directory,"probe",reader=reader)
        report = dict(schema_version=3,stage_states={"probe":saved},matrix=[],selected_status="pass",
                      exit_code=0,reserved_cny="0",simulation_only=True,allowed_requests=0)
        return SessionOutcome(report,ledger)
    monkeypatch.setattr(auth,"run_session",no_send)
    report = auth.execute(path)
    assert report["selected_status"] == "pass" and report["session_status"] == "incomplete"
    assert report["exit_code"] == 3 and report["allocation_state"] == "open"
    assert account(setup).inspect()[0]["allocated_cny"] == "1.0"


def test_older_closed_session_after_later_account_version_fails_read_only(setup,monkeypatch):
    from offerpilot.evaluation.core_targeted_kernel import SessionOutcome
    path = prepare(setup)
    def no_send(manifest,grant,directory,binding,selected,backend,make_ledger):
        return SessionOutcome(dict(stage_states={},matrix=[],selected_status="incomplete",
            exit_code=3,reserved_cny="0",allowed_requests=0,simulation_only=True),make_ledger(True))
    monkeypatch.setattr(auth,"run_session",no_send)
    auth.execute(path)
    acc = account(setup)
    acc.allocate("later-session",Decimal("0.5"))
    empty = setup["root"] / "later-ledger.jsonl"
    empty.write_text("")
    acc.close("later-session",Decimal("0.5"),empty,exit_proven=True)
    before = setup["account"].read_bytes()
    with pytest.raises(ValueError,match="version"):
        auth.execute(path)
    assert setup["account"].read_bytes() == before


def received(report):
    return sum(1 for saved in report["stage_states"].values() if "result_file" in saved
        for obs in load(Path(saved["result"]["private_directory"]),saved["result"]["evidence_hash"])["input_observations"]
        if obs.get("category") == "offline_provider_received")


def test_full_controller_normal_nine_cli_and_closed_resume(setup):
    path = prepare(setup,cli=True)
    cli = CliRunner()
    for command in ["targeted-preflight","targeted-run"]:
        with offline.loopback_only():
            result = cli.invoke(app,[command,"--plan",str(path)])
        (path.parent / (command+".txt")).write_text(result.output,encoding="utf-8")
        assert result.exit_code == 0,(result.output,result.exception)
    report = offline.read(path.parent / "targeted-report.json")
    assert report["selected_status"] == "pass" and report["parent_denominator"] == 13
    assert report["allocation_state"] == "closed" and report["real_provider_requests"] == 0
    assert received(report) == report["allowed_requests"] == 25
    assert report["transport_observation"]["provider_responses_observed"] == 25
    assert report["transport_observation"]["reserved_without_observed_response"] == 0
    assert sum(r["stage_status"] == "not_run" for r in report["matrix"]) == 4
    assert not (path.parent / "run-manifest.json").exists()
    before = {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}
    with offline.loopback_only():
        resumed = cli.invoke(app,["targeted-run","--plan",str(path)])
    assert resumed.exit_code == 0,(resumed.output,resumed.exception)
    assert before == {str(p):file_hash(p) for p in setup["root"].rglob("*") if p.is_file()}
    assert account(setup).inspect()[0]["allocated_cny"] == "0"


def test_full_controller_27_boundary_real_loopback_receives_and_no_resend(setup):
    setup["ports"].backend.faults.update({s:"extra_reads" for s in offline.SELECTED})
    path = prepare(setup)
    with offline.loopback_only():
        report = auth.execute(path)
    assert report["allowed_requests"] == received(report) == 27
    assert report["exit_code"] == 3 and report["selected_status"] == "incomplete"
    assert report["stopped_after_reservation_denial"] is True
    assert report["allocation_state"] == "closed"
    budget = (path.parent / "run-budget.jsonl").read_bytes()
    with offline.loopback_only():
        resumed = auth.execute(path)
    assert resumed == report and (path.parent / "run-budget.jsonl").read_bytes() == budget


@pytest.mark.parametrize("fault",["http_error","timeout"])
def test_full_controller_failure_retains_reservations_and_transport_unknowns(setup,fault):
    setup["ports"].backend.faults[offline.SELECTED[0]] = fault
    path = prepare(setup,max_requests=2)
    with offline.loopback_only():
        report = auth.execute(path)
    assert report["allowed_requests"] >= 2 and report["reserved_cny"] != "0"
    assert report["selected_status"] != "pass"
    assert report["allocation_state"] == "closed"
    if fault == "timeout":
        assert report["transport_observation"]["reserved_without_observed_response"] >= 1
    before = (path.parent / "run-budget.jsonl").read_bytes()
    with offline.loopback_only():
        assert auth.execute(path) == report
    assert (path.parent / "run-budget.jsonl").read_bytes() == before


@pytest.mark.parametrize("window",["raw","pre_ack"])
def test_full_controller_parent_kill_and_competing_controller_preserve_account(setup,window):
    path = prepare(setup,max_requests=3)
    code = """
from pathlib import Path
import sys,time,runpy,os,json
from offerpilot.evaluation import core_authorized as a,core_targeted as o
from offerpilot.evaluation.core_targeted_account import AuthorityRegistry,FileBudgetAccount
from typer.testing import CliRunner
from offerpilot.evaluation.cli import app
Base=runpy.run_path('tests/evaluation/test_targeted_authorized.py')['LoopbackBackend']
class Backend(Base):
 def run(self,stage,*args):
  result=super().run(stage,*args)
  if stage=='E01.runtime_read' and sys.argv[4]=='raw':
   Path(sys.argv[3]).write_text('raw-sealed')
   time.sleep(120)
  return result
ports=a.Assembly(AuthorityRegistry(Path(sys.argv[1])),Backend({}))
a.assembly=lambda:ports
Path(sys.argv[2]).with_name('controller-info-'+sys.argv[4]+'.json').write_text(json.dumps(dict(actual_pid=os.getpid(),parent_pid=os.getppid())))
if sys.argv[4]=='pre_ack':
 def checkpoint(self,name):
  if name=='after_spend_anchor':
   anchor=o.read(ports.registry.root/'targeted-claims/test-authorization/spend-anchor.json')
   if anchor['ledger']['requests']==2:
    Path(sys.argv[3]).write_text('pre-ACK pause')
    time.sleep(120)
 FileBudgetAccount.checkpoint=checkpoint
with o.loopback_only():
 result=CliRunner().invoke(app,['targeted-run','--plan',sys.argv[2]])
 print(result.output,flush=True)
 raise SystemExit(result.exit_code)
"""
    signal = path.parent / "raw-sealed.signal"
    with (path.parent / "parent.stdout").open("w") as stdout, (path.parent / "parent.stderr").open("w") as stderr:
        tree = ProcessTree([sys.executable,"-B","-c",code,str(setup["ports"].registry.root),str(path),str(signal),window],
                                stdout=stdout,stderr=stderr,env=os.environ.copy())
        proc, exited = tree.process, False
        try:
            until = time.monotonic()+180
            while not signal.exists() and time.monotonic()<until:
                assert proc.poll() is None
                time.sleep(0.1)
            assert signal.exists(), "actual Runtime did not seal target before timeout"
            # Same CLI controller in another native process must fail before new
            # claims/allocation/sends while the first controller holds the account.
            before_competitor = {str(p):file_hash(p) for p in setup["ports"].registry.root.rglob("*") if p.is_file()}
            budget_before = (path.parent / "run-budget.jsonl").read_bytes()
            account_before = setup["account"].read_bytes()
            second_code = code[:code.index("if sys.argv[4]=='pre_ack':")] + "\nwith o.loopback_only():\n result=CliRunner().invoke(app,['targeted-run','--plan',sys.argv[2]])\n print(result.output)\n raise SystemExit(result.exit_code)\n"
            with (path.parent / "competitor.stdout").open("w") as second_out, (path.parent / "competitor.stderr").open("w") as second_err:
                competitor = ProcessTree([sys.executable,"-B","-c",second_code,str(setup["ports"].registry.root),str(path),str(signal),"competitor"],
                                        stdout=second_out,stderr=second_err,env=os.environ.copy())
                try:
                    second_exit = competitor.wait(timeout=60)
                except BaseException:
                    competitor.kill()
                    competitor.wait(timeout=15)
                    raise
                finally:
                    competitor.close()
            assert second_exit == 2
            assert before_competitor == {str(p):file_hash(p) for p in setup["ports"].registry.root.rglob("*") if p.is_file()}
            assert setup["account"].read_bytes() == account_before
            assert (path.parent / "run-budget.jsonl").read_bytes() == budget_before
            atomic_json(path.parent / "competitor-receipt.json",dict(exit_code=second_exit,requests_added=0,
                        account_changed=False,claims_changed=False))
            tree.kill()
            parent_exit = tree.wait(timeout=15)
            exited = True
            actual = offline.read(path.parent / ("controller-info-"+window+".json"))
            atomic_json(path.parent / "parent-kill-receipt.json",dict(exit_code=parent_exit,window=window,
                launcher_pid=proc.pid,actual_controller_pid=actual["actual_pid"],owned_tree_exit_proven=True,
                scope="only this ProcessTree/Job and descendants"))
        finally:
            if not exited:
                tree.kill()
                tree.wait(timeout=15)
            tree.close()
    assert account(setup).inspect()[0]["allocated_cny"] == "1.0"
    ledger_before = (path.parent / "run-budget.jsonl").read_bytes()
    with offline.loopback_only():
        report = auth.execute(path)
    assert report["allowed_requests"] == (3 if window == "raw" else 2)
    assert received(report) == (3 if window == "raw" else 1)
    assert report["exit_code"] == 3 and report["allocation_state"] == ("closed" if window == "raw" else "open")
    assert report["stage_states"][offline.SELECTED[0]]["status"] == "interrupted_unknown"
    if window == "raw":
        assert report["stage_states"][offline.SELECTED[0]]["result_recovered_from_sealed_disk"] is True
    else:
        assert len(report["stage_states"]) == 2
        assert report["transport_observation"]["reserved_without_observed_response"] == 1
        assert account(setup).inspect()[0]["allocated_cny"] == "1.0"
    assert (path.parent / "run-budget.jsonl").read_bytes() == ledger_before
