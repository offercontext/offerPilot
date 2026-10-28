"""Evidence-generation contracts; synthetic approvals never touch the real store."""

from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from offerpilot.evaluation import core_governance as governance
from offerpilot.evaluation.registry import digest
from offerpilot.evaluation.runner import ROOT
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import policy, review, rows


def test_refresh_gate_freezes_baseline_arm_without_copying_candidate_verdict(manifest, tmp_path):
    r = rows(
        manifest,
        failure=next(p.identity() for p in manifest.planned_trials if p.variant_id == "candidate"),
    )
    bundle = review(manifest, tmp_path)
    bundle = bundle.model_copy(
        update={
            "independent_reviews": [
                v.model_copy(
                    update={
                        "candidate_snapshot_hash": governance.product_hash(manifest, "baseline")
                    }
                )
                for v in bundle.independent_reviews
            ]
        }
    )
    gate = governance.gate_core(
        [manifest],
        [r],
        policy(manifest),
        bundle,
        "initial_baseline",
        "enforce",
        candidate_variant="baseline",
    )
    assert gate["candidate_variant"] == "baseline"
    assert gate["candidate_snapshot_hash"] == governance.product_hash(manifest, "baseline")
    assert gate["gate_satisfied"] is True
    assert any(v.verdict == "fail" for v in r if v.variant_id == "candidate")


def test_release_cannot_select_baseline_as_candidate(manifest, tmp_path):
    with pytest.raises(ValueError, match="candidate arm"):
        governance.gate_core(
            [manifest],
            [rows(manifest)],
            policy(manifest),
            review(manifest, tmp_path),
            "release",
            "enforce",
            candidate_variant="baseline",
        )


def test_refresh_api_requires_frozen_request_not_implementation_permission(tmp_path):
    from offerpilot.evaluation.core_refresh import activate_refresh

    request = tmp_path / "not-approval.json"
    request.write_text(
        json.dumps(dict(scope="implementation and114", is_frozen_evidence_approval=False))
    )
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, KeyError)):
        activate_refresh(request, request)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_historical_time_is_not_current_frozen_approval():
    from offerpilot.evaluation.core_refresh_contracts import valid_at

    now = datetime.now(timezone.utc)
    created, expired = now - timedelta(days=3), now - timedelta(days=2)
    assert valid_at(created, expired, created + timedelta(seconds=1))
    assert not valid_at(created, expired, now)
    assert not valid_at(created.replace(tzinfo=None), expired, now)
    assert not valid_at(created, now + timedelta(days=31), now)


def test_generation_journal_rejects_orphan_reservation_without_recovering_cache(tmp_path):
    from offerpilot.evaluation.core_refresh_store import generation_state

    directory = tmp_path / "evaluator-refreshes"
    directory.mkdir()
    reservation = directory / "orphan.reservation"
    reservation.write_text(digest({"synthetic": "partial"}))
    before = reservation.read_bytes()
    with pytest.raises(ValueError, match="audit"):
        generation_state(tmp_path, [])
    assert reservation.read_bytes() == before and not (tmp_path / "evidence-generations").exists()


