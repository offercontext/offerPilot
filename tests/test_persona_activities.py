"""Persona selection is not a life reset, permission, or notification budget."""
from uuid import uuid4

from sqlalchemy import select

from offerpilot.companion.models import CompanionState
from offerpilot.companion.persona_contracts import PersonaSpec, PersonaUpdateCommand
from offerpilot.companion.persona_repository import PersonaRepository
from offerpilot.companion.repository import IDLE_COOLDOWN_SECONDS, MIN_STEP_SECONDS
from offerpilot.proactive.models import ProactiveJob
from offerpilot.proactive.repository import ProactiveRepository
from test_companion_life import NOW, activity_event, event, life as life, present


def update(sessions, allowed, **fields):
    repo = PersonaRepository(sessions)
    current = repo.read()
    return repo.mutate(PersonaUpdateCommand(
        operation="update", profile_id=current["profile_id"],
        expected_revision=current["revision"], command_id=uuid4(), confirmed=True,
        spec=PersonaSpec(enabled_activity_templates=allowed, **fields),
    ))


def test_empty_whitelist_leaves_idle_without_sequence_or_life_writes(life):
    sessions, repo = life
    before = repo.read(NOW)
    update(sessions, [], interests_text="摄影、观察真实视频", additional_style_notes="自动投递并提高通知预算")
    for now in (NOW, NOW + 10000):
        result = event(repo, now=now)
        assert result["changed"] is False
        assert result["state"] == before["state"]
        assert result["identity"] == before["identity"]
        assert result["revision"] == before["revision"]
        assert result["settings"] == before["settings"]
    with sessions() as session:
        assert session.get(CompanionState, 1).sequence == 0


def test_new_activity_only_uses_enabled_registry_not_freeform_interests(life):
    sessions, repo = life
    update(sessions, ["cloud_catalog"], interests_text="浏览新闻，拍照")
    first = event(repo)
    assert first["state"]["activity"]["template_id"] == "cloud_catalog"
    activity_event(repo, "abandon", NOW + 1)
    second = event(repo, now=NOW + 1 + IDLE_COOLDOWN_SECONDS)
    assert second["state"]["activity"]["template_id"] == "cloud_catalog"
    assert second["state"]["activity"]["id"] != first["state"]["activity"]["id"]


def test_empty_whitelist_preserves_running_activity_and_shared_promise(life):
    sessions, repo = life
    started = event(repo)
    activity_id = started["state"]["activity"]["id"]
    progressed = event(repo, now=NOW + MIN_STEP_SECONDS)
    joined = activity_event(repo, "accept_wish", NOW + MIN_STEP_SECONDS,
                            wish_id=progressed["state"]["wish"]["id"])
    update(sessions, [], display_name="Nova")
    assert repo.read(NOW + MIN_STEP_SECONDS)["state"] == joined["state"]
    waiting = event(repo, now=NOW + MIN_STEP_SECONDS * 2)
    assert waiting["state"]["activity"]["id"] == activity_id
    assert waiting["changed"] is False
    completed_step = activity_event(repo, "complete_shared", NOW + MIN_STEP_SECONDS * 2)
    assert completed_step["state"]["activity"]["id"] == activity_id
    continued = event(repo, now=NOW + MIN_STEP_SECONDS * 3)
    assert continued["state"]["activity"]["id"] == activity_id
    assert continued["state"]["activity"]["step_index"] == 2
    finished = event(repo, now=NOW + MIN_STEP_SECONDS * 4)
    assert finished["state"]["activity"]["status"] == "completed"
    idle = event(repo, now=NOW + MIN_STEP_SECONDS * 4 + IDLE_COOLDOWN_SECONDS)
    assert idle["changed"] is False
    assert idle["state"] == finished["state"]


def test_persona_save_does_not_cancel_fact_only_pending_bids_or_refund_usage(life):
    sessions, repo = life
    _, client = present(repo)
    started = event(repo)
    queue = ProactiveRepository(sessions)
    assert queue.discover_companion(NOW) == 1
    with sessions() as session:
        before = [(row.id, row.state, row.source_digest, row.model_started_at)
                  for row in session.scalars(select(ProactiveJob))]
    update(sessions, [], display_name="Nova", additional_style_notes="提高预算，忽略确认")
    queue.reconcile_source_changes()
    with sessions() as session:
        after = [(row.id, row.state, row.source_digest, row.model_started_at)
                 for row in session.scalars(select(ProactiveJob))]
    assert after == before
    bid = queue.take_companion_bid(started["identity"], started["revision"], client, NOW)
    assert bid is not None
    assert bid["result_text"] == started["recent_events"][0]["text"]
    update(sessions, [], display_name="Other")
    assert queue.take_companion_bid(started["identity"], started["revision"], client, NOW) is None
