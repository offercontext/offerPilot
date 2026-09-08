from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import (
    MockInterviewAttempt,
    MockInterviewFeedbackProposal,
    MockInterviewReviewDraft,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text


class MockInterviewReviewDraftAlreadyConfirmed(ValueError):
    pass


class MockInterviewReviewDraftValidationError(ValueError):
    pass


_ASCII_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


class MockInterviewReviewDraftRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def confirm_review_draft(
        self,
        application_id: int,
        event_id: int,
        attempt_id: int,
        proposal_id: int,
        confirmation_idempotency_key: str,
        selected_blocks: list[dict[str, Any]],
    ) -> tuple[MockInterviewReviewDraft, bool]:
        if _ASCII_KEY.fullmatch(confirmation_idempotency_key or "") is None:
            raise MockInterviewReviewDraftValidationError(
                "confirmation_idempotency_key must be an ASCII idempotency key"
            )
        if not selected_blocks:
            raise MockInterviewReviewDraftValidationError("at least one block is required")
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.application_id != application_id
                or attempt.event_id != event_id
            ):
                raise LookupError("attempt not found")
            proposal = session.get(MockInterviewFeedbackProposal, proposal_id)
            if (
                proposal is None
                or proposal.attempt_id != attempt_id
                or proposal.proposal_status != "normal"
            ):
                raise MockInterviewReviewDraftValidationError("proposal is not confirmable")
            existing = session.scalar(
                select(MockInterviewReviewDraft).where(
                    MockInterviewReviewDraft.proposal_id == proposal_id
                )
            )
            if existing is not None:
                if existing.confirmation_idempotency_key == confirmation_idempotency_key:
                    session.expunge(existing)
                    return existing, False
                raise MockInterviewReviewDraftAlreadyConfirmed(
                    "mock_interview_review_draft_already_confirmed"
                )
            proposal_json = json.loads(proposal.proposal_json)
            blocks_by_id = {
                item["id"]: item
                for field in ("strengths", "practice_points", "follow_up_questions", "next_practice_steps")
                for item in proposal_json.get(field, [])
            }
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            for block in selected_blocks:
                if not isinstance(block, dict):
                    raise MockInterviewReviewDraftValidationError("invalid selected block")
                block_id = block.get("id")
                if not isinstance(block_id, str) or block_id in seen or block_id not in blocks_by_id:
                    raise MockInterviewReviewDraftValidationError("selected block is not in proposal")
                original = blocks_by_id[block_id]
                text = block.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 1000:
                    raise MockInterviewReviewDraftValidationError("selected block text is invalid")
                if block.get("evidence_refs") != original.get("evidence_refs"):
                    raise MockInterviewReviewDraftValidationError("evidence refs cannot be changed")
                normalized.append(
                    {"id": block_id, "text": text, "evidence_refs": original["evidence_refs"]}
                )
                seen.add(block_id)
            encoded = canonical_json(normalized)
            draft = MockInterviewReviewDraft(
                attempt_id=attempt.id,
                proposal_id=proposal.id,
                confirmation_idempotency_key=confirmation_idempotency_key,
                application_id=attempt.application_id,
                event_id=attempt.event_id,
                selected_blocks_json=encoded,
                content_hash=sha256_text(encoded),
                source_fingerprint=proposal.source_fingerprint,
                status="confirmed",
            )
            session.add(draft)
            session.commit()
            session.refresh(draft)
            session.expunge(draft)
            return draft, True
