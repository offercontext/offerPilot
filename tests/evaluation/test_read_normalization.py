from pathlib import Path
from datetime import datetime, timezone

from offerpilot.db import init_database
from offerpilot.evaluation.evidence.observer import install, read
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


def test_application_read_normalization_does_not_commit_business_changes(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    sessions = init_database(db)
    repository = ApplicationsRepository(sessions)
    created = repository.create(ApplicationCreate(company_name="synthetic", position_name="QA"))
    install(db)
    with sessions() as session:
        application = repository.bind(session).get(created.id)
        assert application is not None
        assert application.first_applied_at.tzinfo is not None
        session.commit()
    assert read(db)["deltas"] == []


def test_normalization_preserves_actual_pending_timestamp_change(tmp_path: Path) -> None:
    sessions = init_database(tmp_path / "data.db")
    repository = ApplicationsRepository(sessions)
    created = repository.create(ApplicationCreate(company_name="synthetic", position_name="QA"))
    with sessions() as session:
        application = repository.bind(session).get(created.id)
        application.first_applied_at = datetime(2026, 10, 1)
        repository.bind(session).get(created.id)
        session.commit()
    assert repository.get(created.id).first_applied_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
