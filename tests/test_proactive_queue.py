from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import Application, ApplicationEvent, Conversation
from offerpilot.proactive.contracts import ProactivePolicy, ProactivePolicyUpdate
from offerpilot.proactive.models import ProactiveJob
from offerpilot.proactive.repository import ProactiveConflict, ProactiveRepository, quiet

NOW = datetime(2026, 9, 9, 4, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def queue(tmp_path):
    sessions = init_database(tmp_path / "proactive.db")
    with sessions() as session:
        app = Application(company_name="公司", position_name="工程师", updated_at=datetime.fromtimestamp(NOW, timezone.utc))
        session.add(app)
        session.flush()
        event = ApplicationEvent(application_id=app.id, event_type="interview", status="todo",
            scheduled_at=datetime.fromtimestamp(NOW + 3600, timezone.utc))
        session.add(event)
        session.commit()
        ids = app.id, event.id
    return sessions, ProactiveRepository(sessions), ids


def enable(repo, app_id, *, draft=False, enabled=True):
    repo.update_settings(ProactivePolicyUpdate(expected_revision=repo.settings()["revision"], confirmed=True,
        settings=ProactivePolicy(enabled=enabled, reminders_enabled=not draft, drafts_enabled=draft,
            application_ids=[app_id])))


def test_disabled_by_default_and_concurrent_claim_is_single_owner(queue):
    _, repo, (app_id, _) = queue
    assert repo.discover(NOW) == 0
    enable(repo, app_id)
    assert repo.discover(NOW) == 1
    assert repo.discover(NOW) == 0
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda owner: repo.claim(owner, NOW), ["one", "two"]))
    assert sum(item is not None for item in claims) == 1


@pytest.mark.parametrize("change", ["source", "disable", "cancel"])
def test_late_result_cannot_publish_after_source_or_scope_change(queue, change):
    sessions, repo, (app_id, event_id) = queue
    enable(repo, app_id, draft=True)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    job = repo.begin(job_id, "worker", NOW)
    assert job["turn_id"] and job["execution_generation"] == 1
    if change == "source":
        with sessions() as session:
            session.get(ApplicationEvent, event_id).status = "cancelled"
            session.commit()
    elif change == "disable":
        enable(repo, app_id, draft=True, enabled=False)
    else:
        repo.cancel(job_id)
    assert not repo.publish(job_id, "worker", job["turn_id"], 1, "迟到草稿", NOW + 1)
    assert not repo.list_jobs()[0]["result_text"]


def test_unknown_model_result_is_not_retried_and_old_generation_cannot_publish(queue):
    _, repo, (app_id, _) = queue
    enable(repo, app_id, draft=True)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    job = repo.begin(job_id, "worker", NOW)
    assert repo.claim("restart", NOW + 121) is None
    assert repo.list_jobs()[0]["state"] == "result_unknown"
    assert not repo.publish(job_id, "worker", job["turn_id"], 1, "旧草稿", NOW + 122)
    assert repo.discover(NOW + 122) == 0


def test_draft_conversation_context_ref_uses_domain_string_type(queue):
    sessions, repo, (app_id, _) = queue
    enable(repo, app_id, draft=True)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    job = repo.begin(job_id, "worker", NOW)
    assert job is not None

    with sessions() as session:
        conversation = session.get(Conversation, job["conversation_id"])
        assert conversation is not None
        assert conversation.context_type == "application"
        assert type(conversation.context_ref) is str
        assert conversation.context_ref == str(app_id)


def test_publish_dedup_and_revision_change_does_not_bypass_subject_rate_limit(queue):
    sessions, repo, (app_id, event_id) = queue
    enable(repo, app_id)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    job = repo.begin(job_id, "worker", NOW)
    assert repo.publish(job_id, "worker", job["turn_id"], 1, "面试提醒", NOW + 1)
    assert not repo.publish(job_id, "worker", job["turn_id"], 1, "重复提醒", NOW + 2)
    with sessions() as session:
        session.get(ApplicationEvent, event_id).round = 2
        session.commit()
    assert repo.discover(NOW + 3) == 0
    assert repo.list_jobs()[0]["result_text"] == ""


def test_completed_job_cannot_be_cancelled_or_erase_result(queue):
    _, repo, (app_id, _) = queue
    enable(repo, app_id)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    job = repo.begin(job_id, "worker", NOW)
    assert repo.publish(job_id, "worker", job["turn_id"], job["execution_generation"], "已完成提醒", NOW + 1)

    with pytest.raises(ProactiveConflict, match="job_already_terminal"):
        repo.cancel(job_id)

    stored = repo.list_jobs()[0]
    assert stored["state"] == "succeeded"
    assert stored["result_text"] == "已完成提醒"