def bytes_at(folder):
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def refresh_case(manifest, tmp_path, monkeypatch):
    """Synthetic component test: historical native audit and source exporter are separate seams."""
    from offerpilot.evaluation import core_refresh as refresh
    from offerpilot.evaluation.core_refresh_contracts import (
        RefreshApproval,
        RefreshRevocation,
        reference,
    )

    store, run = tmp_path / "store", tmp_path / "fresh-run"
    state = {"rows": rows(manifest)}
    original = dict(
        schema_version=2,
        baseline_id="original",
        evaluator_hash="0" * 64,
        product_snapshot_hash=governance.product_hash(manifest, "baseline"),
        sources=[dict(directory=str(tmp_path / "old-run"), manifest_hash="a" * 64)],
        run_directory=str(tmp_path / "old-run"),
        manifest_hash="a" * 64,
    )
    old_manifest = dict(variants=dict(candidate=manifest.variants["baseline"]))
    write(tmp_path / "old-run/run-manifest.json", old_manifest)
    original["manifest_hash"] = digest(old_manifest)
    original_path = write(store / "records/original.json", original)
    original_path.with_suffix(".reservation").write_text(digest(original), encoding="utf-8")
    write(
        store / "events/00000001_original.json",
        dict(
            sequence=1,
            action="promote",
            previous=None,
            baseline_id="original",
            record_hash=digest(original),
        ),
    )
    write(store / "current.json", dict(baseline_id="original"))
    (store / ".promotion-lock").write_bytes(b"0")
    for name in (
        "run-plan.json",
        "run-manifest.json",
        "trial-results.json",
        "summary.json",
        "source-files.json",
    ):
        write(run / name, dict(synthetic=True, file=name))
    write(
        run / "run-plan.json",
        dict(
            manifest=manifest.model_dump(mode="json"),
            plan_hash=digest(manifest.model_dump(mode="json")),
        ),
    )
    write(run / "run-manifest.json", manifest.model_dump(mode="json"))
    monkeypatch.setattr(governance, "read_run", lambda *_: (manifest, state["rows"]))
    monkeypatch.setattr(refresh, "read_run", lambda *_: (manifest, state["rows"]))
    monkeypatch.setattr(refresh, "source_origins", lambda *_: dict(synthetic_origin_test=True))
    history = write(tmp_path / "history.json", dict(synthetic_history=True))
    monkeypatch.setattr(
        refresh,
        "audit_history",
        lambda *args: dict(
            proof=dict(synthetic_original_audit=True),
            child=dict(synthetic_child=True),
            descriptor=reference(history),
        ),
    )
    now = datetime.now(timezone.utc)
    source = write(
        tmp_path / "independent.json",
        dict(
            schema_version=3,
            kind="evaluator-refresh-independent-record",
            reviewer="independent test reviewer",
            role="independent_reviewer",
            reviewed_variant="baseline",
            baseline_snapshot_hash=governance.product_hash(manifest, "baseline"),
            candidate_snapshot_hash=governance.product_hash(manifest),
            evaluator_hash=manifest.evaluator_hash,
            run_manifest_hash=digest(manifest.model_dump(mode="json")),
            evidence_fingerprint=refresh.evidence_fingerprint(manifest, state["rows"]),
            disposition="accepted",
            created_at=(now - timedelta(minutes=1)).isoformat(),
            expires_at=(now + timedelta(days=1)).isoformat(),
            notes="Synthetic independent source record; never an actual maintainer approval.",
        ),
    )
    record = json.loads(source.read_bytes())
    review_path = write(
        tmp_path / "review.json",
        dict(
            schema_version=2,
            independent_reviews=[
                dict(
                    reviewer=record["reviewer"],
                    role="independent_reviewer",
                    candidate_snapshot_hash=record["baseline_snapshot_hash"],
                    evaluator_hash=record["evaluator_hash"],
                    record_ref=str(source),
                    record_hash=reference(source)["sha256"],
                    disposition="accepted",
                    created_at=record["created_at"],
                    expires_at=record["expires_at"],
                )
            ],
        ),
    )
    left, right = (governance.variable_values(manifest, arm) for arm in ("baseline", "candidate"))
    experiment = write(
        tmp_path / "experiment.json",
        dict(
            variables=[
                dict(name=k, baseline=left[k], candidate=right[k])
                for k in left
                if left[k] != right[k]
            ],
            rationale="Synthetic changed variables only.",
        ),
    )
    counter = [0]

    def prepare(generation="g1"):
        counter[0] += 1
        output = tmp_path / f"prepared-{counter[0]}"
        refresh.prepare_refresh(
            store,
            "original",
            generation,
            run,
            ROOT / "evals/policies/core-initial-baseline-v6.json",
            review_path,
            experiment,
            history,
            output,
        )
        return output / "refresh-request.json"

    def authorize(request_path, kind="evaluator-refresh-approval", **changes):
        envelope = json.loads(request_path.read_bytes())
        request = envelope["request"]
        payload = dict(
            schema_version=3,
            kind=kind,
            baseline_id=request["baseline_id"],
            generation_id=request["generation_id"],
            request_hash=envelope["request_hash"],
            expected_current=request["expected_current"],
            expected_previous_generation=request["expected_previous_generation"],
            reviewer="synthetic maintainer",
            role="maintainer",
            created_at=(now - timedelta(minutes=1)).isoformat(),
            expires_at=(now + timedelta(days=1)).isoformat(),
        )
        payload.update(
            dict(source_variant="baseline")
            if kind == "evaluator-refresh-approval"
            else dict(reason="synthetic revocation")
        )
        payload.update(changes)
        model = RefreshApproval if kind == "evaluator-refresh-approval" else RefreshRevocation
        payload = model.model_validate(
            payload | dict(record_ref="unused", record_hash="0" * 64)
        ).model_dump(mode="json", exclude={"record_ref", "record_hash"})
        counter[0] += 1
        authority = write(
            tmp_path / f"authority-{counter[0]}.json",
            dict(schema_version=3, kind="maintainer-source-record", record=payload),
        )
        return write(
            tmp_path / f"approval-{counter[0]}.json",
            payload | dict(record_ref=str(authority), record_hash=reference(authority)["sha256"]),
        )

    return SimpleNamespace(
        store=store,
        run=run,
        manifest=manifest,
        state=state,
        original=original,
        prepare=prepare,
        authorize=authorize,
        review=review_path,
        independent=source,
        now=now,
        refresh=refresh,
    )


