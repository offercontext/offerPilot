from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from offerpilot.db import init_database
from offerpilot.job_mail.models import (
    JobMailConnection, JobMailEvidence, JobMailPreview, JobMailReceipt, JobMailSuggestion,
)
from offerpilot.job_mail.review import (
    JobMailError, JobMailReviewService, ingest_candidate, purge_expired_evidence, utcnow, workspace_id,
)
from offerpilot.job_mail.schemas import ConfirmRequest, ReviewRequest
from offerpilot.models import Application, ApplicationEvent
from offerpilot.repositories.application_events import ApplicationEventCreate, ApplicationEventsRepository


@pytest.fixture
def setup(tmp_path):
    sessions = init_database(tmp_path / "mail.db")
    with sessions() as session:
        application = Application(company_name="Example", position_name="Engineer", status="applied")
        other = Application(company_name="Example", position_name="Designer", status="applied")
        session.add_all([application, other])
        session.commit()
        ids = application.id, other.id
    yield sessions, JobMailReviewService(sessions), ids
    sessions.kw["bind"].dispose()


def _ingest(setup, *, action="create_event", mode="fixed", fields=None, connection_id=None,
            message_id="<one@example.test>", body="interview at 2026-10-20T10:00:00Z, duration 60 minutes"):
    sessions, _service, _ids = setup
    fields = fields if fields is not None else {
        "event_type": "interview", "scheduled_at": "2026-10-20T10:00:00Z", "duration_minutes": 60,
    }
    parsed = {"body_text": body, "received_at": "2026-10-09T10:00:00Z", "message_id": message_id,
              "sender": "hr@example.test", "subject": "interview", "folder": "INBOX", "uid": 1,
              "uidvalidity": "generation-1"}
    candidates = [{"action": action, "time_mode": mode, "proposed_fields": fields,
                   "company": "Example", "evidence": {"notice": body}}]
    with sessions() as session:
        result = ingest_candidate(session, parsed, candidates, connection_id=connection_id)
        session.commit()
        return result["items"][0]


def _request(setup, item, **overrides):
    return ReviewRequest.model_validate({
        "operation_id": str(uuid4()), "suggestion_version": item["version"],
        "application_id": setup[2][0], "edited_fields": {}, **overrides,
    })


def _confirm(request, preview, **overrides):
    return ConfirmRequest.model_validate({
        **request.model_dump(mode="json", exclude_unset=True),
        "preview_token": preview["preview_token"], "explicit_confirmation": True, **overrides,
    })


def _counts(sessions):
    with sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (ApplicationEvent, JobMailReceipt))


def _event(setup, **overrides):
    return ApplicationEventsRepository(setup[0]).create(ApplicationEventCreate(
        application_id=setup[2][0], event_type="interview", scheduled_at=datetime(2026, 10, 20, 10),
        duration_minutes=45, location="User's original location", notes="Private preparation",
        **overrides,
    ))


def test_preview_has_zero_business_writes_and_confirm_is_atomic_and_recoverable(setup):
    sessions, service, ids = setup
    item = _ingest(setup)
    assert len(item["application_candidates"]) == 2  # no company-name auto-binding
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    assert _counts(sessions) == (0, 0)
    result = service.confirm(item["id"], _confirm(request, preview))
    assert _counts(sessions) == (1, 1)
    assert result["after"]["duration_minutes"] == 60
    with sessions() as session:
        assert session.get(Application, ids[0]).status == "applied"
    assert service.confirm(item["id"], _confirm(request, preview))["replayed"] is True
    assert JobMailReviewService(sessions).receipt(request.operation_id) == result
    assert service.get(item["id"])["status"] == "applied"


@pytest.mark.parametrize("changed", [
    {"edited_fields": {"location": "Different location"}},
    {"operation_id": str(uuid4())}, {"suggestion_version": 2},
    {"application_id": 2}, {"target_event_id": 999},
])
def test_preview_binds_every_actual_field_and_target(setup, changed):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    with pytest.raises(JobMailError, match="确认字段"):
        service.confirm(item["id"], _confirm(request, preview, **changed))
    assert _counts(sessions) == (0, 0)
    assert service.get(item["id"])["status"] == "pending"


