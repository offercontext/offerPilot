import json

import pytest
from pydantic import ValidationError

from offerpilot.evaluation.core_contracts import CaseSpecV2, SuiteV2
from offerpilot.evaluation.core_registry import ITEMS, EXTENSION_ITEMS, case_definition
from offerpilot.evaluation.core_graders import grade_rule
from scripts.evaluation_subject.core_fixtures import prepare
from scripts.evaluation_subject import core_observer as observer


def test_extension_has_separate_denominator_and_two_actual_eligible_memories(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert len(ITEMS) == 19 and len(EXTENSION_ITEMS) == 1
    CaseSpecV2.model_validate(case_definition("E11.memory_withdraw_scope"))
    SuiteV2.model_validate_json((root / "evals/suites/harness-extension-v1.json").read_bytes())
    with pytest.raises(ValidationError):
        SuiteV2.model_validate(
            dict(
                suite_id="harness-core-v1",
                execution_items=[
                    dict(
                        execution_item_id="E11.memory_withdraw_scope",
                        spec_path="cases/E11.memory_withdraw_scope.v1.json",
                    )
                ],
            )
        )
    aliases = prepare(tmp_path, root, "E09.memory_withdraw", "E11.memory_withdraw_scope")
    observer.install(tmp_path / "data.db", "context_sources.v1")
    rows = observer.read(tmp_path / "data.db")["snapshot"]["confirmed_memories"]
    assert {r["id"] for r in rows} == {aliases["memory_id"], aliases["untouched_memory_id"]}
    assert all(r["state"] == "active" for r in rows)


@pytest.mark.parametrize(
    "mutation", ["none", "stale", "omitted_unrelated", "modified_unrelated", "missing"]
)
def test_extension_unit_identity_and_unrelated_preservation_are_independent(mutation):
    retained = dict(id="keep", version=1, preference="海燕-29")
    units = [] if mutation == "omitted_unrelated" else [retained]
    if mutation == "stale":
        units.append(dict(id="withdraw", version=1, preference="stale"))
    before = dict(id="keep", current_version=1, state="active")
    e = dict(
        facts=dict(
            aliases=dict(memory_id="withdraw", untouched_memory_id="keep"), withdraw_control_seq=3
        ),
        initial=dict(confirmed_memories=[before]),
        observed=dict(
            snapshot=dict(
                confirmed_memories=[before | dict(current_version=2)]
                if mutation == "modified_unrelated"
                else [before]
            )
        ),
        input_observations=[]
        if mutation == "missing"
        else [
            dict(
                seq=4,
                category="sources",
                sources=[dict(kind="confirmed_memory", content=json.dumps(units))],
            ),
            dict(
                seq=5,
                category="final_input",
                observations=[
                    dict(kind="confirmed_memory", ranges=[dict(unit_identity=u) for u in units])
                ],
            ),
        ],
    )
    withdrawn, _ = grade_rule("source.withdrawn_unit", "E11.memory_withdraw_scope", e)
    untouched, _ = grade_rule("source.unrelated_preserved", "E11.memory_withdraw_scope", e)
    assert withdrawn is (None if mutation == "missing" else mutation != "stale")
    assert untouched is (
        None
        if mutation == "missing"
        else mutation not in {"omitted_unrelated", "modified_unrelated"}
    )