def test_source_change_is_persisted_before_runtime_cancel_request(queue):
    from offerpilot.proactive.runtime import ProactiveRuntime

    sessions, repo, (app_id, event_id) = queue
    enable(repo, app_id, draft=True)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    assert repo.begin(job_id, "worker", NOW) is not None
    with sessions() as session:
        session.get(ApplicationEvent, event_id).status = "cancelled"
        session.commit()

    observed_states = []

    class InspectingManager:
        def interrupt(self, turn_id, *, generation):
            del turn_id, generation
            with sessions() as session:
                observed_states.append(session.get(ProactiveJob, job_id).state)

    runtime = ProactiveRuntime(repo, InspectingManager(), lambda source, budget: "ignored", clock=lambda: NOW)
    runtime.stop_cancelled()

    assert observed_states == ["cancelled"]
    with sessions() as session:
        stored = session.get(ProactiveJob, job_id)
        assert stored.state == "cancelled"
        assert stored.error_code == "source_changed"
        assert stored.result_text == ""
    repo.cancel(job_id)
    with sessions() as session:
        stored = session.get(ProactiveJob, job_id)
        assert stored.state == "cancelled"
        assert stored.error_code == "source_changed"


def test_remind_at_window_discovers_event_scheduled_far_in_future(queue):
    sessions, repo, (app_id, event_id) = queue
    with sessions() as session:
        event = session.get(ApplicationEvent, event_id)
        event.scheduled_at = datetime.fromtimestamp(NOW + 7 * 86400, timezone.utc)
        event.remind_at = datetime.fromtimestamp(NOW, timezone.utc)
        session.commit()

    enable(repo, app_id)
    assert repo.discover(NOW) == 1
    assert repo.list_jobs()[0]["event_id"] == event_id


def test_remind_at_window_keeps_candidate_limit_bounded(queue):
    sessions, repo, (app_id, original_event_id) = queue
    with sessions() as session:
        for _ in range(101):
            session.add(ApplicationEvent(
                application_id=app_id,
                event_type="interview",
                status="todo",
                scheduled_at=datetime.fromtimestamp(NOW + 7 * 86400, timezone.utc),
                remind_at=datetime.fromtimestamp(NOW, timezone.utc),
            ))
        session.commit()

    enable(repo, app_id)
    assert repo.discover(NOW) == 100
    jobs = repo.list_jobs()
    assert len(jobs) == 100
    assert all(job["event_id"] != original_event_id for job in jobs)


def test_no_model_budget_spent_for_reminder(queue):
    sessions, repo, (app_id, _) = queue
    enable(repo, app_id)
    repo.discover(NOW)
    job_id = repo.claim("worker", NOW)
    repo.begin(job_id, "worker", NOW)
    with sessions() as session:
        assert session.scalar(select(ProactiveJob)).model_started_at is None


def test_quiet_hours_follow_timezone_and_dst():
    policy = ProactivePolicy(timezone="America/New_York")
    assert quiet(policy, datetime(2026, 3, 8, 7, tzinfo=timezone.utc).timestamp())
    assert not quiet(policy, datetime(2026, 3, 8, 13, tzinfo=timezone.utc).timestamp())
    assert quiet(ProactivePolicy(timezone="Asia/Shanghai"), datetime(2026, 9, 9, 15, tzinfo=timezone.utc).timestamp())


def test_shared_runtime_draft_is_single_call_and_cancel_fences_late_result(queue):
    from threading import Event
    from offerpilot.pilot_runtime.managed_execution import RuntimeExecutionManager
    from offerpilot.proactive.runtime import ProactiveRuntime
    _, repo, (app_id, _) = queue
    enable(repo, app_id, draft=True)
    entered, release, finished = Event(), Event(), Event()
    calls = []

    def draft(source, budget):
        budget.reserve_model_call()
        calls.append(source)
        entered.set()
        assert release.wait(5)
        finished.set()
        return "迟到的模型草稿"

    manager = RuntimeExecutionManager(run_workers=1, max_queue=2)
    runtime = ProactiveRuntime(repo, manager, draft, clock=lambda: NOW)
    try:
        runtime.tick()
        assert entered.wait(5)
        job = repo.list_jobs()[0]
        repo.cancel(job["id"])
        runtime.stop_cancelled()
        release.set()
        assert finished.wait(5)
        runtime.tick()
        assert len(calls) == 1
        assert repo.list_jobs()[0]["state"] == "cancelled"
        assert repo.list_jobs()[0]["result_text"] == ""
    finally:
        release.set()
        runtime.stop()
        manager.close(wait=True)


def test_configured_draft_provider_has_one_attempt_no_tools_and_output_cap(monkeypatch):
    from offerpilot.ai.client import ConfiguredAIClient
    from offerpilot.ai.types import Message
    from offerpilot.config import Config
    captured = []

    def completion(**payload):
        captured.append(payload)
        return {"choices": [{"message": {"content": "准备清单"}}]}

    monkeypatch.setattr("offerpilot.ai.client.completion", completion)
    client = ConfiguredAIClient(Config(api_key="test-key"))
    assert client.complete_readonly_draft([Message(role="user", content="自动准备来源")], timeout_seconds=5).content == "准备清单"
    assert len(captured) == 1
    assert captured[0]["num_retries"] == 0 and captured[0]["timeout"] == 5
    assert captured[0]["max_tokens"] <= 1024
    assert "tools" not in captured[0]
