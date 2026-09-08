from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import Application, Offer, OfferComparisonDimension, OfferComparisonValue, OfferNegotiationProposal
from offerpilot.repositories.offer_negotiation import (
    OfferNegotiationConflictError,
    OfferNegotiationRepository,
    OfferNegotiationValidationError,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text


def _repo(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        offer = Offer(
            company_name="星云数据",
            position_name="后端工程师",
            base_monthly=28000,
            months_per_year=12,
            signing_bonus=0,
        )
        first = OfferComparisonDimension(label="通勤")
        second = OfferComparisonDimension(label="成长空间")
        session.add_all([offer, first, second])
        session.flush()
        session.add(OfferComparisonValue(offer_id=offer.id, dimension_id=first.id, value_text="地铁 35 分钟"))
        session.commit()
        offer_id = offer.id
        first_id = first.id
        second_id = second.id
    return factory, OfferNegotiationRepository(factory), offer_id, first_id, second_id


def test_first_claim_persists_snapshot_and_same_set_is_idempotent(tmp_path) -> None:
    factory, repository, offer_id, first_id, second_id = _repo(tmp_path)
    first = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[second_id, first_id],
        user_brief={"goal": "争取入职时间", "concerns": "通勤", "scenario": "电话沟通"},
        idempotency_key="A" * 16,
    )
    second = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id, second_id],
        user_brief={"goal": "争取入职时间", "concerns": "通勤", "scenario": "电话沟通"},
        idempotency_key="A" * 16,
    )
    assert first.should_call is True
    assert second.should_call is False
    assert second.proposal.id == first.proposal.id
    assert first.snapshot == second.snapshot
    assert "id" not in first.snapshot["offer_snapshot"]
    assert first.snapshot["offer_snapshot"]["dimensions"][0]["path_id"] == "dimension_001"
    assert first.snapshot["offer_snapshot"]["dimensions"][0]["value_text"] == "地铁 35 分钟"
    with factory() as session:
        assert session.scalar(select(OfferNegotiationProposal).where(OfferNegotiationProposal.id == first.proposal.id))


def test_key_different_snapshot_is_conflict_and_ready_is_immutable(tmp_path) -> None:
    factory, repository, offer_id, first_id, second_id = _repo(tmp_path)
    first = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标一", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="B" * 16,
    )
    with pytest.raises(OfferNegotiationConflictError) as error:
        repository.prepare_or_replay(
            offer_id=offer_id,
            dimension_ids=[second_id],
            user_brief={"goal": "目标二", "concerns": "顾虑", "scenario": "电话"},
            idempotency_key="B" * 16,
        )
    assert error.value.code == "offer_negotiation_idempotency_conflict"
    with factory() as session:
        row = session.get(OfferNegotiationProposal, first.proposal.id)
        assert row is not None
        assert row.attempt_status == "generating"
        assert row.revision == first.revision
        assert row.provider_call_token == first.owner_token
        assert row.lease_expires_at is not None


def test_duplicate_dimension_label_does_not_change_existing_source(tmp_path) -> None:
    factory, repository, offer_id, first_id, _ = _repo(tmp_path)
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="Z" * 16,
    )
    with factory() as session:
        session.add(OfferComparisonDimension(label="通勤"))
        session.commit()
    completed = repository.complete_ready(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
        proposal={"proposal_status": "safe_empty", "communication_goals": [], "clarification_questions": [], "talking_points": [], "preparation_checks": []},
        proposal_hash="hash",
    )
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        assert repository._offer_snapshot_matches(session, completed, offer)


def test_application_reassignment_invalidates_frozen_source(tmp_path) -> None:
    factory, repository, offer_id, first_id, _ = _repo(tmp_path)
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        first_application = Application(company_name="A", position_name="P")
        second_application = Application(company_name="B", position_name="P")
        session.add_all([first_application, second_application])
        session.flush()
        offer.application_id = first_application.id
        session.commit()
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "鐩爣", "concerns": "顾虑", "scenario": "鐢佃瘽"},
        idempotency_key="A" * 16,
    )
    completed = repository.complete_ready(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
        proposal={"proposal_status": "safe_empty", "communication_goals": [], "clarification_questions": [], "talking_points": [], "preparation_checks": []},
        proposal_hash="hash",
    )
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        offer.application_id = second_application.id
        session.commit()
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        assert not repository._offer_snapshot_matches(session, completed, offer)


