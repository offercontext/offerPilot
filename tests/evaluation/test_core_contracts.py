import pytest
from pydantic import ValidationError

from offerpilot.evaluation.core_contracts import CaseSpecV2, ProductBudgetSpecV2, SuiteV2
from offerpilot.evaluation.core_registry import ITEMS, case_definition


def test_core_registered_counts_and_contracts():
    assert len(ITEMS) == 19 and sum(i["real"] for i in ITEMS.values()) == 13
    for item in ITEMS:
        CaseSpecV2.model_validate(case_definition(item))
    assert 19 * 2 * 3 == 114 and 13 * 2 * 3 == 78


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_id", "os.system"),
        ("required_rules", []),
        ("case_version", 99),
        ("coverage_profile", "any-table"),
    ],
)
def test_core_json_cannot_change_registered_behavior(field, value):
    data = case_definition("E01.runtime_read")
    data[field] = value
    with pytest.raises(ValidationError):
        CaseSpecV2.model_validate(data)


def test_core_suite_does_not_accept_partial_denominator():
    with pytest.raises(ValidationError):
        SuiteV2.model_validate(dict(suite_id="harness-core-v1", execution_items=[]))
    with pytest.raises(ValidationError):
        ProductBudgetSpecV2(policy="configure", max_model_calls=2)
    with pytest.raises(ValidationError):
        ProductBudgetSpecV2(max_model_calls=2)