def test_receipt_key_reuse_with_different_fields_is_rejected(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    service.confirm(item["id"], _confirm(request, preview))
    with pytest.raises(JobMailError) as error:
        service.confirm(item["id"], _confirm(request, preview, edited_fields={"notes": "changed"}))
    assert error.value.code == "operation_conflict"
    assert _counts(sessions) == (1, 1)


@pytest.mark.parametrize("reminder_placeholder", [{}, {"remind_at": None}, {"remind_at": ""}])
def test_update_preserves_omitted_fields_and_same_id_and_discloses_old_reminder(
        setup, reminder_placeholder):
    sessions, service, _ids = setup
    target = _event(setup, remind_at=datetime(2026, 10, 20, 9))
    item = _ingest(setup, action="update_event", fields={
        "scheduled_at": "2026-10-21T10:00:00Z", "location": "", "notes": None,
        **reminder_placeholder,
    })
    request = _request(setup, item, target_event_id=target.id)
    preview = service.preview(item["id"], request)
    assert preview["after"]["notes"] == "Private preparation"
    assert preview["after"]["location"] == "User's original location"
    assert preview["after"]["duration_minutes"] == 45
    assert preview["after"]["remind_at"] is None
    assert any(change["field"] == "remind_at" for change in preview["changes"])
    assert len(preview["warnings"]) == 2
    result = service.confirm(item["id"], _confirm(request, preview))
    assert result["application_event_id"] == target.id
    assert _counts(sessions) == (1, 1)



@pytest.mark.parametrize("reminder_edit", [None, "2026-10-21T09:30:00Z"])
def test_explicit_reminder_edit_wins_over_empty_proposal_on_time_change(setup, reminder_edit):
    _sessions, service, _ids = setup
    target = _event(setup, remind_at=datetime(2026, 10, 20, 9))
    item = _ingest(setup, action="update_event", fields={
        "scheduled_at": "2026-10-21T10:00:00Z", "remind_at": "",
    })
    request = _request(setup, item, target_event_id=target.id,
                       edited_fields={"remind_at": reminder_edit})
    preview = service.preview(item["id"], request)
    assert preview["after"]["remind_at"] == reminder_edit
    assert len(preview["warnings"]) == 1
    result = service.confirm(item["id"], _confirm(request, preview))
    assert result["after"]["remind_at"] == reminder_edit


def test_same_time_with_different_offset_does_not_clear_reminder(setup):
    _sessions, service, _ids = setup
    target = _event(setup, remind_at=datetime(2026, 10, 20, 9))
    item = _ingest(setup, action="update_event", fields={
        "scheduled_at": "2026-10-20T18:00:00+08:00", "location": "new meeting room",
    })
    preview = service.preview(item["id"], _request(setup, item, target_event_id=target.id))
    assert preview["after"]["remind_at"] == "2026-10-20T09:00:00Z"
    assert {change["field"] for change in preview["changes"]} == {"location"}


@pytest.mark.parametrize("mutation", ["notes", "schedule", "delete", "parent", "soft_delete"])
def test_concurrent_manual_edits_or_deletion_reject_without_overwrite(setup, mutation):
    sessions, service, ids = setup
    target = _event(setup)
    item = _ingest(setup, action="update_event", fields={"location": "suggested place"})
    request = _request(setup, item, target_event_id=target.id)
    preview = service.preview(item["id"], request)
    with sessions() as session:
        current = session.get(ApplicationEvent, target.id)
        if mutation == "notes":
            current.notes = "Changed by human"
        elif mutation == "schedule":
            current.scheduled_at = datetime(2026, 10, 25, 10)
        elif mutation == "delete":
            session.delete(current)
        elif mutation == "parent":
            session.get(Application, ids[0]).status = "interview"
        else:
            session.get(Application, ids[0]).deleted_at = utcnow()
        session.commit()
    with pytest.raises(JobMailError):
        service.confirm(item["id"], _confirm(request, preview))
    assert _counts(sessions)[1] == 0
    with sessions() as session:
        if mutation != "delete":
            assert session.get(ApplicationEvent, target.id).location == "User's original location"
        assert session.get(JobMailSuggestion, item["id"]).status == "pending"


def test_cross_application_target_is_rejected(setup):
    _sessions, service, ids = setup
    target = _event(setup)
    item = _ingest(setup, action="update_event", fields={"location": "proposed"})
    with pytest.raises(JobMailError) as error:
        service.preview(item["id"], _request(setup, item, application_id=ids[1], target_event_id=target.id))
    assert error.value.code == "target_scope_mismatch"


@pytest.mark.parametrize("field", ["version", "superseded_by", "status"])
def test_old_suggestion_cannot_be_applied(setup, field):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    with sessions() as session:
        row = session.get(JobMailSuggestion, item["id"])
        setattr(row, field, {"version": 2, "superseded_by": str(uuid4()), "status": "ignored"}[field])
        session.commit()
    with pytest.raises(JobMailError):
        service.confirm(item["id"], _confirm(request, preview))
    assert _counts(sessions) == (0, 0)


@pytest.mark.parametrize("mutation", ["scope_version", "status", "scope_id"])
def test_connection_scope_revocation_blocks_old_preview(setup, mutation):
    sessions, service, _ids = setup
    with sessions() as session:
        connection = JobMailConnection(id=str(uuid4()), scope_id=workspace_id(session), status="connected")
        session.add(connection)
        session.commit()
        connection_id = connection.id
    item = _ingest(setup, connection_id=connection_id)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    with sessions() as session:
        setattr(session.get(JobMailConnection, connection_id), mutation,
                {"scope_version": 2, "status": "disconnected", "scope_id": "other-workspace"}[mutation])
        session.commit()
    with pytest.raises(JobMailError) as error:
        service.confirm(item["id"], _confirm(request, preview))
    assert error.value.code == "mail_scope_changed"
    assert _counts(sessions) == (0, 0)


def test_concurrent_duplicate_confirm_creates_exactly_one_event_and_receipt(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    command = _confirm(request, preview)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.confirm(item["id"], command), range(6)))
    assert len({result["application_event_id"] for result in results}) == 1
    assert sum(not result["replayed"] for result in results) == 1
    assert _counts(sessions) == (1, 1)


