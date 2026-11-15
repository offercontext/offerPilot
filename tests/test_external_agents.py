from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from offerpilot.db import init_database
from offerpilot.external_agents.service import (
    ExternalAgentError,
    ExternalAgentService,
    create_connection,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.application_events import (
    ApplicationEventsRepository,
    ApplicationEventCreate,
)


@pytest.fixture
def env(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    applications = ApplicationsRepository(sessions)
    first = applications.create(ApplicationCreate(company_name="云岚", position_name="开发"))
    second = applications.create(ApplicationCreate(company_name="北辰", position_name="测试"))
    connection = create_connection(sessions, "DSH", [first.id], allow_scheduling=True)
    service = ExternalAgentService(sessions, connection["connection_id"])
    return sessions, service, first, second


def proposal(application_id, **overrides):
    return {
        "idempotency_key": str(uuid4()),
        "application_id": application_id,
        "scheduled_at": "2026-11-01T15:00:00+08:00",
        "duration_minutes": 60,
        "location": "线上",
        "round": 1,
        "notes": "技术面",
        **overrides,
    }


def test_proposal_never_writes_before_separate_user_decision_and_replays(env):
    sessions, service, application, _ = env
    payload = proposal(application.id)
    pending = service.call("propose_interview", payload)
    assert pending["status"] == "pending"
    assert ApplicationEventsRepository(sessions).list() == []
    assert service.call("propose_interview", payload) == pending
    reviewed = service.review(pending["request_id"])
    result = service.decide(reviewed["request_id"], reviewed["proposal_digest"], "approve")
    assert result["status"] == "committed"
    assert service.call("get_request", {"request_id": pending["request_id"]}) == result
    assert service.decide(reviewed["request_id"], reviewed["proposal_digest"], "approve") == result
    assert len(ApplicationEventsRepository(sessions).list()) == 1
    event = service.call("get_event", {"event_id": result["event"]["id"]})
    assert event["scheduled_at"] == "2026-11-01T07:00:00Z"


def test_scope_is_enforced_for_reads_proposals_and_request_ownership(env):
    sessions, service, first, second = env
    assert [x["id"] for x in service.call("list_applications", {})["items"]] == [first.id]
    for tool, args in [
        ("get_application", {"application_id": second.id}),
        ("list_events", {"application_id": second.id}),
        ("propose_interview", proposal(second.id)),
    ]:
        with pytest.raises(ExternalAgentError, match="scope_denied"):
            service.call(tool, args)
    pending = service.call("propose_interview", proposal(first.id))
    other = create_connection(sessions, "Other", [first.id], allow_scheduling=True)
    other_service = ExternalAgentService(sessions, other["connection_id"])
    with pytest.raises(ExternalAgentError, match="request_not_found"):
        other_service.call("cancel_request", {"request_id": pending["request_id"]})


def test_read_only_grant_and_model_confirmation_are_rejected(env):
    sessions, service, application, _ = env
    readonly = create_connection(sessions, "Readonly", [application.id])
    with pytest.raises(ExternalAgentError, match="scheduling_not_granted"):
        ExternalAgentService(sessions, readonly["connection_id"]).call(
            "propose_interview", proposal(application.id)
        )
    with pytest.raises(ExternalAgentError, match="invalid_input"):
        service.call("propose_interview", proposal(application.id, confirmed=True))
    with pytest.raises(ExternalAgentError, match="unknown_tool"):
        service.call("confirm_request", {"confirmed": True})


def test_same_idempotency_key_with_different_payload_is_conflict(env):
    _, service, application, _ = env
    payload = proposal(application.id)
    service.call("propose_interview", payload)
    with pytest.raises(ExternalAgentError, match="idempotency_conflict"):
        service.call("propose_interview", {**payload, "location": "新地址"})


@pytest.mark.parametrize("terminal", ["reject", "cancel", "expire"])
def test_non_approval_terminal_states_never_write(env, terminal):
    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    reviewed = service.review(pending["request_id"])
    if terminal == "reject":
        result = service.decide(reviewed["request_id"], reviewed["proposal_digest"], "reject")
    elif terminal == "cancel":
        result = service.call("cancel_request", {"request_id": pending["request_id"]})
    else:
        service.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
        result = service.call("get_request", {"request_id": pending["request_id"]})
    assert (
        result["status"]
        == {"reject": "rejected", "cancel": "cancelled", "expire": "expired"}[terminal]
    )
    assert service.decide(reviewed["request_id"], reviewed["proposal_digest"], "approve") == result
    assert ApplicationEventsRepository(sessions).list() == []


def test_reschedule_requires_current_version_and_checks_again_at_approval(env):
    sessions, service, application, _ = env
    events = ApplicationEventsRepository(sessions)
    data = ApplicationEventCreate(application.id, "interview", datetime(2026, 11, 1), 60)
    event = events.create(data)
    snapshot = service.call("get_event", {"event_id": event.id})
    payload = {
        "idempotency_key": str(uuid4()),
        "application_id": application.id,
        "event_id": event.id,
        "expected_revision": snapshot["revision"],
        "scheduled_at": "2026-11-02T15:00:00+08:00",
        "duration_minutes": 30,
        "location": "新地点",
    }
    pending = service.call("propose_reschedule", payload)
    data.location = "用户已修改"
    events.update(event.id, data)
    reviewed = service.review(pending["request_id"])
    result = service.decide(reviewed["request_id"], reviewed["proposal_digest"], "approve")
    assert result["status"] == "conflict"
    assert events.get(event.id).location == "用户已修改"


def test_concurrent_approvals_create_one_business_event(env):
    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    reviewed = service.review(pending["request_id"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: service.decide(
                    reviewed["request_id"], reviewed["proposal_digest"], "approve"
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert results[0]["status"] == "committed"
    assert len(ApplicationEventsRepository(sessions).list()) == 1


def test_revocation_fences_live_calls_and_previously_reviewed_requests(env):
    from offerpilot.external_agents.models import ExternalAgentRequest

    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    view = service.review(pending["request_id"])
    service.revoke()
    service.revoke()
    with sessions() as session:
        assert session.get(ExternalAgentRequest, pending["request_id"]).status == "cancelled"
    for operation in [
        lambda: service.call("list_applications", {}),
        lambda: service.call("propose_interview", proposal(application.id)),
        lambda: service.decide(view["request_id"], view["proposal_digest"], "approve"),
    ]:
        with pytest.raises(ExternalAgentError, match="connection_unavailable"):
            operation()
    assert ApplicationEventsRepository(sessions).list() == []


def test_deleted_parent_and_changed_preview_never_commit(env):
    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    view = service.review(pending["request_id"])
    with pytest.raises(ExternalAgentError, match="proposal_changed"):
        service.decide(view["request_id"], "0" * 64, "approve")
    ApplicationsRepository(sessions).delete(application.id)
    assert (
        service.decide(view["request_id"], view["proposal_digest"], "approve")["status"]
        == "conflict"
    )
    assert ApplicationEventsRepository(sessions).list() == []


def test_successful_reschedule_preserves_details_and_reminder_lead_time(env):
    sessions, service, application, _ = env
    events = ApplicationEventsRepository(sessions)
    event = events.create(
        ApplicationEventCreate(
            application.id,
            "interview",
            datetime(2026, 11, 1, 7),
            60,
            subtype="technical",
            tags=["important"],
            round=2,
            notes="原笔记",
            remind_at=datetime(2026, 11, 1, 6, 30),
        )
    )
    snapshot = service.call("get_event", {"event_id": event.id})
    pending = service.call(
        "propose_reschedule",
        {
            "idempotency_key": str(uuid4()),
            "application_id": application.id,
            "event_id": event.id,
            "expected_revision": snapshot["revision"],
            "scheduled_at": "2026-11-02T15:00:00+08:00",
            "duration_minutes": 30,
            "location": "线下",
        },
    )
    view = service.review(pending["request_id"])
    assert view["proposal"]["after"]["remind_at"] == "2026-11-02T06:30:00Z"
    result = service.decide(view["request_id"], view["proposal_digest"], "approve")
    saved = result["event"]
    assert saved["id"] == event.id
    assert saved["subtype"] == "technical"
    assert saved["tags"] == ["important"]
    assert saved["round"] == 2 and saved["notes"] == "原笔记"
    assert saved["remind_at"] == "2026-11-02T06:30:00Z"
    assert service.call("cancel_request", {"request_id": pending["request_id"]}) == result


def test_two_reschedule_requests_cannot_overwrite_each_other(env):
    sessions, service, application, _ = env
    events = ApplicationEventsRepository(sessions)
    event = events.create(
        ApplicationEventCreate(application.id, "interview", datetime(2026, 11, 1), 60)
    )
    revision = service.call("get_event", {"event_id": event.id})["revision"]
    views = []
    for location in ("第一处", "第二处"):
        pending = service.call(
            "propose_reschedule",
            {
                "idempotency_key": str(uuid4()),
                "application_id": application.id,
                "event_id": event.id,
                "expected_revision": revision,
                "scheduled_at": "2026-11-02T15:00:00Z",
                "duration_minutes": 30,
                "location": location,
            },
        )
        views.append(service.review(pending["request_id"]))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda view: service.decide(view["request_id"], view["proposal_digest"], "approve"),
                views,
            )
        )
    assert sorted(result["status"] for result in results) == ["committed", "conflict"]
    assert len(events.list()) == 1


@pytest.mark.parametrize("value", ["2026-11-01T15:00:00", "bad-time"])
def test_ambiguous_times_are_not_scheduled(env, value):
    _, service, application, _ = env
    with pytest.raises(ExternalAgentError, match="invalid_input"):
        service.call("propose_interview", proposal(application.id, scheduled_at=value))


def test_explicit_scope_required_and_pagination(env):
    sessions, _, first, second = env
    with pytest.raises(ExternalAgentError, match="explicit_scope_required"):
        create_connection(sessions, "Empty", [])
    with pytest.raises(ExternalAgentError, match="explicit_scope_required"):
        create_connection(sessions, "Both", [first.id], workspace=True)
    full = create_connection(sessions, "Workspace", [], workspace=True)
    service = ExternalAgentService(sessions, full["connection_id"])
    page = service.call("list_applications", {"limit": 1})
    assert [item["id"] for item in page["items"]] == [first.id]
    assert page["next_after_id"] == first.id
    page = service.call("list_applications", {"limit": 1, "after_id": page["next_after_id"]})
    assert [item["id"] for item in page["items"]] == [second.id]
    assert page["next_after_id"] is None


def test_repository_failure_rolls_back_request_and_business_write(env, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from offerpilot.external_agents.models import ExternalAgentRequest

    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    view = service.review(pending["request_id"])
    real_create = ApplicationEventsRepository.create

    def failing_create(self, data):
        real_create(self, data)
        raise OperationalError("write", {}, RuntimeError("fail after flush"))

    with monkeypatch.context() as patch:
        patch.setattr(ApplicationEventsRepository, "create", failing_create)
        with pytest.raises(ExternalAgentError, match="service_unavailable") as raised:
            service.decide(view["request_id"], view["proposal_digest"], "approve")
        assert raised.value.retryable
    assert ApplicationEventsRepository(sessions).list() == []
    with sessions() as session:
        assert session.get(ExternalAgentRequest, pending["request_id"]).status == "pending"
    assert (
        service.decide(view["request_id"], view["proposal_digest"], "approve")["status"]
        == "committed"
    )


def test_operator_review_refuses_piped_approval(env, monkeypatch):
    import io
    import sys
    from offerpilot.external_agents.cli import review_in_terminal

    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"approve {pending['request_id']}\n"))
    with pytest.raises(ExternalAgentError, match="interactive_operator_terminal_required"):
        review_in_terminal(service, pending["request_id"])
    assert ApplicationEventsRepository(sessions).list() == []


def test_interactive_review_binds_the_displayed_payload_and_requires_exact_phrase(env, monkeypatch):
    import io
    import sys
    from offerpilot.external_agents.cli import review_in_terminal

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    sessions, service, application, _ = env
    pending = service.call(
        "propose_interview", proposal(application.id, notes="文本\x1b[31m\u202e危险\u2028换行\u2029换段")
    )
    output = Terminal()
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    with pytest.raises(ExternalAgentError, match="review_not_confirmed"):
        review_in_terminal(service, pending["request_id"])
    assert ApplicationEventsRepository(sessions).list() == []
    assert "云岚" in output.getvalue() and "\\u001b" in output.getvalue()
    assert "\x1b" not in output.getvalue() and "\u202e" not in output.getvalue()
    assert "\u2028" not in output.getvalue() and "\u2029" not in output.getvalue()
    assert "\\u2028" in output.getvalue() and "\\u2029" in output.getvalue()
    monkeypatch.setattr("builtins.input", lambda _: f"approve {pending['request_id']}")
    assert review_in_terminal(service, pending["request_id"])["status"] == "committed"


def test_durable_request_survives_restart_and_concurrent_retries(env):
    from sqlalchemy import func, select
    from offerpilot.external_agents.models import ExternalAgentRequest

    sessions, service, application, _ = env
    payload = proposal(application.id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.call("propose_interview", payload), range(2)))
    assert results[0] == results[1]
    resumed = ExternalAgentService(sessions, service.connection_id)
    assert resumed.call("propose_interview", payload) == results[0]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExternalAgentRequest)) == 1


