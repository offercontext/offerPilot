from pathlib import Path
import sys
from uuid import uuid4

import pytest

from offerpilot.evaluation.contracts import PlannedTrial, VariantConfig
from offerpilot.evaluation.registry import file_hash, load_suite
from offerpilot.evaluation.runner import ROOT, run_trial, snapshot
from offerpilot.evaluation.reporting import atomic_json


def execute_case(tmp_path: Path, item: str, fault: str = "none", shutdown_seconds: int | None = None):
    suite, specs = load_suite(ROOT / "evals/suites/harness-p0-v1.json", ROOT / "evals")
    declaration = next(i for i in suite.execution_items if i.execution_item_id == item)
    p = PlannedTrial(run_id=uuid4(), variant_id="candidate", execution_item_id=item,
        mode="deterministic", repeat_index=1, case_id=declaration.case_id, case_version=1,
        spec_hash=file_hash(ROOT / "evals" / declaration.spec_path),
        fixture_hash=file_hash(ROOT / "evals/fixtures/synthetic-interviews-v1.json"),
        coverage_kind=declaration.coverage_kind, required_rules=specs[item].required_rules)
    variant = VariantConfig.model_validate({"variant_id": "candidate", "checkout": str(ROOT),
                                           "interpreter": sys.executable, "fault": fault})
    spec = specs[item]
    if shutdown_seconds is not None:
        spec = spec.model_copy(update={"evaluator_limits": spec.evaluator_limits.model_copy(
            update={"shutdown_seconds": shutdown_seconds})})
    expected = snapshot(ROOT)
    result, evidence = run_trial(p, spec, variant, tmp_path / "private", expected)
    atomic_json(tmp_path / "trial-result.json", result.model_dump(mode="json"))
    atomic_json(tmp_path / "trial-config.json", {"plan": p.model_dump(mode="json"),
        "variant": variant.model_dump(mode="json"), "source": expected, "spec": spec.model_dump(mode="json")})
    return result, evidence


@pytest.mark.parametrize("item", ["E01.runtime_read", "E04.runtime_edit", "E06.legacy_saved"])
def test_legal_http_case(tmp_path: Path, item: str) -> None:
    result, evidence = execute_case(tmp_path, item)
    assert result.verdict == "pass", (result, evidence.fragment_errors)
    assert all(h["product_file"].replace("\\", "/").startswith(str(ROOT / "src").replace("\\", "/"))
               for h in evidence.facts["handshakes"])
    if item == "E06.legacy_saved":
        assert len(evidence.facts["replays"]) == 2
        assert evidence.facts["terminal_pending"] is None
        assert all(r.get("pending_action") is None for r in evidence.facts["replays"])
        assert not [c for c in evidence.calls if c["epoch"] == 2]


@pytest.mark.parametrize("fault,rule", [
    ("wrong_target", "business.no_unexpected_delta"),
    ("wrong_payload", "approval.effective_payload"),
    ("duplicate_effect", "operation.single_effect"),
    ("unapproved_compensation", "approval.before_commit"),
    ("bad_receipt", "receipt.edited_no_model_continuation"),
])
def test_actual_http_negative_control(tmp_path: Path, fault: str, rule: str) -> None:
    result, evidence = execute_case(tmp_path, "E04.runtime_edit", fault)
    assert result.verdict == "fail", (result, evidence.fragment_errors)
    assert rule in result.known_failures
    assert result.faulted and evidence.facts["fault_witnesses"]


def test_actual_response_tail_write_is_observed_until_owned_exit(tmp_path: Path) -> None:
    result, evidence = execute_case(tmp_path, "E01.runtime_read", "late_write")
    assert evidence.facts["driver_completed"] and evidence.facts["control_closed"]
    boundary = next(m["seq"] for m in evidence.markers if m["name"] == "driver_finished")
    assert any(d.seq > boundary for d in evidence.deltas)
    assert result.verdict == "fail" and "business.zero_committed_dml" in result.known_failures


def test_actual_shutdown_failure_cannot_pass(tmp_path: Path) -> None:
    result, evidence = execute_case(tmp_path, "E01.runtime_read", "shutdown_timeout", shutdown_seconds=1)
    assert evidence.facts["driver_completed"]
    assert evidence.coverage.status == "gap" and evidence.fragment_errors["control"]
    assert result.verdict == "incomplete" and result.consumption["model_attempts"] is None
    assert result.consumption["model_attempts_observed_lower_bound"] > 0