def test_concurrent_two_operations_for_same_suggestion_do_not_duplicate(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    requests = [_request(setup, item) for _ in range(2)]
    commands = [_confirm(req, service.preview(item["id"], req)) for req in requests]
    def confirm(command):
        try:
            return service.confirm(item["id"], command)
        except JobMailError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, commands))
    assert sum(result is not None for result in results) == 1
    assert _counts(sessions) == (1, 1)


def test_receipt_failure_rolls_back_event_and_suggestion(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    def fail_receipt(*args):
        raise RuntimeError("simulated receipt write failure")
    event.listen(JobMailReceipt, "before_insert", fail_receipt)
    try:
        with pytest.raises(RuntimeError):
            service.confirm(item["id"], _confirm(request, preview))
    finally:
        event.remove(JobMailReceipt, "before_insert", fail_receipt)
    assert _counts(sessions) == (0, 0)
    assert service.get(item["id"])["status"] == "pending"
    # Retry the same reviewed operation after a *known rolled-back* failure.
    assert service.confirm(item["id"], _confirm(request, preview))["replayed"] is False


@pytest.mark.parametrize("fields", [
    {"event_type": "interview"},
    {"event_type": "interview", "scheduled_at": "2026-10-20T10:00:00Z"},
    {"event_type": "interview", "duration_minutes": 60},
    {"event_type": "interview", "scheduled_at": "2026-10-20", "duration_minutes": 60},
    {"event_type": "interview", "scheduled_at": "2026-10-20T10:00:00", "duration_minutes": 60},
    {"event_type": "interview", "scheduled_at": "2026-10-20T10:00:00Z", "duration_minutes": 0},
    {"event_type": "interview", "scheduled_at": "2026-10-20T10:00:00Z", "duration_minutes": True},
])
def test_missing_or_ambiguous_time_never_fabricates_business_record(setup, fields):
    sessions, service, _ids = setup
    item = _ingest(setup, fields=fields)
    with pytest.raises(JobMailError):
        service.preview(item["id"], _request(setup, item))
    assert _counts(sessions) == (0, 0)


@pytest.mark.parametrize("mode,action", [("deadline", "create_event"), ("window", "create_event"),
                                          ("unknown", "create_event"), ("fixed", "manual_only"),
                                          ("fixed", "cancel_event"), ("fixed", "create_offer")])
def test_unsupported_suggestions_cannot_be_promoted_by_user_edits(setup, mode, action):
    sessions, service, _ids = setup
    item = _ingest(setup, mode=mode, action=action)
    assert item["status"] == "manual_required"
    with pytest.raises(JobMailError):
        service.preview(item["id"], _request(setup, item, edited_fields={
            "scheduled_at": "2026-10-20T10:00:00Z", "duration_minutes": 60,
        }))
    assert _counts(sessions) == (0, 0)


def test_ignore_is_durable_and_duplicate_ingest_does_not_resurrect(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    service.ignore(item["id"], item["version"])
    duplicate = _ingest(setup)
    assert duplicate["id"] == item["id"]
    assert duplicate["status"] == "ignored"
    distinct = _ingest(setup, message_id="<another@example.test>")
    assert distinct["id"] != item["id"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobMailEvidence)) == 2


def test_expired_preview_and_cleared_evidence_cannot_confirm(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    with sessions() as session:
        session.get(JobMailPreview, preview["preview_token"]).expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    with pytest.raises(JobMailError) as error:
        service.confirm(item["id"], _confirm(request, preview))
    assert error.value.code == "preview_expired"
    fresh = service.preview(item["id"], request)
    with sessions() as session:
        session.get(JobMailEvidence, item["evidence"]["id"]).created_at = utcnow() - timedelta(days=91)
        session.flush()
        assert purge_expired_evidence(session) == 1
        session.commit()
    with pytest.raises(JobMailError) as error:
        service.confirm(item["id"], _confirm(request, fresh))
    assert error.value.code == "mail_evidence_unavailable"
    detail = service.get(item["id"])
    assert detail["evidence"]["snippet"] is None
    assert detail["field_evidence"] == {}
    assert _counts(sessions) == (0, 0)


def test_invalid_evidence_and_injected_actions_are_rejected_without_partial_storage(setup):
    sessions, _service, _ids = setup
    for candidate in [
        {"tool_calls": [{"name": "send_email"}]},
        {"proposed_fields": {"status": "offer"}},
        {"evidence": {"scheduled_at": "hallucinated time"}},
        {"evidence": {"notice": {"quote": "interview", "start": 1, "end": 10}}},
    ]:
        with sessions() as session:
            with pytest.raises(JobMailError):
                ingest_candidate(session, {"body_text": "interview", "received_at": "2026-10-09T10:00:00Z"}, [candidate])
            session.rollback()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobMailEvidence)) == 0


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_confirmation_must_be_explicit_strict_boolean(value):
    with pytest.raises(ValidationError):
        ConfirmRequest.model_validate({"operation_id": str(uuid4()), "suggestion_version": 1,
            "application_id": 1, "preview_token": str(uuid4()), "explicit_confirmation": value})