def test_prepare_is_readonly_and_activation_preserves_product_pointer_and_approval(refresh_case):
    c = refresh_case
    before = bytes_at(c.store)
    request = c.prepare()
    assert bytes_at(c.store) == before
    approval = c.authorize(request)
    record = c.refresh.activate_refresh(request, approval)
    resolved = c.refresh.resolve_baseline(c.store, "original")
    assert resolved["baseline_id"] == "original" and resolved["source_variant"] == "baseline"
    assert resolved["product_snapshot_hash"] == c.original["product_snapshot_hash"]
    assert resolved["refresh_generation"]["generation_id"] == "g1"
    assert record["approval"]["request_hash"] == json.loads(request.read_bytes())["request_hash"]
    assert json.loads((c.store / "current.json").read_bytes()) == dict(baseline_id="original")
    assert (c.store / "records/original.json").read_bytes() == before["records/original.json"]
    assert json.loads((c.store / "events").glob("00000002*.json").__next__().read_bytes())[
        "committed_at"
    ]


def test_refresh_dual_cas_rejects_stale_parallel_request_without_partial_write(refresh_case):
    c = refresh_case
    first, second = c.prepare("g1"), c.prepare("g2")
    one, two = c.authorize(first), c.authorize(second)
    c.refresh.activate_refresh(first, one)
    before = bytes_at(c.store)
    with pytest.raises(ValueError, match="compare-and-swap"):
        c.refresh.activate_refresh(second, two)
    assert bytes_at(c.store) == before


@pytest.mark.parametrize("phase", ["record", "journal", "cache", "cache_tmp"])
def test_interrupted_generation_fails_closed_without_implicit_repair(
    refresh_case, monkeypatch, phase
):
    c = refresh_case
    request = c.prepare()
    approval = c.authorize(request)
    original_atomic = c.refresh.atomic_json

    def interrupted(path, value):
        if (phase == "record" and path.parent.name == "evaluator-refreshes") or (
            phase in {"cache", "cache_tmp"} and path.parent.name == "evidence-generations"
        ):
            if phase == "cache_tmp":
                write(path.with_suffix(".json.tmp"), dict(partial=True))
            raise OSError("injected write interruption")
        original_atomic(path, value)

    monkeypatch.setattr(c.refresh, "atomic_json", interrupted)
    if phase == "journal":
        monkeypatch.setattr(
            governance,
            "append_store_event",
            lambda *_: (_ for _ in ()).throw(OSError("injected commit interruption")),
        )
    with pytest.raises(OSError, match="interruption"):
        c.refresh.activate_refresh(request, approval)
    before = bytes_at(c.store)
    with pytest.raises(ValueError, match="audit"):
        c.refresh.resolve_baseline(c.store, "original")
    assert bytes_at(c.store) == before
    assert "evaluator-refreshes/g1.reservation" in before


