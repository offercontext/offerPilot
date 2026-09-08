from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.ai.offer_negotiation import build_offer_negotiation_snapshot
from offerpilot.models import (
    Offer,
    OfferComparisonDimension,
    OfferComparisonValue,
    OfferNegotiationBrief,
    OfferNegotiationProposal,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text


LEASE_SECONDS = 30
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class OfferNegotiationError(ValueError):
    def __init__(self, message: str, code: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class OfferNegotiationValidationError(OfferNegotiationError):
    def __init__(self, message: str, code: str = "offer_negotiation_invalid_request") -> None:
        super().__init__(message, code, 422)


class OfferNegotiationNotFoundError(OfferNegotiationError):
    def __init__(self, message: str, code: str = "offer_negotiation_offer_not_found") -> None:
        super().__init__(message, code, 404)


class OfferNegotiationConflictError(OfferNegotiationError):
    def __init__(self, message: str, code: str = "offer_negotiation_idempotency_conflict") -> None:
        super().__init__(message, code, 409)


class OfferNegotiationUnverifiableError(OfferNegotiationError):
    def __init__(self, message: str = "offer negotiation output was not verifiable") -> None:
        super().__init__(message, "offer_negotiation_unverifiable", 502)


@dataclass
class OfferNegotiationGenerationResult:
    proposal: OfferNegotiationProposal
    snapshot: dict[str, Any]
    source_fingerprint: str
    should_call: bool
    pending: bool
    created: bool
    revision: int
    owner_token: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lease_until() -> datetime:
    return _utcnow() + timedelta(seconds=LEASE_SECONDS)


def _is_live(lease_expires_at: datetime | None) -> bool:
    if lease_expires_at is None:
        return False
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    return lease_expires_at > _utcnow()


class OfferNegotiationRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def prepare_or_replay(
        self,
        *,
        offer_id: int,
        dimension_ids: list[int],
        user_brief: dict[str, str],
        idempotency_key: str,
        expected_source_fingerprint: str | None = None,
    ) -> OfferNegotiationGenerationResult:
        self._validate_key(idempotency_key)
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            offer = session.get(Offer, offer_id)
            if offer is None:
                raise OfferNegotiationNotFoundError("offer is not visible")
            existing = session.scalar(
                select(OfferNegotiationProposal).where(
                    OfferNegotiationProposal.offer_id == offer_id,
                    OfferNegotiationProposal.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                result = self._replay_existing(
                    existing,
                    dimension_ids=dimension_ids,
                    user_brief=user_brief,
                    expected_source_fingerprint=expected_source_fingerprint,
                    session=session,
                )
                session.commit()
                return result

            snapshot = self._build_snapshot(session, offer, dimension_ids, user_brief)
            fingerprint = sha256_text(canonical_json(snapshot))
            if expected_source_fingerprint is not None and expected_source_fingerprint != fingerprint:
                raise OfferNegotiationConflictError("offer source changed", "offer_negotiation_source_changed")
            if existing is None:
                token = uuid4().hex
                row = OfferNegotiationProposal(
                    offer_id=offer_id,
                    application_id=offer.application_id,
                    idempotency_key=idempotency_key,
                    attempt_status="generating",
                    source_fingerprint=fingerprint,
                    input_snapshot_json=canonical_json(snapshot),
                    source_states_json=canonical_json({
                        "offer": "current",
                        "application_id": offer.application_id,
                        "dimension_ids": sorted(dimension_ids),
                    }),
                    provider_call_token=token,
                    lease_expires_at=_lease_until(),
                    revision=1,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(
                        select(OfferNegotiationProposal).where(
                            OfferNegotiationProposal.offer_id == offer_id,
                            OfferNegotiationProposal.idempotency_key == idempotency_key,
                        )
                    )
                    if existing is None:
                        raise
                    result = self._replay_existing(
                        existing,
                        dimension_ids=dimension_ids,
                        user_brief=user_brief,
                        expected_source_fingerprint=expected_source_fingerprint,
                        session=session,
                    )
                    session.commit()
                    return result
                session.refresh(row)
                return OfferNegotiationGenerationResult(
                    row, snapshot, fingerprint, True, False, True, row.revision, token
                )

            raise AssertionError("new offer negotiation proposal was not inserted")

    def preview(
        self,
        *,
        offer_id: int,
        dimension_ids: list[int],
        user_brief: dict[str, str],
    ) -> tuple[dict[str, Any], str]:
        """Read and hash the exact generation input without creating an Attempt."""
        with self._session_factory() as session:
            offer = session.get(Offer, offer_id)
            if offer is None:
                raise OfferNegotiationNotFoundError("offer is not visible")
            snapshot = self._build_snapshot(session, offer, dimension_ids, user_brief)
            return snapshot, sha256_text(canonical_json(snapshot))

    def complete_ready(
        self,
        *,
        proposal_id: int,
        revision: int,
        provider_call_token: str,
        proposal: dict[str, Any],
        proposal_hash: str,
    ) -> OfferNegotiationProposal:
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(OfferNegotiationProposal, proposal_id)
            if row is None:
                raise OfferNegotiationNotFoundError("proposal is not visible", "offer_negotiation_proposal_not_found")
            if row.attempt_status == "ready":
                session.commit()
                return row
            if not self._owns(row, revision, provider_call_token) or row.attempt_status != "generating":
                session.commit()
                raise OfferNegotiationConflictError("proposal generation ownership was lost")
            row.proposal_json = canonical_json(proposal)
            row.proposal_hash = proposal_hash
            row.attempt_status = "ready"
            row.provider_call_token = ""
            row.lease_expires_at = None
            row.ready_at = _utcnow()
            session.commit()
            session.refresh(row)
            return row

    def mark_provider_unknown(
        self, *, proposal_id: int, revision: int, provider_call_token: str
    ) -> OfferNegotiationProposal:
        return self._mark_status(
            proposal_id=proposal_id,
            revision=revision,
            provider_call_token=provider_call_token,
            status="provider_unknown",
            reason="",
        )

    def invalidate(
        self,
        *,
        proposal_id: int,
        revision: int,
        provider_call_token: str,
        reason: str,
    ) -> OfferNegotiationProposal:
        return self._mark_status(
            proposal_id=proposal_id,
            revision=revision,
            provider_call_token=provider_call_token,
            status="invalidated",
            reason=reason,
        )

    def get(self, proposal_id: int) -> OfferNegotiationProposal | None:
        with self._session_factory() as session:
            return session.get(OfferNegotiationProposal, proposal_id)

    def source_matches(self, proposal_id: int, offer: Offer | None) -> bool:
        if offer is None:
            return False
        with self._session_factory() as session:
            row = session.get(OfferNegotiationProposal, proposal_id)
            if row is None:
                return False
            return self._offer_snapshot_matches(session, row, offer)

    def confirm_proposal(
        self,
        *,
        proposal_id: int,
        confirmation_key: str,
        selected_blocks: list[str],
        edited_content: dict[str, str],
    ) -> tuple[OfferNegotiationBrief, bool]:
        self._validate_key(confirmation_key)
        if not selected_blocks or len(selected_blocks) > 32 or len(set(selected_blocks)) != len(selected_blocks):
            raise OfferNegotiationValidationError("selected blocks are invalid")
        if not isinstance(edited_content, dict):
            raise OfferNegotiationValidationError("edited content is invalid")
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            proposal = session.get(OfferNegotiationProposal, proposal_id)
            if proposal is None:
                raise OfferNegotiationNotFoundError(
                    "proposal is not visible", "offer_negotiation_proposal_not_found"
                )
            existing = session.scalar(
                select(OfferNegotiationBrief).where(OfferNegotiationBrief.proposal_id == proposal_id)
            )
            if existing is not None:
                session.commit()
                return existing, False
            if proposal.attempt_status != "ready" or proposal.proposal_json is None:
                raise OfferNegotiationConflictError(
                    "only a ready proposal can be confirmed", "offer_negotiation_proposal_not_ready"
                )
            offer = session.get(Offer, proposal.offer_id)
            if offer is None or not self._offer_snapshot_matches(session, proposal, offer):
                raise OfferNegotiationConflictError(
                    "offer source changed", "offer_negotiation_source_changed"
                )
            proposal_payload = json.loads(proposal.proposal_json)
            blocks = {
                item["id"]: item
                for field in ("communication_goals", "clarification_questions", "talking_points", "preparation_checks")
                for item in proposal_payload.get(field, [])
            }
            if any(block_id not in blocks for block_id in selected_blocks):
                raise OfferNegotiationValidationError("selected block is not in proposal")
            if any(
                not isinstance(block_id, str)
                or not isinstance(value, str)
                or not value.strip()
                or len(value) > 600
                for block_id, value in edited_content.items()
            ) or any(block_id not in selected_blocks for block_id in edited_content):
                raise OfferNegotiationValidationError("edited content is invalid")
            selected = [blocks[block_id] for block_id in selected_blocks]
            derived = {
                "blocks": selected,
                "edits": {block_id: edited_content.get(block_id, blocks[block_id]["text"]) for block_id in selected_blocks},
                "proposal_hash": proposal.proposal_hash,
            }
            brief = OfferNegotiationBrief(
                proposal_id=proposal.id,
                offer_id=proposal.offer_id,
                origin_application_id=proposal.application_id,
                confirmation_key=confirmation_key,
                selected_blocks_json=canonical_json(selected_blocks),
                edited_content_json=canonical_json(derived),
                content_hash=sha256_text(canonical_json(derived)),
            )
            session.add(brief)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(OfferNegotiationBrief).where(OfferNegotiationBrief.proposal_id == proposal_id)
                )
                if existing is None:
                    raise
                return existing, False
            session.refresh(brief)
            return brief, True

    def list_for_offer(self, offer_id: int) -> list[OfferNegotiationProposal]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(OfferNegotiationProposal)
                    .where(
                        OfferNegotiationProposal.offer_id == offer_id,
                        OfferNegotiationProposal.attempt_status == "ready",
                        OfferNegotiationProposal.proposal_json.is_not(None),
                    )
                    .order_by(OfferNegotiationProposal.created_at.desc(), OfferNegotiationProposal.id.desc())
                )
            )

    def get_brief(self, proposal_id: int) -> OfferNegotiationBrief | None:
        with self._session_factory() as session:
            return session.scalar(
                select(OfferNegotiationBrief).where(OfferNegotiationBrief.proposal_id == proposal_id)
            )

    def expire_for_test(self, proposal_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(OfferNegotiationProposal, proposal_id)
            assert row is not None
            row.lease_expires_at = _utcnow() - timedelta(seconds=1)
            session.commit()

    @staticmethod
    def _offer_snapshot_matches(session: Session, proposal: OfferNegotiationProposal, offer: Offer) -> bool:
        try:
            stored = json.loads(proposal.input_snapshot_json)
            brief = stored["user_brief"]
            offer_snapshot = stored["offer_snapshot"]
            dimensions = offer_snapshot["dimensions"]
            source_states = json.loads(proposal.source_states_json or "{}")
        except (KeyError, TypeError, ValueError):
            return False
        if stored.get("snapshot_version") is None:
            # v1 snapshots lack the status and dimension identities required to
            # prove that a confirmation still targets the original facts. They
            # remain readable as historical, source-changed records only.
            return False
        if (
            stored.get("snapshot_version") != 1
            or proposal.application_id != offer.application_id
            or source_states.get("application_id") != offer.application_id
        ):
            return False
        fields = (
            "company_name", "position_name", "status", "base_monthly", "months_per_year", "signing_bonus",
            "equity", "perks", "deadline", "notes",
        )
        if any(offer_snapshot.get(field) != getattr(offer, field, None) for field in fields):
            return False
        current_dimensions: list[dict[str, Any]] = []
        dimension_ids = source_states.get("dimension_ids")
        if not isinstance(dimension_ids, list) or len(dimension_ids) != len(dimensions):
            return False
        for dimension, dimension_id in zip(dimensions, dimension_ids, strict=True):
            if not isinstance(dimension_id, int) or isinstance(dimension_id, bool) or dimension_id <= 0:
                return False
            match = session.get(OfferComparisonDimension, dimension_id)
            if match is None:
                return False
            value = session.scalar(
                select(OfferComparisonValue).where(
                    OfferComparisonValue.offer_id == offer.id,
                    OfferComparisonValue.dimension_id == dimension_id,
                )
            )
            current_dimensions.append(
                {"id": match.id, "label": match.label, "value_text": value.value_text if value else None}
            )
        current = build_offer_negotiation_snapshot(
            offer={field: getattr(offer, field, None) for field in fields},
            dimensions=current_dimensions,
            user_brief=brief,
            idempotency_key="",
        )
        return sha256_text(canonical_json(current)) == proposal.source_fingerprint

    @staticmethod
    def _validate_key(idempotency_key: str) -> None:
        if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise OfferNegotiationValidationError("idempotency_key must be ASCII")

    @staticmethod
    def _build_snapshot(
        session: Session,
        offer: Offer,
        dimension_ids: list[int],
        user_brief: dict[str, str],
    ) -> dict[str, Any]:
        if not isinstance(dimension_ids, list) or len(dimension_ids) > 8:
            raise OfferNegotiationValidationError("at most eight dimensions are allowed", "offer_negotiation_too_many_dimensions")
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in dimension_ids):
            raise OfferNegotiationValidationError("dimension ids are invalid")
        if len(set(dimension_ids)) != len(dimension_ids):
            raise OfferNegotiationValidationError("dimension ids must be unique")
        for field in ("goal", "concerns", "scenario"):
            value = user_brief.get(field, "")
            if not isinstance(value, str) or not value.strip():
                raise OfferNegotiationValidationError("user brief is invalid")
        dimensions: list[dict[str, Any]] = []
        for dimension_id in sorted(dimension_ids):
            dimension = session.get(OfferComparisonDimension, dimension_id)
            if dimension is None or dimension.archived_at is not None:
                raise OfferNegotiationValidationError(
                    "only active dimensions may be selected", "offer_negotiation_dimension_not_available"
                )
            value = session.scalar(
                select(OfferComparisonValue).where(
                    OfferComparisonValue.offer_id == offer.id,
                    OfferComparisonValue.dimension_id == dimension_id,
                )
            )
            dimensions.append(
                {"id": dimension.id, "label": dimension.label, "value_text": value.value_text if value else None}
            )
        return build_offer_negotiation_snapshot(
            offer={
                "company_name": offer.company_name,
                "position_name": offer.position_name,
                "status": offer.status,
                "base_monthly": offer.base_monthly,
                "months_per_year": offer.months_per_year,
                "signing_bonus": offer.signing_bonus,
                "equity": offer.equity,
                "perks": offer.perks,
                "deadline": offer.deadline,
                "notes": offer.notes,
            },
            dimensions=dimensions,
            user_brief=user_brief,
            idempotency_key="",
        )

    def _existing_result(
        self,
        row: OfferNegotiationProposal,
        snapshot: dict[str, Any],
        fingerprint: str,
        session: Session,
    ) -> OfferNegotiationGenerationResult:
        if row.source_fingerprint != fingerprint:
            raise OfferNegotiationConflictError("source snapshot changed")
        if row.attempt_status == "ready":
            return OfferNegotiationGenerationResult(row, snapshot, fingerprint, False, False, False, row.revision, "")
        if row.attempt_status == "invalidated":
            if row.invalidation_reason == "contract_failed":
                raise OfferNegotiationUnverifiableError()
            raise OfferNegotiationConflictError(
                "offer negotiation attempt was invalidated", "offer_negotiation_attempt_invalidated"
            )
        if row.attempt_status in {"generating", "provider_unknown"} and _is_live(row.lease_expires_at):
            return OfferNegotiationGenerationResult(row, snapshot, fingerprint, False, True, False, row.revision, "")
        token = uuid4().hex
        row.attempt_status = "generating"
        row.revision += 1
        row.provider_call_token = token
        row.lease_expires_at = _lease_until()
        session.flush()
        return OfferNegotiationGenerationResult(row, snapshot, fingerprint, True, False, False, row.revision, token)

    def _replay_existing(
        self,
        row: OfferNegotiationProposal,
        *,
        dimension_ids: list[int],
        user_brief: dict[str, str],
        expected_source_fingerprint: str | None,
        session: Session,
    ) -> OfferNegotiationGenerationResult:
        try:
            snapshot = json.loads(row.input_snapshot_json)
            source_states = json.loads(row.source_states_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OfferNegotiationConflictError("stored offer snapshot is invalid") from exc
        if not self._request_matches_stored_snapshot(
            snapshot,
            source_states,
            dimension_ids=dimension_ids,
            user_brief=user_brief,
        ):
            raise OfferNegotiationConflictError("source snapshot changed")
        if (
            expected_source_fingerprint is not None
            and expected_source_fingerprint != row.source_fingerprint
        ):
            raise OfferNegotiationConflictError("offer source changed", "offer_negotiation_source_changed")
        return self._existing_result(row, snapshot, row.source_fingerprint, session)

    @staticmethod
    def _request_matches_stored_snapshot(
        snapshot: object,
        source_states: object,
        *,
        dimension_ids: list[int],
        user_brief: dict[str, str],
    ) -> bool:
        if not isinstance(snapshot, dict) or not isinstance(source_states, dict):
            return False
        stored_brief = snapshot.get("user_brief")
        stored_dimension_ids = source_states.get("dimension_ids")
        if not isinstance(stored_brief, dict) or not isinstance(stored_dimension_ids, list):
            return False
        if any(not isinstance(value, int) or isinstance(value, bool) for value in stored_dimension_ids):
            return False
        return (
            sorted(dimension_ids) == sorted(stored_dimension_ids)
            and {
                field: user_brief.get(field)
                for field in ("goal", "concerns", "scenario")
            }
            == {
                field: stored_brief.get(field)
                for field in ("goal", "concerns", "scenario")
            }
        )

    @staticmethod
    def _owns(row: OfferNegotiationProposal, revision: int, token: str) -> bool:
        return row.revision == revision and row.provider_call_token == token

    def _mark_status(
        self,
        *,
        proposal_id: int,
        revision: int,
        provider_call_token: str,
        status: str,
        reason: str,
    ) -> OfferNegotiationProposal:
        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(OfferNegotiationProposal, proposal_id)
            if row is None:
                raise OfferNegotiationNotFoundError("proposal is not visible", "offer_negotiation_proposal_not_found")
            if row.attempt_status == "ready":
                session.commit()
                return row
            if not self._owns(row, revision, provider_call_token) or row.attempt_status != "generating":
                session.commit()
                raise OfferNegotiationConflictError("proposal generation ownership was lost")
            row.attempt_status = status
            row.invalidation_reason = reason
            if status == "invalidated":
                row.provider_call_token = ""
                row.lease_expires_at = None
            session.commit()
            session.refresh(row)
            return row