def test_legacy_snapshot_is_read_only_when_identity_fields_are_missing(tmp_path) -> None:
    factory, repository, offer_id, first_id, _ = _repo(tmp_path)
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "鐩爣", "concerns": "顾虑", "scenario": "鐢佃瘽"},
        idempotency_key="L" * 16,
    )
    with factory() as session:
        row = session.get(OfferNegotiationProposal, claimed.proposal.id)
        offer = session.get(Offer, offer_id)
        assert row is not None and offer is not None
        current = json.loads(row.input_snapshot_json)
        legacy = {
            "offer_snapshot": {
                "company_name": offer.company_name,
                "position_name": offer.position_name,
                "base_monthly": offer.base_monthly,
                "months_per_year": offer.months_per_year,
                "signing_bonus": offer.signing_bonus,
                "equity": offer.equity,
                "perks": offer.perks,
                "deadline": offer.deadline,
                "notes": offer.notes,
                "assessment": offer.assessment,
            },
            "dimensions": [
                {"path_id": "dimension_001", "label": current["offer_snapshot"]["dimensions"][0]["label"], "value_text": current["offer_snapshot"]["dimensions"][0]["value_text"]}
            ],
            "user_brief": current["user_brief"],
        }
        row.input_snapshot_json = canonical_json(legacy)
        row.source_fingerprint = sha256_text(canonical_json(legacy))
        row.source_states_json = canonical_json({"offer": "current"})
        session.commit()
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        row = session.get(OfferNegotiationProposal, claimed.proposal.id)
        assert row is not None
        assert not repository._offer_snapshot_matches(session, row, offer)


def test_invalid_dimensions_do_not_create_attempt(tmp_path) -> None:
    factory, repository, offer_id, first_id, second_id = _repo(tmp_path)
    with pytest.raises(OfferNegotiationValidationError):
        repository.prepare_or_replay(
            offer_id=offer_id,
            dimension_ids=[first_id] * 2,
            user_brief={"goal": "", "concerns": "顾虑", "scenario": ""},
            idempotency_key="C" * 16,
        )
    with factory() as session:
        assert session.scalar(select(OfferNegotiationProposal)) is None


def test_lease_claim_and_cas_completion(tmp_path) -> None:
    _, repository, offer_id, first_id, _, = _repo(tmp_path)
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="D" * 16,
    )
    assert claimed.owner_token
    completed = repository.complete_ready(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
        proposal={"proposal_status": "safe_empty", "communication_goals": [], "clarification_questions": [], "talking_points": [], "preparation_checks": []},
        proposal_hash="hash",
    )
    assert completed.attempt_status == "ready"
    replay = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="D" * 16,
    )
    assert replay.proposal.id == completed.id
    assert replay.should_call is False


def test_late_provider_error_after_ready_does_not_change_ready_state(tmp_path) -> None:
    factory, repository, offer_id, first_id, _, = _repo(tmp_path)
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="L" * 16,
    )
    repository.complete_ready(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
        proposal={"proposal_status": "safe_empty", "communication_goals": [], "clarification_questions": [], "talking_points": [], "preparation_checks": []},
        proposal_hash="hash",
    )
    row = repository.mark_provider_unknown(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
    )
    assert row.attempt_status == "ready"
    with factory() as session:
        persisted = session.get(OfferNegotiationProposal, claimed.proposal.id)
        assert persisted is not None
        assert persisted.attempt_status == "ready"


def test_provider_unknown_is_pending_until_lease_expiry(tmp_path) -> None:
    _, repository, offer_id, first_id, _, = _repo(tmp_path)
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="E" * 16,
    )
    repository.mark_provider_unknown(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
    )
    pending = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="E" * 16,
    )
    assert pending.pending is True
    assert pending.should_call is False
    repository.expire_for_test(claimed.proposal.id)
    takeover = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief={"goal": "目标", "concerns": "顾虑", "scenario": "电话"},
        idempotency_key="E" * 16,
    )
    assert takeover.should_call is True
    assert takeover.revision == claimed.revision + 1
    assert takeover.owner_token != claimed.owner_token


def test_provider_unknown_takeover_reuses_frozen_snapshot_after_offer_change(tmp_path) -> None:
    factory, repository, offer_id, first_id, _, = _repo(tmp_path)
    brief = {"goal": "目标", "concerns": "顾虑", "scenario": "电话"}
    claimed = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief=brief,
        idempotency_key="F" * 16,
    )
    repository.mark_provider_unknown(
        proposal_id=claimed.proposal.id,
        revision=claimed.revision,
        provider_call_token=claimed.owner_token,
    )
    repository.expire_for_test(claimed.proposal.id)
    with factory() as session:
        offer = session.get(Offer, offer_id)
        assert offer is not None
        offer.base_monthly = 31000
        session.commit()

    takeover = repository.prepare_or_replay(
        offer_id=offer_id,
        dimension_ids=[first_id],
        user_brief=brief,
        idempotency_key="F" * 16,
        expected_source_fingerprint=claimed.source_fingerprint,
    )
    assert takeover.should_call is True
    assert takeover.snapshot == claimed.snapshot
    assert takeover.source_fingerprint == claimed.source_fingerprint
