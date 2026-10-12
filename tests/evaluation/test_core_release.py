"""Release contribution regrades evidence and rejects false zero exits."""

import json

import pytest

from offerpilot.evaluation import core_release as release
from offerpilot.evaluation.core_governance import ExperimentSpec, gate_core, product_hash
from offerpilot.evaluation.registry import digest, file_hash
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import policy, review, rows
from tests.evaluation.test_core_governance import real_manifest


def setup_release(manifest, tmp_path, monkeypatch):
    root = release.ROOT
    changed = manifest.model_copy(deep=True)
    changed.variants["candidate"]["checkout"] = str(root)
    paths = {}
    inputs = {
        "suite": json.loads((root / "evals/suites/harness-core-v1.json").read_bytes()),
        "policy": policy(changed).model_dump(mode="json"),
        "variants": [
            {k: v[k] for k in ("variant_id", "checkout", "interpreter", "fault")}
            for v in changed.variants.values()
        ],
        "review": review(changed, tmp_path).model_dump(mode="json"),
        "experiment": ExperimentSpec(
            variables=[dict(name="product_snapshot", baseline=product_hash(changed), candidate=product_hash(changed))],
            rationale="Synthetic approved candidate reuse without a product change",
        ).model_dump(mode="json"),
    }
    for name, data in inputs.items():
        p = tmp_path / (name + ".json")
        p.write_text(json.dumps(data), encoding="utf-8")
        paths[name] = p
    # The suite hash is byte-bound, even when semantically equal JSON is reformatted.
    data = inputs["policy"] | {"suite_hash": file_hash(paths["suite"])}
    paths["policy"].write_text(json.dumps(data), encoding="utf-8")
    changed = changed.model_copy(update={"suite_hash": file_hash(paths["suite"])})
    record = dict(
        baseline_id="synthetic",
        sources=[
            dict(
                directory=str(tmp_path / "baseline"),
                manifest_hash=digest(changed.model_dump(mode="json")),
            )
        ],
    )
    monkeypatch.setattr(release, "load_baseline", lambda *_: record)
    monkeypatch.setattr(release, "read_run", lambda *_: (changed, rows(changed)))
    monkeypatch.setattr(release, "read_plan", lambda *_: changed)
    monkeypatch.setattr(release, "create_plan", lambda *_: changed)
    monkeypatch.setattr(release, "snapshot", lambda *_: {"synthetic_snapshot": True})
    monkeypatch.setattr(release, "environment", lambda: {"synthetic_environment": True})
    monkeypatch.setattr(release, "evaluator_hash", lambda: changed.evaluator_hash)
    monkeypatch.setattr(release, "run_plan", lambda *_: pytest.fail("preflight must never execute"))
    args = [
        paths["suite"],
        paths["policy"],
        paths["variants"],
        tmp_path / "output",
        "synthetic",
        tmp_path / "store",
        paths["review"],
        paths["experiment"],
    ]
    return changed, paths, args


def test_preflight_is_zero_network_and_checks_dirty_source(manifest, tmp_path, monkeypatch):
    _, _, args = setup_release(manifest, tmp_path, monkeypatch)
    value = release.preflight(*args)
    path = args[3] / "release-preflight.json"
    assert release.read_preflight(path) == value
    monkeypatch.setattr(release, "snapshot", lambda *_: {"changed_dirty_diff": True})
    with pytest.raises(ValueError, match="source/environment changed"):
        release.read_preflight(path)


def test_approved_same_run_reuse_preflight_execute_and_receipt(manifest, tmp_path, monkeypatch):
    m, paths, args = setup_release(manifest, tmp_path, monkeypatch)
    candidate_hash = product_hash(m)
    paths["experiment"].write_text(json.dumps(dict(
        variables=[dict(name="product_snapshot", baseline=candidate_hash, candidate=candidate_hash)],
        rationale="Synthetic same approved candidate reused",
    )), encoding="utf-8")
    first = next(p.identity() for p in m.planned_trials if p.variant_id == "baseline")
    actual_rows = rows(m, failure=first)
    monkeypatch.setattr(release, "read_run", lambda *_: (m, actual_rows))
    release.preflight(*args, reuse=[tmp_path / "baseline"])
    path = args[3] / "release-preflight.json"
    report = release.execute(path)
    assert report["gate_satisfied"]
    comparison = report["relative"]["comparisons"][0]
    assert comparison["baseline_variant"] == "candidate"
    assert {g["classification"] for g in comparison["groups"]} == {"passing"}
    assert release.verify_receipt(path)["evaluation_satisfied"]


def test_release_real_requirement_blocks_missing_profile_and_grant_before_execute(manifest, tmp_path, monkeypatch):
    m = real_manifest(tmp_path)
    _, _, args = setup_release(m, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="required real mode needs profile and authorized grant"):
        release.preflight(*args)
    assert not args[3].exists()


