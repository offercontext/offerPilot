from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.companion_ai_controller import history as h
from scripts.companion_ai_controller import policy as p
from scripts.companion_ai_controller.verify import make_rehearsal_bundle


def verify(value):
    return h.verify_history(value, registration=p.fixture_registration(), current_run=910001)


def snapshot():
    return make_rehearsal_bundle()["history"]


def test_real_four_archives_preserve_total_and_missing_requests():
    assert h.verified_archives() == h.Balance(48, 411934, 0)
    assert "requests.jsonl" not in h.fourth.MEMBERS
    assert len(h.LEGACY_RUNS) == 4


@pytest.mark.parametrize("field,value", [("complete", False), ("complete", 1), ("unknown_paid_runs", [123]),
                                         ("active_runs", [123]), ("mode", "production")])
def test_unresolved_legacy_scan(field, value):
    data = snapshot()
    data["legacy_scan"][field] = value
    with pytest.raises(p.Blocked, match="LEGACY_SCAN_UNRESOLVED"):
        verify(data)


def test_legacy_checkpoint_cannot_be_omitted_or_changed():
    data = snapshot()
    data["legacy_checkpoints"].pop()
    with pytest.raises(p.Blocked, match="LEGACY_CHAIN_INVALID"):
        verify(data)


@pytest.mark.parametrize("status", ["completed", "cancelled", "in_progress", "waiting"])
@pytest.mark.parametrize("different_key", [False, True])
def test_any_prior_paid_start_halts_even_new_nonce(status, different_key):
    data = snapshot()
    row = deepcopy(data["controller_runs"][0])
    row.update(id=910000, status=status, paid_started=True,
               batch_key="different-nonce" if different_key else p.BATCH_KEY)
    data["controller_runs"][0]["number"] = 2
    data["controller_runs"].insert(0, row)
    with pytest.raises(p.Blocked, match="PAID_STARTED_GLOBAL_HALT"):
        verify(data)


def test_pending_other_run_blocks_serial_admission():
    data = snapshot()
    row = deepcopy(data["controller_runs"][0])
    row.update(id=910000, status="waiting")
    data["controller_runs"][0]["number"] = 2
    data["controller_runs"].insert(0, row)
    with pytest.raises(p.Blocked, match="PRIOR_RUN_UNRESOLVED"):
        verify(data)


@pytest.mark.parametrize("field,value", [("number", 2), ("attempt", 2), ("id", True),
    ("workflow_id", 999), ("controller_sha", "b"*40), ("paid_started", 1)])
def test_history_gap_or_mutation(field, value):
    data = snapshot()
    data["controller_runs"][0][field] = value
    with pytest.raises(p.Blocked):
        verify(data)


def test_independent_dryrun_never_exempts_real_paid_start():
    data = snapshot()
    reg = p.fixture_registration()
    data["dry_runs"] = [{"id": 920001, "number": 1, "workflow_id": reg.dry_run_workflow_id,
        "attempt": 1, "kind": "dry_run", "status": "completed", "paid_started": False,
        "batch_key": None, "controller_sha": reg.controller_sha}]
    assert verify(data) == h.Balance()
    data["dry_runs"][0]["paid_started"] = True
    with pytest.raises(p.Blocked, match="DRY_RUN_IS_NOT_PAID_EXEMPTION"):
        verify(data)


def test_master_stub_schema_is_not_controller_schema():
    data = snapshot()
    row = deepcopy(data["controller_runs"][0])
    row.update(id=910000, kind="master_stub", batch_key=None, status="completed")
    data["controller_runs"][0]["number"] = 2
    data["controller_runs"].insert(0, row)
    assert verify(data) == h.Balance()
    data["controller_runs"][0]["kind"] = "unregistered"
    with pytest.raises(p.Blocked, match="UNKNOWN_HISTORY_SCHEMA"):
        verify(data)
