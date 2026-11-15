"""The JSON corpus is also consumed by the TypeScript presentation tests."""
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import Application, ApplicationEvent, InterviewNote, InterviewPreparationProposal, Offer
from offerpilot.proactive.api import register_proactive_routes
from offerpilot.proactive.candidates import DAY, reminder_source, source_candidate
from offerpilot.proactive.contracts import ProactivePolicy, ProactivePolicyUpdate
from offerpilot.proactive.models import ProactiveJob
from offerpilot.proactive.repository import ProactiveRepository

CORPUS = json.loads((Path(__file__).parents[1] / "contracts/reminder-candidates.v1.json").read_text())


def date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def populate(sessions, case):
    with sessions() as session:
        for values in case.get("apps", []):
            fields = {**values}
            for key in ("updated_at", "closed_at", "deleted_at"):
                if fields.get(key):
                    fields[key] = date(fields[key])
            session.add(Application(company_name="Acme", position_name="Engineer", **fields))
        session.flush()
        for values in case.get("events", []):
            fields = {**values}
            for key in ("scheduled_at", "remind_at"):
                if fields.get(key):
                    fields[key] = date(fields[key])
            session.add(ApplicationEvent(**fields))
        for values in case.get("offers", []):
            session.add(Offer(company_name="Acme", position_name="Engineer",
                updated_at=date("2026-09-01T00:00:00Z"), **values))
        session.flush()
        if case.get("ready"):
            session.add(InterviewPreparationProposal(application_id=1, application_event_id=1, resume_id=1,
                idempotency_key="ready", attempt_status="ready", proposal_status="normal"))
        if case.get("note"):
            session.add(InterviewNote(application_id=1, application_event_id=1, company="Acme", position="Engineer"))
        session.commit()


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda item: item["name"])
def test_shared_candidate_contract_and_queue_delivery(tmp_path, case):
    sessions = init_database(tmp_path / "candidate.db")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    result = repository.reminder_candidates(now, case.get("timezone", "UTC"))
    assert result == case["expected"]
    # Reading facts neither grants permission nor creates a job.
    assert repository.discover(now) == 0
    assert repository.list_jobs() == []
    if case.get("apps") and not case["apps"][0].get("deleted_at"):
        repository.update_settings(ProactivePolicyUpdate(expected_revision=0, confirmed=True,
            settings=ProactivePolicy(enabled=True, reminders_enabled=True, application_ids=[1],
                quiet_start_hour=0, quiet_end_hour=0)))
        assert repository.discover(now) == case["delivery_count"]
        for job in repository.list_jobs():
            fact = next((item for item in result if item["id"] == job["candidate_id"]), None)
            if fact is not None:
                assert job["source_revision"] == fact["source_revision"]
                assert job["reason_code"] == fact["reason_code"]
                assert job["due_at"] == fact["delivery_due_at"]


def test_source_changes_retract_cards_and_fence_running_job(tmp_path):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "interview_24h")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    first = repository.reminder_candidates(now)[0]
    repository.update_settings(ProactivePolicyUpdate(expected_revision=0, confirmed=True,
        settings=ProactivePolicy(enabled=True, reminders_enabled=True, application_ids=[1],
            quiet_start_hour=0, quiet_end_hour=0)))
    assert repository.discover(now) == 1
    job_id = repository.claim("owner", now)
    job = repository.begin(job_id, "owner", now)
    with sessions() as session:
        event = session.get(ApplicationEvent, 1)
        event.round = 2
        session.commit()
    changed = repository.reminder_candidates(now)[0]
    assert changed["id"] == first["id"]
    assert changed["source_revision"] != first["source_revision"]
    assert not repository.publish(job_id, "owner", job["turn_id"], 1, "stale", now + 1)
    assert repository.list_jobs()[0]["result_text"] == ""
    with sessions() as session:
        session.add(InterviewPreparationProposal(application_id=1, application_event_id=1, resume_id=1,
            idempotency_key="ready", attempt_status="ready", proposal_status="normal"))
        session.commit()
    assert repository.reminder_candidates(now) == []
    assert repository.discover(now + 2) == 0


def test_stale_status_change_withdraws_existing_job(tmp_path):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "stale_7_days")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    repository.update_settings(ProactivePolicyUpdate(expected_revision=0, confirmed=True,
        settings=ProactivePolicy(enabled=True, reminders_enabled=True, application_ids=[1],
            quiet_start_hour=0, quiet_end_hour=0)))
    assert repository.discover(now) == 1
    with sessions() as session:
        session.get(Application, 1).status = "interview"
        session.commit()
    assert repository.reminder_candidates(now) == []
    assert repository.reconcile_source_changes() == 1
    assert repository.list_jobs()[0]["state"] == "cancelled"


