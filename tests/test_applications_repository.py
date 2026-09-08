from datetime import datetime, timezone

from offerpilot.db import init_database
from offerpilot.models import ApplicationJDVersion
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


def test_create_and_list_applications_ordered_by_applied_at(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)

    older = repo.create(
        ApplicationCreate(
            company_name="A",
            position_name="Backend",
            applied_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    newer = repo.create(
        ApplicationCreate(
            company_name="B",
            position_name="Frontend",
            applied_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    )

    apps = repo.list()

    assert [app.id for app in apps] == [newer.id, older.id]
    assert apps[0].status == "applied"
    assert apps[0].source == "cli"


def test_filter_applications_by_status(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    repo.create(ApplicationCreate(company_name="A", position_name="Backend", status="interview"))
    repo.create(ApplicationCreate(company_name="B", position_name="Frontend", status="offer"))

    apps = repo.list(status="interview")

    assert len(apps) == 1
    assert apps[0].company_name == "A"


def test_delete_soft_deletes_and_default_reads_hide_deleted(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    kept = repo.create(ApplicationCreate(company_name="A", position_name="Backend"))
    deleted = repo.create(ApplicationCreate(company_name="B", position_name="Frontend"))

    repo.delete(deleted.id)

    assert [app.id for app in repo.list()] == [kept.id]
    assert repo.get(deleted.id) is None
    assert repo.dashboard()["total"] == 1


def test_update_full_replaces_application_fields(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    app = repo.create(ApplicationCreate(company_name="A", position_name="Backend", notes="first"))

    updated = repo.update_full(
        app.id,
        ApplicationCreate(
            company_name="B",
            position_name="Frontend",
            job_url="https://example.test",
            status="offer",
            notes="second",
            source="web",
        ),
    )

    assert updated.company_name == "B"
    assert updated.position_name == "Frontend"
    assert updated.job_url == "https://example.test"
    assert updated.status == "offer"
    assert updated.notes == "second"
    assert updated.source == "web"


def test_application_jd_version_model_is_registered(tmp_path):
    init_database(tmp_path / "data.db")

    assert ApplicationJDVersion.__tablename__ == "application_jd_versions"


def test_update_full_records_first_status_timestamp_only_once(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    app = repo.create(
        ApplicationCreate(company_name="A", position_name="Backend", status="applied")
    )

    first = repo.update_full(
        app.id,
        ApplicationCreate(company_name="A", position_name="Backend", status="interview"),
    )
    repo.update_full(
        app.id,
        ApplicationCreate(company_name="A", position_name="Backend", status="written_test"),
    )
    second = repo.update_full(
        app.id,
        ApplicationCreate(company_name="A", position_name="Backend", status="interview"),
    )

    assert app.first_applied_at is not None
    assert first is not None
    assert first.first_interview_at is not None
    assert second is not None
    assert second.first_interview_at == first.first_interview_at


def test_update_full_requires_closed_reason_when_entering_closed(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    app = repo.create(
        ApplicationCreate(company_name="A", position_name="Backend", status="interview")
    )

    try:
        repo.update_full(
            app.id, ApplicationCreate(company_name="A", position_name="Backend", status="closed")
        )
    except ValueError as exc:
        assert str(exc) == "closed_reason is required when closing an application"
    else:
        raise AssertionError("closing without closed_reason should fail")

    closed = repo.update_full(
        app.id,
        ApplicationCreate(
            company_name="A",
            position_name="Backend",
            status="closed",
            closed_reason="主动放弃",
        ),
    )

    assert closed is not None
    assert closed.status == "closed"
    assert closed.closed_reason == "主动放弃"
    assert closed.closed_at is not None


def test_update_full_requires_fresh_closed_reason_and_rejects_reopen(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    app = repo.create(
        ApplicationCreate(
            company_name="A",
            position_name="Backend",
            status="applied",
            closed_reason="stale",
        )
    )

    assert app.closed_reason == ""

    try:
        repo.update_full(
            app.id, ApplicationCreate(company_name="A", position_name="Backend", status="closed")
        )
    except ValueError as exc:
        assert str(exc) == "closed_reason is required when closing an application"
    else:
        raise AssertionError("closing without a fresh closed_reason should fail")

    closed = repo.update_full(
        app.id,
        ApplicationCreate(
            company_name="A",
            position_name="Backend",
            status="closed",
            closed_reason="岗位关闭",
        ),
    )
    assert closed is not None

    try:
        repo.update_full(
            app.id,
            ApplicationCreate(company_name="A", position_name="Backend", status="interview"),
        )
    except ValueError as exc:
        assert str(exc) == "closed application cannot be reopened"
    else:
        raise AssertionError("closed application should not reopen")


def test_restore_status_if_matches_changes_only_status_fields(tmp_path):
    repo = ApplicationsRepository(init_database(tmp_path / "data.db"))
    app = repo.create(
        ApplicationCreate(company_name="A", position_name="Backend", status="interview")
    )
    repo.update_full(
        app.id,
        ApplicationCreate(
            company_name="User renamed",
            position_name="Backend",
            status="offer",
            notes="user note",
        ),
    )

    restored = repo.restore_status_if_matches(
        app.id,
        expected_status="offer",
        expected_closed_reason="",
        status="interview",
        closed_reason="",
    )

    assert restored is True
    current = repo.get(app.id)
    assert current is not None
    assert current.status == "interview"
    assert current.company_name == "User renamed"
    assert current.notes == "user note"


def test_restore_status_if_matches_rejects_conflict(tmp_path):
    repo = ApplicationsRepository(init_database(tmp_path / "data.db"))
    app = repo.create(ApplicationCreate(company_name="A", position_name="Backend", status="offer"))

    restored = repo.restore_status_if_matches(
        app.id,
        expected_status="closed",
        expected_closed_reason="user",
        status="interview",
        closed_reason="",
    )

    assert restored is False
    assert repo.get(app.id).status == "offer"


def test_dashboard_groups_by_status(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    repo = ApplicationsRepository(session_factory)
    repo.create(ApplicationCreate(company_name="A", position_name="Backend", status="interview"))
    repo.create(ApplicationCreate(company_name="B", position_name="Frontend", status="offer"))

    dashboard = repo.dashboard()

    assert dashboard["total"] == 2
    assert len(dashboard["board"]["interview"]) == 1
    assert len(dashboard["board"]["offer"]) == 1
