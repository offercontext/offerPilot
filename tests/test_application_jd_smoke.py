from offerpilot.smoke import run_application_jd_smoke


def test_application_jd_smoke_isolated_contract(tmp_path) -> None:
    report = run_application_jd_smoke(tmp_path)

    assert report.ok is True
    assert [step.name for step in report.steps] == [
        "application_jd_v1",
        "application_jd_v2",
        "application_jd_history",
        "application_jd_cleanup",
    ]
