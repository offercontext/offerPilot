from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from offerpilot.evaluation.contracts import ExecutionItem, PlannedTrial, RunManifest
from offerpilot.evaluation.registry import load_suite

ROOT = Path(__file__).resolve().parents[2]


def plan(rule_ids: list[str] | None = None) -> PlannedTrial:
    return PlannedTrial(run_id=uuid4(), variant_id="candidate", execution_item_id="E01.runtime_read",
                        mode="deterministic", repeat_index=1, case_id="E01", case_version=1,
                        spec_hash="a" * 64, fixture_hash="b" * 64, coverage_kind="task",
                        required_rules=rule_ids or ["business.zero_committed_dml"])


def manifest(p: PlannedTrial) -> RunManifest:
    return RunManifest(run_id=p.run_id, plan_kind="diagnostic", suite_id="harness-p0-v1", suite_hash="c" * 64,
                       config_hash="d" * 64, evaluator_hash="e" * 64,
                       variants={"candidate": {}}, environment={}, planned_trials=[p])


def test_static_contracts_and_registry() -> None:
    suite, specs = load_suite(ROOT / "evals/suites/harness-p0-v1.json", ROOT / "evals")
    assert len(suite.execution_items) == len(specs) == 3
    broken = suite.model_dump()
    broken["execution_items"].append(broken["execution_items"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        type(suite).model_validate(broken)
    broken = suite.model_dump()
    broken["execution_items"][0]["repeats"] = {"real": 1}
    with pytest.raises(ValidationError, match="deterministic"):
        type(suite).model_validate(broken)


@pytest.mark.parametrize("path", ["../secret", "/tmp/a", "D:/foo", "cases/../a", "cases\\a"])
def test_reject_unsafe_spec_path(path: str) -> None:
    suite, _ = load_suite(ROOT / "evals/suites/harness-p0-v1.json", ROOT / "evals")
    with pytest.raises(ValidationError):
        ExecutionItem.model_validate({**suite.execution_items[0].model_dump(), "spec_path": path})


def test_duplicate_trial_and_unknown_field() -> None:
    p = plan()
    m = manifest(p)
    with pytest.raises(ValidationError, match="duplicate"):
        RunManifest.model_validate({**m.model_dump(), "planned_trials": [p, p]})
    with pytest.raises(ValidationError, match="Extra"):
        PlannedTrial.model_validate({**p.model_dump(), "surprise": 1})
    with pytest.raises(ValidationError):
        PlannedTrial.model_validate({**p.model_dump(), "repeat_index": True})
    with pytest.raises(ValidationError, match="suite x variants"):
        RunManifest.model_validate({**m.model_dump(), "plan_kind": "p0"})