def test_revoke_latest_generation_has_no_fallback_to_older_generation(refresh_case):
    c = refresh_case
    first = c.prepare("g1")
    c.refresh.activate_refresh(first, c.authorize(first))
    second = c.prepare("g2")
    c.refresh.activate_refresh(second, c.authorize(second))
    revocation = c.authorize(
        second, kind="evaluator-refresh-revocation", expected_previous_generation="g2"
    )
    c.refresh.revoke_refresh(c.store, revocation)
    assert (c.store / "evaluator-refreshes/g1.json").is_file()
    assert (c.store / "evaluator-refreshes/g2.json").is_file()
    with pytest.raises(ValueError, match="specifically approved"):
        c.refresh.resolve_baseline(c.store, "original")


def test_original_product_revocation_invalidates_all_generations(refresh_case, tmp_path):
    c = refresh_case
    request = c.prepare()
    c.refresh.activate_refresh(request, c.authorize(request))
    authority = tmp_path / "product-revocation.txt"
    authority.write_text("synthetic maintainer revocation")
    from offerpilot.evaluation.registry import file_hash

    governance.revoke_baseline(
        c.store, "original", "synthetic revoke", str(authority), file_hash(authority)
    )
    with pytest.raises(ValueError, match="not current"):
        c.refresh.resolve_baseline(c.store, "original")
    assert (c.store / "evaluator-refreshes/g1.json").is_file()


@pytest.mark.parametrize(
    "mutation",
    ["raw", "request_hash", "source_record", "review_wrong_arm", "plan_checksum", "foreign_plan"],
)
def test_wrong_frozen_binding_is_rejected_before_store_write(refresh_case, mutation):
    c = refresh_case
    request = c.prepare()
    approval = c.authorize(request)
    if mutation == "raw":
        (c.run / "trial-results.json").write_text("changed raw bytes")
    elif mutation == "request_hash":
        value = json.loads(request.read_bytes())
        value["request_hash"] = "f" * 64
        write(request, value)
    elif mutation == "source_record":
        value = json.loads(approval.read_bytes())
        write(Path(value["record_ref"]), dict(scope="implementation only"))
    elif mutation == "review_wrong_arm":
        data = json.loads(c.independent.read_bytes())
        data["reviewed_variant"] = "candidate"
        write(c.independent, data)
    else:
        from uuid import uuid4

        data = json.loads((c.run / "run-plan.json").read_bytes())
        if mutation == "plan_checksum":
            data["plan_hash"] = "0" * 64
        else:
            new_id = str(uuid4())
            data["manifest"]["run_id"] = new_id
            for trial in data["manifest"]["planned_trials"]:
                trial["run_id"] = new_id
            data["manifest"]["execution_order"] = [
                [new_id, *key[1:]] for key in data["manifest"]["execution_order"]
            ]
            data["plan_hash"] = digest(data["manifest"])
        write(c.run / "run-plan.json", data)
    before = bytes_at(c.store)
    with pytest.raises(ValueError):
        c.refresh.activate_refresh(request, approval)
    assert bytes_at(c.store) == before


@pytest.mark.parametrize("bad", ["failed", "unknown", "not_run", "missing", "duplicate"])
def test_baseline_bad_or_missing_results_never_activate(refresh_case, bad):
    c = refresh_case
    request = c.prepare()
    approval = c.authorize(request)
    row = c.state["rows"][0]
    if bad == "missing":
        c.state["rows"].pop(0)
    elif bad == "duplicate":
        c.state["rows"][0] = c.state["rows"][1]
    elif bad == "not_run":
        c.state["rows"][0] = row.model_copy(
            update={"execution_status": "not_run", "verdict": "incomplete"}
        )
    else:
        rule = row.rules[0].model_copy(update={"status": "fail" if bad == "failed" else "unknown"})
        c.state["rows"][0] = row.model_copy(
            update={
                "rules": [rule, *row.rules[1:]],
                "verdict": "fail" if bad == "failed" else "incomplete",
            }
        )
    before = bytes_at(c.store)
    with pytest.raises(ValueError):
        c.refresh.activate_refresh(request, approval)
    assert bytes_at(c.store) == before


