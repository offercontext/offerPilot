import pytest

from offerpilot.application_status import (
    APPLICATION_STATUS_IDS,
    application_status_options,
    normalize_application_status,
)


def test_application_status_contract_exposes_canonical_lifecycle():
    assert APPLICATION_STATUS_IDS == (
        "pending",
        "applied",
        "written_test",
        "interview",
        "offer",
        "closed",
    )
    assert [item["value"] for item in application_status_options()] == list(APPLICATION_STATUS_IDS)
    assert application_status_options()[0]["label"] == "待投递"
    assert application_status_options()[-1]["label"] == "结束"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pending", "pending"),
        ("applied", "applied"),
        ("assessment", "written_test"),
        ("eliminated", "closed"),
        ("rejected", "closed"),
        ("  INTERVIEW  ", "interview"),
    ],
)
def test_normalize_application_status_accepts_canonical_and_legacy_values(raw, expected):
    assert normalize_application_status(raw) == expected


def test_normalize_application_status_rejects_unknown_values():
    with pytest.raises(ValueError, match="invalid application status"):
        normalize_application_status("onsite")