def test_receipt_and_business_rows_survive_database_restart(setup):
    sessions, service, _ids = setup
    item = _ingest(setup)
    request = _request(setup, item)
    result = service.confirm(item["id"], _confirm(request, service.preview(item["id"], request)))
    database_path = sessions.kw["bind"].url.database
    from pathlib import Path
    restarted = init_database(Path(database_path))
    try:
        assert JobMailReviewService(restarted).receipt(request.operation_id) == result
        assert _counts(restarted) == (1, 1)
    finally:
        restarted.kw["bind"].dispose()


def test_reauthorization_requires_actual_reread_and_never_resurrects_processed(setup):
    sessions, service, _ids = setup
    with sessions() as session:
        connection = JobMailConnection(id=str(uuid4()), scope_id=workspace_id(session), status="connected")
        session.add(connection)
        session.commit()
        connection_id = connection.id
    item = _ingest(setup, connection_id=connection_id)
    ignored = _ingest(setup, connection_id=connection_id, message_id="<ignored@example.test>")
    service.ignore(ignored["id"], ignored["version"])
    request = _request(setup, item)
    preview = service.preview(item["id"], request)
    with sessions() as session:
        session.get(JobMailConnection, connection_id).scope_version = 2
        session.commit()
    with pytest.raises(JobMailError) as error:
        service.preview(item["id"], request)
    assert error.value.code == "mail_scope_changed"
    # A current-scope read of this exact evidence restores only unprocessed work.
    with sessions() as session:
        for message_id in ("<one@example.test>", "<ignored@example.test>"):
            ingest_candidate(session, {
                "body_text": "interview at 2026-10-20T10:00:00Z, duration 60 minutes",
                "received_at": "2026-10-09T10:00:00Z", "message_id": message_id,
                "sender": "hr@example.test", "subject": "interview", "folder": "INBOX", "uid": 2,
            }, [], connection_id=connection_id, scope_version=2)
        session.commit()
    refreshed = service.get(item["id"])
    assert refreshed["version"] == 2
    assert refreshed["scope_version"] == 2
    assert service.get(ignored["id"])["status"] == "ignored"
    assert service.get(ignored["id"])["scope_version"] == 1
    with pytest.raises(JobMailError):
        service.confirm(item["id"], _confirm(request, preview))
    next_request = _request(setup, refreshed)
    receipt = service.confirm(item["id"], _confirm(next_request, service.preview(item["id"], next_request)))
    assert receipt["suggestion_version"] == 2