@pytest.mark.parametrize("expiry", ["approval", "review"])
@pytest.mark.parametrize("stage", ["regrade", "record"])
def test_expiry_during_final_regrade_cannot_commit_using_earlier_time(
    refresh_case, monkeypatch, expiry, stage
):
    c = refresh_case
    request = c.prepare()
    approval = c.authorize(
        request,
        **({"expires_at": (c.now + timedelta(days=14)).isoformat()} if expiry == "review" else {}),
    )
    clock = {"value": datetime.now(timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["value"]

    monkeypatch.setattr(c.refresh, "datetime", Clock)
    count = [0]

    def delayed(*_):
        count[0] += 1
        if count[0] == 2 and stage == "regrade":
            clock["value"] = c.now + timedelta(days=2)
        return c.manifest, c.state["rows"]

    monkeypatch.setattr(c.refresh, "read_run", delayed)
    original_atomic = c.refresh.atomic_json

    def write_then_expire(path, value):
        original_atomic(path, value)
        if path.parent.name == "evaluator-refreshes" and stage == "record":
            clock["value"] = c.now + timedelta(days=2)

    monkeypatch.setattr(c.refresh, "atomic_json", write_then_expire)
    with pytest.raises(ValueError, match="stale|expired"):
        c.refresh.activate_refresh(request, approval)
    assert len(governance.store_events(c.store)) == 1
    assert (c.store / "evaluator-refreshes/g1.reservation").is_file() is (stage == "record")


@pytest.mark.parametrize("target", ["approval", "request", "raw"])
def test_changed_frozen_input_during_regrade_is_rejected_before_store_write(
    refresh_case, monkeypatch, target
):
    c = refresh_case
    request = c.prepare()
    approval = c.authorize(request)
    count = [0]

    def changing(*_):
        count[0] += 1
        if count[0] == 2:
            path = {"approval": approval, "request": request, "raw": c.run / "summary.json"}[target]
            path.write_text("{}", encoding="utf-8")
        return c.manifest, c.state["rows"]

    monkeypatch.setattr(c.refresh, "read_run", changing)
    before = bytes_at(c.store)
    with pytest.raises(ValueError, match="changed"):
        c.refresh.activate_refresh(request, approval)
    assert bytes_at(c.store) == before


def test_foreign_product_generation_revocation_cannot_poison_store(refresh_case):
    c = refresh_case
    request = c.prepare()
    c.refresh.activate_refresh(request, c.authorize(request))
    other = c.original | dict(baseline_id="other")
    path = write(c.store / "records/other.json", other)
    path.with_suffix(".reservation").write_text(digest(other), encoding="utf-8")
    governance.append_store_event(
        c.store,
        dict(action="promote", previous="original", baseline_id="other", record_hash=digest(other)),
    )
    write(c.store / "current.json", dict(baseline_id="other"))
    revocation = c.authorize(
        request,
        kind="evaluator-refresh-revocation",
        baseline_id="other",
        expected_current="other",
        expected_previous_generation=None,
    )
    before = bytes_at(c.store)
    with pytest.raises(ValueError, match="revocation target"):
        c.refresh.revoke_refresh(c.store, revocation)
    assert bytes_at(c.store) == before


def test_historical_review_time_cannot_bypass_candidate_release(manifest, tmp_path):
    with pytest.raises(ValueError, match="only for deterministic"):
        governance.gate_core(
            [manifest],
            [rows(manifest)],
            policy(manifest),
            review(manifest, tmp_path),
            "release",
            "enforce",
            review_at=datetime.now(timezone.utc) - timedelta(days=1),
        )


@pytest.mark.parametrize("mutation", ["checksum", "foreign_run"])
def test_prepare_rejects_unrelated_or_bad_plan_before_freezing_request(refresh_case, mutation):
    from uuid import uuid4

    c = refresh_case
    data = json.loads((c.run / "run-plan.json").read_bytes())
    if mutation == "checksum":
        data["plan_hash"] = "0" * 64
    else:
        run_id = str(uuid4())
        data["manifest"]["run_id"] = run_id
        for row in data["manifest"]["planned_trials"]:
            row["run_id"] = run_id
        data["manifest"]["execution_order"] = [
            [run_id, *key[1:]] for key in data["manifest"]["execution_order"]
        ]
        data["plan_hash"] = digest(data["manifest"])
    write(c.run / "run-plan.json", data)
    before = bytes_at(c.store)
    with pytest.raises(ValueError, match="checksum|differs from its frozen plan"):
        c.prepare()
    assert bytes_at(c.store) == before


def test_two_native_competitors_commit_exactly_one_generation(refresh_case, tmp_path):
    c = refresh_case
    first, second = c.prepare("g1"), c.prepare("g2")
    approvals = [c.authorize(p) for p in (first, second)]
    manifest_path = write(tmp_path / "worker-manifest.json", c.manifest.model_dump(mode="json"))
    row_path = write(
        tmp_path / "worker-rows.json", [r.model_dump(mode="json") for r in c.state["rows"]]
    )
    worker = """
import json,sys,time
from pathlib import Path
from contextlib import contextmanager
from offerpilot.evaluation import core_refresh as r
from offerpilot.evaluation.core_contracts import RunManifestV2,TrialResultV2
p,approval,m,rows,barrier,token=map(Path,sys.argv[1:])
manifest=RunManifestV2.model_validate_json(m.read_bytes())
results=[TrialResultV2.model_validate(x) for x in json.loads(rows.read_bytes())]
request=json.loads(p.read_bytes())['request']
historical=json.loads(Path(request['historical_proof']['path']).read_bytes())
r.read_run=lambda *_:(manifest,results)
r.audit_history=lambda *_:historical
r.source_origins=lambda *_:dict(synthetic_origin_test=True)
original=r.gov.store_lock
@contextmanager
def simultaneous(store):
 (barrier/('ready-'+str(token))).write_text('ready')
 deadline=time.monotonic()+20
 while len(list(barrier.glob('ready-*')))<2:
  if time.monotonic()>deadline:raise TimeoutError('barrier')
  time.sleep(.01)
 with original(store):
  (barrier/('acquired-'+str(token))).write_text('exclusive')
  time.sleep(.25)
  yield
r.gov.store_lock=simultaneous
try:
 result=r.activate_refresh(p,approval)
 print(json.dumps(dict(generation=result['generation_id'],committed=True)))
except ValueError as exc:
 print(json.dumps(dict(committed=False,error=str(exc))))
 sys.exit(2)
"""
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    children = []
    try:
        for n, p in enumerate((first, second)):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        worker,
                        str(p),
                        str(approvals[n]),
                        str(manifest_path),
                        str(row_path),
                        str(barrier),
                        str(n),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
            )
        receipts = []
        for child in children:
            stdout, stderr = child.communicate(timeout=45)
            receipts.append(dict(exit_code=child.returncode, stdout=stdout, stderr=stderr))
        write(tmp_path / "native-competition-receipts.json", receipts)
        assert sorted(x["exit_code"] for x in receipts) == [0, 2], receipts
        assert (
            len(list(barrier.glob("ready-*"))) == 2 and len(list(barrier.glob("acquired-*"))) == 1
        )
        assert (
            len(
                [
                    e
                    for e in governance.store_events(c.store)
                    if e["action"] == "evaluator_evidence_refresh"
                ]
            )
            == 1
        )
        state = c.refresh.strict_state(c.store)
        assert state[2]["original"]["generation_id"] in {"g1", "g2"}
        assert len(list((c.store / "evaluator-refreshes").glob("*.json"))) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