def test_reads_coexist_with_reserved_writer_while_proposals_fail_boundedly(env, tmp_path):
    from offerpilot.external_agents.server import open_sessions

    sessions, service, application, _ = env
    fast_sessions = open_sessions(tmp_path)
    try:
        with sessions() as locker:
            locker.connection().exec_driver_sql("BEGIN IMMEDIATE")
            fast_service = ExternalAgentService(fast_sessions, service.connection_id)
            assert fast_service.call("list_applications", {})["items"]
            assert fast_service.grant()["active"]
            with pytest.raises(ExternalAgentError, match="service_unavailable") as raised:
                fast_service.call("propose_interview", proposal(application.id))
            assert raised.value.retryable
            locker.rollback()
        assert ExternalAgentService(fast_sessions, service.connection_id).call(
            "list_applications", {}
        )["items"]
    finally:
        fast_sessions.kw["bind"].dispose()


def test_cli_config_does_not_overwrite_existing_files_and_reports_missing_service(env, tmp_path):
    import json
    from typer.testing import CliRunner
    from offerpilot.cli import app

    _, service, _, _ = env
    output = tmp_path / "offerpilot.cordis.yml"
    command = [
        "mcp",
        "config",
        service.connection_id,
        "--format",
        "dsh",
        "--data-dir",
        str(tmp_path),
        "--output",
        str(output),
    ]
    runner = CliRunner()
    result = runner.invoke(app, command)
    assert result.exit_code == 0
    original = output.read_bytes()
    assert json.loads(original)[0]["insert"][0]["config"]["transport"] == "stdio"
    result = runner.invoke(app, command)
    assert result.exit_code == 2 and output.read_bytes() == original
    result = runner.invoke(
        app, ["mcp", "config", service.connection_id, "--data-dir", str(tmp_path / "missing")]
    )
    assert result.exit_code == 2 and "service_unavailable" in result.output