def test_read_only_endpoint_is_independent_of_opt_in_and_validates_timezone(tmp_path, monkeypatch):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "interview_48h")
    populate(sessions, case)
    monkeypatch.setattr("offerpilot.proactive.api.time.time", lambda: date(case["now"]).timestamp())
    app = FastAPI()
    register_proactive_routes(app, sessions, Mock(), Mock())
    with TestClient(app) as client:
        response = client.get("/api/reminders/candidates?timezone=UTC")
        assert response.status_code == 200
        assert response.json()["items"] == case["expected"]
        assert client.get("/api/reminders/candidates?timezone=Invalid/Zone").status_code == 422
    with sessions() as session:
        assert session.scalar(select(ProactiveJob.id)) is None


def test_stale_delivery_buckets_do_not_change_source_revision(tmp_path):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "stale_7_days")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    first, second = repository.reminder_candidates(now)[0], repository.reminder_candidates(now + 7 * DAY)[0]
    assert first["id"] == second["id"]
    assert first["source_revision"] == second["source_revision"]
    assert first["due_at"] == second["due_at"]
    assert second["delivery_due_at"] - first["delivery_due_at"] == 7 * DAY


@pytest.mark.parametrize("change", ["ready", "done", "cancelled", "closed", "deleted"])
def test_eligibility_change_removes_fact_and_fences_publication(tmp_path, change):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "interview_24h")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    repository.update_settings(ProactivePolicyUpdate(expected_revision=0, confirmed=True,
        settings=ProactivePolicy(enabled=True, reminders_enabled=True, application_ids=[1],
            quiet_start_hour=0, quiet_end_hour=0)))
    repository.discover(now)
    job_id = repository.claim("owner", now)
    job = repository.begin(job_id, "owner", now)
    with sessions() as session:
        if change == "ready":
            session.add(InterviewPreparationProposal(application_id=1, application_event_id=1, resume_id=1,
                idempotency_key="ready", attempt_status="ready", proposal_status="normal"))
        elif change in {"done", "cancelled"}:
            session.get(ApplicationEvent, 1).status = change
        elif change == "closed":
            session.get(Application, 1).closed_at = date(case["now"])
        else:
            session.get(Application, 1).deleted_at = date(case["now"])
        session.commit()
    assert repository.reminder_candidates(now) == []
    assert not repository.publish(job_id, "owner", job["turn_id"], 1, "stale", now + 1)
    assert repository.list_jobs()[0]["result_text"] == ""


@pytest.mark.parametrize("event_id", [1, 2, 3])
def test_display_only_events_never_become_queue_sources_after_revision_changes(tmp_path, event_id):
    sessions = init_database(tmp_path / "candidate.db")
    case = next(item for item in CORPUS["cases"] if item["name"] == "display_events_24h")
    populate(sessions, case)
    repository = ProactiveRepository(sessions)
    now = date(case["now"]).timestamp()
    first = next(item for item in repository.reminder_candidates(now) if item["event_id"] == event_id)
    repository.update_settings(ProactivePolicyUpdate(expected_revision=0, confirmed=True,
        settings=ProactivePolicy(enabled=True, reminders_enabled=True, drafts_enabled=True,
            application_ids=[1], quiet_start_hour=0, quiet_end_hour=0)))
    with sessions() as session:
        event = session.get(ApplicationEvent, event_id)
        event.scheduled_at = datetime.fromtimestamp(now + 3600, timezone.utc)
        event.remind_at = datetime.fromtimestamp(now, timezone.utc)
        session.commit()
        # Even an explicit remind_at and all opt-ins cannot reinterpret display
        # facts as any supported delivery kind or give event_upcoming permission.
        for kind in ("event_upcoming", "interview_preparation", "interview_draft", "review", "deadline", "stale_application"):
            assert reminder_source(session, 1, event_id, kind) is None
            assert source_candidate(session, 1, event_id, kind, now) is None
    changed = next(item for item in repository.reminder_candidates(now) if item["event_id"] == event_id)
    assert changed["id"] == first["id"]
    assert changed["source_revision"] != first["source_revision"]
    assert changed["due_at"] == now + 3600
    assert changed["delivery_due_at"] is None
    assert changed["delivery_expires_at"] is None
    assert repository.discover(now) == 0
    assert repository.list_jobs() == []
    with sessions() as session:
        session.get(Application, 1).status = "closed"
        session.commit()
    assert repository.reminder_candidates(now) == []