@pytest.mark.parametrize(
    "mutation", ["wrong_candidate", "diagnostic", "stale_review", "initial_policy"]
)
def test_preflight_rejects_invalid_scope_before_execution(
    manifest, tmp_path, monkeypatch, mutation
):
    _, paths, args = setup_release(manifest, tmp_path, monkeypatch)
    if mutation in {"wrong_candidate", "diagnostic"}:
        raw = json.loads(paths["variants"].read_bytes())
        raw[1]["checkout" if mutation == "wrong_candidate" else "fault"] = (
            str(tmp_path) if mutation == "wrong_candidate" else "wrong_target"
        )
        paths["variants"].write_text(json.dumps(raw))
    elif mutation == "stale_review":
        raw = json.loads(paths["review"].read_bytes())
        raw["independent_reviews"][0]["candidate_snapshot_hash"] = "0" * 64
        paths["review"].write_text(json.dumps(raw))
    else:
        raw = json.loads(paths["policy"].read_bytes()) | {"purposes": ["initial_baseline"]}
        paths["policy"].write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        release.preflight(*args)


@pytest.mark.parametrize("mutation", ["missing", "observe", "initial", "forged_success"])
def test_receipt_regrades_and_rejects_missing_or_forged_success(
    manifest, tmp_path, monkeypatch, mutation
):
    m, paths, args = setup_release(manifest, tmp_path, monkeypatch)
    release.preflight(*args)
    preflight = args[3] / "release-preflight.json"
    gate = gate_core([m], [rows(m)], policy(m), review(m, tmp_path), "initial_baseline", "enforce")
    gate.update(
        mode="observe" if mutation == "observe" else "enforce",
        purpose="initial_baseline" if mutation == "initial" else "release",
        gate_satisfied=True,
        exit_code=0,
    )
    gate["frozen_experiment"] = json.loads(paths["experiment"].read_bytes())
    if mutation != "missing":
        (preflight.parent / "release-evaluation.json").write_text(
            json.dumps(dict(preflight_hash=file_hash(preflight), evaluation_gate=gate))
        )
    if mutation == "forged_success":
        first = next(p.identity() for p in m.planned_trials if p.variant_id == "candidate")
        monkeypatch.setattr(release, "read_run", lambda *_: (m, rows(m, failure=first)))
    with pytest.raises((ValueError, FileNotFoundError)):
        release.verify_receipt(preflight)


def test_execute_and_receipt_require_regraded_enforce_result(manifest, tmp_path, monkeypatch):
    _, _, args = setup_release(manifest, tmp_path, monkeypatch)
    release.preflight(*args)
    called = []
    monkeypatch.setattr(release, "run_plan", lambda *a: called.append(a))
    path = args[3] / "release-preflight.json"
    report = release.execute(path)
    assert len(called) == 1 and report["gate_satisfied"] and not report["release_eligible"]
    assert release.verify_receipt(path)["evaluation_satisfied"]


@pytest.mark.parametrize("mutation", ["outer_release", "gate_release", "statistics", "extra_field"])
def test_receipt_binds_complete_report_and_cannot_grant_release(
    manifest, tmp_path, monkeypatch, mutation
):
    _, _, args = setup_release(manifest, tmp_path, monkeypatch)
    release.preflight(*args)
    monkeypatch.setattr(release, "run_plan", lambda *a: None)
    path = args[3] / "release-preflight.json"
    release.execute(path)
    receipt = path.parent / "release-evaluation.json"
    raw = json.loads(receipt.read_bytes())
    if mutation == "outer_release":
        raw["release_eligible"] = True
    elif mutation == "gate_release":
        raw["evaluation_gate"]["release_eligible"] = True
    elif mutation == "statistics":
        raw["evaluation_gate"]["absolute"]["incomplete"] = ["forged statistic"]
        raw["gate_hash"] = digest(raw["evaluation_gate"])
    else:
        raw["unverified_extension"] = True
    receipt.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        release.verify_receipt(path)


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_owned_release_child_propagates_native_exit(tmp_path, code):
    import sys

    assert (
        release.owned_command(
            [sys.executable, "-c", f"raise SystemExit({code})"], 5, tmp_path / "child.log"
        )
        == code
    )


def test_owned_release_timeout_closes_real_child(tmp_path):
    import sys
    import time

    marker = tmp_path / "late.txt"
    code = "import time; from pathlib import Path; import sys; time.sleep(2); Path(sys.argv[1]).write_text('late side effect')"
    with pytest.raises(TimeoutError, match="timed out and was closed"):
        release.owned_command(
            [sys.executable, "-c", code, str(marker)], 0.2, tmp_path / "child.log"
        )
    time.sleep(2.2)
    assert not marker.exists()