def test_external_schema_is_additive_and_reinitialization_preserves_receipts(env, tmp_path):
    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    view = service.review(pending["request_id"])
    result = service.decide(view["request_id"], view["proposal_digest"], "approve")
    refreshed_sessions = init_database(tmp_path / "data.db")
    try:
        assert (
            ExternalAgentService(refreshed_sessions, service.connection_id).call(
                "get_request", {"request_id": pending["request_id"]}
            )
            == result
        )
        assert len(ApplicationEventsRepository(sessions).list()) == 1
    finally:
        refreshed_sessions.kw["bind"].dispose()


def test_owner_can_cancel_after_source_deletion_and_receipts_hide_business_text(env):
    sessions, service, application, _ = env
    pending = service.call("propose_interview", proposal(application.id))
    committed = service.call("propose_interview", proposal(application.id, notes="不再可见的内容"))
    view = service.review(committed["request_id"])
    service.decide(view["request_id"], view["proposal_digest"], "approve")
    ApplicationsRepository(sessions).delete(application.id)
    safe = service.call("get_request", {"request_id": committed["request_id"]})
    assert safe["status"] == "committed" and safe["source_available"] is False
    assert "event" not in safe and "不再可见的内容" not in str(safe)
    view = service.review(pending["request_id"])
    assert view["source_available"] is False
    cancelled = service.call("cancel_request", {"request_id": pending["request_id"]})
    assert cancelled["status"] == "cancelled"
    assert (
        service.decide(view["request_id"], view["proposal_digest"], "approve")["status"]
        == "cancelled"
    )


def test_out_of_scope_event_ids_do_not_reveal_existence(env):
    sessions, service, first, second = env
    foreign = ApplicationEventsRepository(sessions).create(
        ApplicationEventCreate(
            second.id,
            "interview",
            datetime(2026, 11, 1),
            60,
        )
    )
    for event_id in [foreign.id, foreign.id + 1000]:
        with pytest.raises(ExternalAgentError, match="event_not_found"):
            service.call("get_event", {"event_id": event_id})
        with pytest.raises(ExternalAgentError, match="event_not_found"):
            service.call(
                "propose_reschedule",
                {
                    "application_id": first.id,
                    "event_id": event_id,
                    "expected_revision": "0" * 64,
                    "idempotency_key": str(uuid4()),
                    "scheduled_at": "2026-11-01T15:00:00Z",
                    "duration_minutes": 60,
                },
            )


def test_chat_auto_approval_does_not_grant_external_write_consent(env, tmp_path):
    from offerpilot.config import Config, save_config

    sessions, service, application, _ = env
    save_config(tmp_path, Config(chat_auto_approve_writes=True))
    pending = service.call("propose_interview", proposal(application.id))
    assert pending["status"] == "pending"
    assert ApplicationEventsRepository(sessions).list() == []