@pytest.mark.parametrize("value", ["1792483200", "1792483200000", 1792483200, "2026-10-20", "tomorrow"])
def test_epoch_values_and_relative_dates_cannot_be_used_as_complete_times(value):
    from offerpilot.job_mail.schemas import EventEdits, ImportRequest
    for field in ("scheduled_at", "remind_at"):
        with pytest.raises(ValidationError):
            EventEdits.model_validate({field: value})
    with pytest.raises(ValidationError):
        ImportRequest.model_validate({"body_text": "interview", "received_at": value})


def test_settings_scope_preserves_retained_sources_but_requires_fresh_preview(setup):
    from offerpilot.job_mail.sync import JobMailSyncService
    from offerpilot.job_mail.transport import FakeIMAPTransport
    sessions, review, _ids = setup
    transport = FakeIMAPTransport()
    transport.generations["Recruiting"] = "1"
    sync = JobMailSyncService(sessions, transport)
    try:
        connection_id = sync.connect({"provider": "synthetic", "folders": ["INBOX"]})["connection"]["id"]
        item = _ingest(setup, connection_id=connection_id)
        ignored = _ingest(setup, connection_id=connection_id, message_id="<ignored@example.test>")
        review.ignore(ignored["id"], ignored["version"])
        request = _request(setup, item)
        preview = review.preview(item["id"], request)
        status = sync.update_settings({"folders": ["INBOX", "Recruiting"]})
        assert status["connection"]["scope_version"] == 2
        updated = review.get(item["id"])
        assert updated["scope_version"] == 2
        assert updated["version"] == 2
        assert review.get(ignored["id"])["status"] == "ignored"
        assert review.get(ignored["id"])["scope_version"] == 1
        assert transport.read_calls == []
        with pytest.raises(JobMailError):
            review.confirm(item["id"], _confirm(request, preview))
        review.preview(item["id"], _request(setup, updated))
        # Removing a source excludes it. Re-adding the directory cannot revive
        # the now-stale source solely on its historical location claim.
        sync.update_settings({"folders": ["Recruiting"]})
        sync.update_settings({"folders": ["INBOX", "Recruiting"]})
        with pytest.raises(JobMailError) as error:
            review.preview(item["id"], _request(setup, updated))
        assert error.value.code == "mail_scope_changed"
        assert _counts(sessions) == (0, 0)
    finally:
        sync.shutdown()
