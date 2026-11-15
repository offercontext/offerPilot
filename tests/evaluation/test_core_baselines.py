from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import pytest

from offerpilot.evaluation import core_governance as governance
from offerpilot.evaluation.registry import digest, file_hash
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import policy, review, rows


def prepare(manifest, tmp_path, monkeypatch, baseline_id="initial-test"):
    result = rows(manifest)
    directory = tmp_path / "run"
    monkeypatch.setattr(governance, "read_run", lambda p: (manifest, result))
    report = governance.gate_core(
        [manifest],
        [result],
        policy(manifest),
        review(manifest, tmp_path),
        "initial_baseline",
        "enforce",
    )
    report["sources"][0]["directory"] = str(directory)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(report), encoding="utf-8")
    record = tmp_path / "approval-record.txt"
    record.write_text(
        "Synthetic maintainer approval for baseline regression only.", encoding="utf-8"
    )
    now = datetime.now(timezone.utc)
    approval = governance.PromotionApproval(
        baseline_id=baseline_id,
        gate_hash=digest(report),
        run_manifest_hash=digest(manifest.model_dump(mode="json")),
        reviewer="synthetic maintainer",
        role="maintainer",
        created_at=now,
        expires_at=now + timedelta(days=1),
        record_ref=str(record),
        record_hash=file_hash(record),
    )
    path = tmp_path / "approval.json"
    path.write_text(approval.model_dump_json(), encoding="utf-8")
    return directory, gate, path


def test_initial_promotion_is_immutable_and_revocation_preserves_history(
    manifest, tmp_path, monkeypatch
):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    store = tmp_path / "baselines"
    record = governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
    assert governance.load_baseline(store, "initial-test") == record
    with pytest.raises((ValueError, FileExistsError)):
        governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
    ref = tmp_path / "revoke.txt"
    ref.write_text("Synthetic revocation record.")
    before = (store / "records/initial-test.json").read_bytes()
    governance.revoke_baseline(store, "initial-test", "synthetic risk", str(ref), file_hash(ref))
    assert (store / "records/initial-test.json").read_bytes() == before
    with pytest.raises(ValueError, match="revoked"):
        governance.load_baseline(store, "initial-test")


def test_concurrent_promotion_has_one_winner_and_no_partial_json(manifest, tmp_path, monkeypatch):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    store = tmp_path / "baselines"

    def promote():
        try:
            governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
            return "ok"
        except (FileExistsError, ValueError):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: promote(), range(2)))
    assert results.count("ok") == 1
    assert json.loads((store / "current.json").read_bytes())["baseline_id"] == "initial-test"
    assert governance.load_baseline(store, "initial-test")["baseline_id"] == "initial-test"


@pytest.mark.parametrize("boundary", ["record", "current"])
def test_interrupted_promotion_recovers_only_committed_journal(
    manifest, tmp_path, monkeypatch, boundary
):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    store = tmp_path / "baselines"
    original = governance.atomic_json

    def interrupted(path, value):
        if boundary == "current" and path.name == "current.json" and value["baseline_id"]:
            raise RuntimeError("synthetic interruption after durable event")
        original(path, value)
        if boundary == "record" and path.parent.name == "records":
            raise RuntimeError("synthetic interruption before commit event")

    monkeypatch.setattr(governance, "atomic_json", interrupted)
    with pytest.raises(RuntimeError):
        governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
    monkeypatch.setattr(governance, "atomic_json", original)
    with governance.store_lock(store):
        assert governance.recover_current(store) == (
            "initial-test" if boundary == "current" else None
        )
    if boundary == "record":
        with pytest.raises(ValueError, match="not committed"):
            governance.load_baseline(store, "initial-test")
    else:
        assert governance.load_baseline(store, "initial-test")["baseline_id"] == "initial-test"


def test_kernel_lock_releases_after_real_process_death(tmp_path):
    code = "from pathlib import Path; import os; from offerpilot.evaluation.core_governance import store_lock; c=store_lock(Path(__import__('sys').argv[1])); c.__enter__(); os._exit(9)"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], timeout=30)
    assert result.returncode == 9
    with governance.store_lock(tmp_path):
        assert governance.recover_current(tmp_path) is None


def test_revoked_current_can_be_replaced_with_explicit_initial_approval(
    manifest, tmp_path, monkeypatch
):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    store = tmp_path / "baselines"
    governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
    ref = tmp_path / "revoke.txt"
    ref.write_text("Synthetic recovery authorization.")
    governance.revoke_baseline(store, "initial-test", "synthetic reason", str(ref), file_hash(ref))
    assert json.loads((store / "current.json").read_bytes())["baseline_id"] is None
    other = json.loads(approval.read_bytes()) | {"baseline_id": "replacement-test"}
    replacement_ref = tmp_path / "replacement-approval.txt"
    replacement_ref.write_text("Synthetic separate approval for replacement-test after revocation.")
    other.update(record_ref=str(replacement_ref), record_hash=file_hash(replacement_ref))
    approval.write_text(json.dumps(other))
    governance.promote_baseline(store, directory, gate, approval, "replacement-test", None)
    assert governance.load_baseline(store, "replacement-test")["baseline_id"] == "replacement-test"


def test_revocation_uses_same_lock_and_rejects_path_aliases(manifest, tmp_path, monkeypatch):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    store = tmp_path / "baselines"
    governance.promote_baseline(store, directory, gate, approval, "initial-test", None)
    ref = tmp_path / "revoke.txt"
    ref.write_text("Synthetic revocation.")
    with governance.store_lock(store):
        with pytest.raises(ValueError, match="busy"):
            governance.revoke_baseline(store, "initial-test", "reason", str(ref), file_hash(ref))
    with pytest.raises(ValueError, match="invalid baseline id"):
        governance.revoke_baseline(store, "./initial-test", "reason", str(ref), file_hash(ref))
    assert governance.load_baseline(store, "initial-test")


@pytest.mark.parametrize(
    "mutation", ["observe", "missing_review", "tampered_private", "stale_approval"]
)
def test_promotion_rereads_evidence_and_rejects_forged_or_stale_success(
    manifest, tmp_path, monkeypatch, mutation
):
    directory, gate, approval = prepare(manifest, tmp_path, monkeypatch)
    report = json.loads(gate.read_bytes())
    if mutation == "observe":
        report["mode"] = "observe"
    elif mutation == "missing_review":
        report["frozen_review"]["independent_reviews"] = []
    elif mutation == "tampered_private":
        monkeypatch.setattr(governance, "read_run", lambda p: (manifest, []))
    if mutation != "tampered_private":
        gate.write_text(json.dumps(report), encoding="utf-8")
        a = json.loads(approval.read_bytes())
        a["gate_hash"] = digest(report)
        if mutation == "stale_approval":
            a["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        approval.write_text(json.dumps(a), encoding="utf-8")
    with pytest.raises(ValueError):
        governance.promote_baseline(
            tmp_path / "baselines", directory, gate, approval, "initial-test", None
        )
