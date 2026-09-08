from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import (
    Application,
    ApplicationEvent,
    MockInterviewFeedbackProposal,
    MockInterviewAttempt,
    ApplicationJDVersion,
    MockInterviewReviewDraft,
    MockInterviewTurn,
    Resume,
    InterviewPreparationProposal,
    InterviewPracticeCase,
    VoiceCoachingSnapshot,
)
from offerpilot.repositories.json_contract import canonical_json, sha256_text
from offerpilot.repositories.interview_preparation_proposals import (
    source_is_current_for_mock_interview,
)
from offerpilot.repositories.interview_practice_cases import validate_context


class MockInterviewIdempotencyConflict(ValueError):
    pass


class MockInterviewTurnIdempotencyConflict(ValueError):
    pass


class MockInterviewSourceChanged(ValueError):
    pass


class MockInterviewContractFailed(ValueError):
    def __init__(self, message: str, attempt_id: int | None = None):
        super().__init__(message)
        self.attempt_id = attempt_id


class MockInterviewAttemptConfirmed(ValueError):
    pass


_ASCII_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
MAX_INTERVIEW_TURNS = 5


@dataclass(frozen=True)
class MockInterviewStartResult:
    attempt: MockInterviewAttempt
    turn: MockInterviewTurn
    created: bool = False
    question_claim: tuple[int, str, str] | None = None


@dataclass(frozen=True)
class MockInterviewFeedbackClaim:
    revision: int
    provider_call_token: str
    transcript_fingerprint: str
    turns: tuple[dict[str, Any], ...]

    def __iter__(self) -> Iterator[Any]:
        yield self.revision
        yield self.provider_call_token
        yield self.transcript_fingerprint

    def __getitem__(self, index: int) -> Any:
        return (self.revision, self.provider_call_token, self.transcript_fingerprint)[index]


@dataclass(frozen=True)
class MockInterviewQuestionClaim:
    revision: int
    provider_call_token: str
    transcript_fingerprint: str
    turns: tuple[dict[str, Any], ...]
    replay_turn: MockInterviewTurn | None = None

    def __iter__(self) -> Iterator[Any]:
        yield self.revision
        yield self.provider_call_token
        yield self.transcript_fingerprint

    def __getitem__(self, index: int) -> Any:
        return (self.revision, self.provider_call_token, self.transcript_fingerprint)[index]


class MockInterviewRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def get_attempt_by_key(
        self, application_id: int, event_id: int, idempotency_key: str
    ) -> MockInterviewAttempt | None:
        with self._session_factory() as session:
            attempt = session.scalar(
                select(MockInterviewAttempt).where(
                    MockInterviewAttempt.application_id == application_id,
                    MockInterviewAttempt.event_id == event_id,
                    MockInterviewAttempt.idempotency_key == idempotency_key,
                )
            )
            if attempt is not None:
                session.expunge(attempt)
            return attempt

    def create_or_replay_start(
        self,
        application_id: int,
        event_id: int,
        resume_id: int,
        jd_text: str,
        preparation_proposal_id: int | None,
        attempt_idempotency_key: str,
        initial_question_idempotency_key: str,
        preparation_selection: dict[str, Any] | None = None,
        jd_version_id: int | None = None,
    ) -> MockInterviewStartResult:
        if not jd_text.strip():
            raise ValueError("jd_text must not be blank")
        _validate_key(attempt_idempotency_key, "attempt_idempotency_key")
        _validate_key(initial_question_idempotency_key, "initial_question_idempotency_key")

        with self._session_factory() as session:
            self._begin_immediate(session)
            existing = session.scalar(
                select(MockInterviewAttempt).where(
                    MockInterviewAttempt.application_id == application_id,
                    MockInterviewAttempt.event_id == event_id,
                    MockInterviewAttempt.idempotency_key == attempt_idempotency_key,
                )
            )
            if existing is not None:
                self._assert_attempt_sources(session, existing)
                if existing.jd_version_id != jd_version_id or existing.resume_id != resume_id:
                    raise MockInterviewIdempotencyConflict("mock interview input changed")
                try:
                    snapshot = json.loads(existing.input_snapshot_json)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise MockInterviewIdempotencyConflict("mock interview input changed") from exc
                if not isinstance(snapshot, dict) or not _preparation_selection_matches(
                    snapshot, preparation_proposal_id, preparation_selection
                ):
                    raise MockInterviewIdempotencyConflict("mock interview input changed")
                fingerprint = existing.source_fingerprint
                if existing.attempt_status == "contract_failed":
                    raise MockInterviewContractFailed("mock_interview_unverifiable", existing.id)
                turn = session.scalar(
                    select(MockInterviewTurn).where(
                        MockInterviewTurn.attempt_id == existing.id,
                        MockInterviewTurn.turn_no == 1,
                    )
                )
                if existing.source_fingerprint != fingerprint:
                    raise MockInterviewIdempotencyConflict("mock interview input changed")
                if turn is None or turn.question_idempotency_key != initial_question_idempotency_key:
                    raise MockInterviewTurnIdempotencyConflict("initial question key changed")
                lease_until = _as_utc(existing.provider_lease_until)
                if turn.turn_status == "generating_question" and (
                    lease_until is None or lease_until <= datetime.now(timezone.utc)
                ):
                    existing.generation_revision += 1
                    existing.provider_call_token = _token()
                    existing.provider_lease_until = _lease_until()
                    existing.attempt_status = "generating_question"
                    session.commit()
                    return MockInterviewStartResult(
                        existing,
                        turn,
                        False,
                        (
                            existing.generation_revision,
                            existing.provider_call_token,
                            existing.transcript_fingerprint,
                        ),
                    )
                return MockInterviewStartResult(existing, turn, False)

            current_version_id = session.scalar(
                select(ApplicationJDVersion.id)
                .where(ApplicationJDVersion.application_id == application_id)
                .order_by(ApplicationJDVersion.version_number.desc())
                .limit(1)
            )
            if jd_version_id is not None and current_version_id != jd_version_id:
                raise MockInterviewSourceChanged("mock interview source changed")
            version = session.get(ApplicationJDVersion, jd_version_id) if jd_version_id is not None else None
            if jd_version_id is not None and version is None:
                raise MockInterviewSourceChanged("mock interview source changed")
            frozen_jd_text = version.jd_text if version is not None else jd_text
            snapshot = self._build_input_snapshot(
                session,
                application_id,
                event_id,
                resume_id,
                frozen_jd_text,
                preparation_proposal_id,
                preparation_selection,
                jd_version_id,
            )
            fingerprint = sha256_text(canonical_json(snapshot))

            attempt = MockInterviewAttempt(
                application_id=application_id,
                event_id=event_id,
                resume_id=resume_id,
                jd_version_id=jd_version_id,
                idempotency_key=attempt_idempotency_key,
                input_snapshot_json=canonical_json(snapshot),
                source_fingerprint=fingerprint,
                attempt_status="generating_question",
                generation_revision=1,
                provider_call_token=_token(),
                provider_lease_until=_lease_until(),
                transcript_fingerprint=_transcript_fingerprint([]),
            )
            session.add(attempt)
            session.flush()
            turn = MockInterviewTurn(
                attempt_id=attempt.id,
                turn_no=1,
                question_idempotency_key=initial_question_idempotency_key,
                question_text="",
                question_source_snapshot_json=canonical_json(_question_source_snapshot(attempt)),
                turn_status="generating_question",
            )
            session.add(turn)
            session.commit()
            session.refresh(attempt)
            session.refresh(turn)
            return MockInterviewStartResult(
                attempt,
                turn,
                True,
                (attempt.generation_revision, attempt.provider_call_token, attempt.transcript_fingerprint),
            )

    def submit_answer(
        self,
        attempt_id: int,
        turn_no: int,
        answer_text: str,
        turn_idempotency_key: str,
    ) -> MockInterviewAttempt:
        _validate_key(turn_idempotency_key, "turn_idempotency_key")
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            turn = session.scalar(
                select(MockInterviewTurn).where(
                    MockInterviewTurn.attempt_id == attempt_id,
                    MockInterviewTurn.turn_no == turn_no,
                )
            )
            if attempt is None or turn is None:
                raise LookupError("mock interview turn not found")
            self._assert_attempt_sources(session, attempt)
            if turn.answer_text:
                if turn.turn_idempotency_key != turn_idempotency_key or turn.answer_text != answer_text:
                    raise MockInterviewTurnIdempotencyConflict("submitted answer changed")
                return attempt
            if turn.turn_status != "awaiting_answer":
                raise ValueError("turn is still being generated")
            if turn.turn_idempotency_key and turn.turn_idempotency_key != turn_idempotency_key:
                raise MockInterviewTurnIdempotencyConflict("submitted answer key changed")
            turn.turn_idempotency_key = turn_idempotency_key
            turn.answer_text = answer_text
            turn.answer_sha256 = sha256_text(answer_text)
            turn.turn_status = "answered"
            attempt.current_turn_no = max(attempt.current_turn_no, turn_no)
            turns = list(session.scalars(
                select(MockInterviewTurn)
                .where(MockInterviewTurn.attempt_id == attempt_id)
                .order_by(MockInterviewTurn.turn_no.asc())
            ).all())
            attempt.transcript_fingerprint = _transcript_fingerprint(turns)
            attempt.attempt_status = "awaiting_answer"
            session.commit()
            session.refresh(attempt)
            return attempt

    def create_or_replay_quick_start(
        self,
        *,
        practice_case_id: int,
        attempt_idempotency_key: str,
        initial_question_idempotency_key: str,
    ) -> MockInterviewStartResult:
        _validate_key(attempt_idempotency_key, "attempt_idempotency_key")
        _validate_key(initial_question_idempotency_key, "initial_question_idempotency_key")
        with self._session_factory() as session:
            self._begin_immediate(session)
            case = session.get(InterviewPracticeCase, practice_case_id)
            if case is None:
                raise LookupError("interview practice case not found")
            existing = session.scalar(
                select(MockInterviewAttempt).where(
                    MockInterviewAttempt.context_kind == "quick_practice",
                    MockInterviewAttempt.practice_case_id == practice_case_id,
                    MockInterviewAttempt.idempotency_key == attempt_idempotency_key,
                )
            )
            if existing is not None:
                self._assert_attempt_sources(session, existing)
                turn = session.scalar(
                    select(MockInterviewTurn).where(
                        MockInterviewTurn.attempt_id == existing.id,
                        MockInterviewTurn.turn_no == 1,
                    )
                )
                if turn is None or turn.question_idempotency_key != initial_question_idempotency_key:
                    raise MockInterviewTurnIdempotencyConflict("initial question key changed")
                if existing.attempt_status == "contract_failed":
                    raise MockInterviewContractFailed("mock_interview_unverifiable", existing.id)
                lease_until = _as_utc(existing.provider_lease_until)
                if turn.turn_status == "generating_question" and (
                    lease_until is None or lease_until <= datetime.now(timezone.utc)
                ):
                    existing.generation_revision += 1
                    existing.provider_call_token = _token()
                    existing.provider_lease_until = _lease_until()
                    existing.attempt_status = "generating_question"
                    session.commit()
                    session.refresh(existing)
                    session.refresh(turn)
                    return MockInterviewStartResult(
                        existing,
                        turn,
                        False,
                        (existing.generation_revision, existing.provider_call_token, existing.transcript_fingerprint),
                    )
                session.expunge(existing)
                session.expunge(turn)
                return MockInterviewStartResult(existing, turn, False)
            if case.status != "active":
                raise ValueError("interview practice case is archived")
            snapshot = self._build_quick_input_snapshot(case)
            fingerprint = sha256_text(canonical_json(snapshot))
            validate_context(
                context_kind="quick_practice",
                application_id=None,
                event_id=None,
                practice_case_id=case.id,
            )
            attempt = MockInterviewAttempt(
                context_kind="quick_practice",
                application_id=None,
                event_id=None,
                practice_case_id=case.id,
                resume_id=case.resume_id,
                jd_version_id=None,
                idempotency_key=attempt_idempotency_key,
                input_snapshot_json=canonical_json(snapshot),
                source_fingerprint=fingerprint,
                attempt_status="generating_question",
                generation_revision=1,
                provider_call_token=_token(),
                provider_lease_until=_lease_until(),
                transcript_fingerprint=_transcript_fingerprint([]),
            )
            session.add(attempt)
            session.flush()
            turn = MockInterviewTurn(
                attempt_id=attempt.id,
                turn_no=1,
                question_idempotency_key=initial_question_idempotency_key,
                question_source_snapshot_json=canonical_json(_question_source_snapshot(attempt)),
                turn_status="generating_question",
            )
            session.add(turn)
            session.commit()
            session.refresh(attempt)
            session.refresh(turn)
            return MockInterviewStartResult(
                attempt,
                turn,
                True,
                (attempt.generation_revision, attempt.provider_call_token, attempt.transcript_fingerprint),
            )

    def claim_question(
        self, attempt_id: int, turn_no: int, question_idempotency_key: str
    ) -> MockInterviewQuestionClaim | None:
        _validate_key(question_idempotency_key, "question_idempotency_key")
        if turn_no < 1 or turn_no > MAX_INTERVIEW_TURNS:
            raise ValueError("interview turn limit reached")
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                raise LookupError("mock interview attempt not found")
            if attempt.attempt_status == "contract_failed":
                raise MockInterviewContractFailed("mock_interview_unverifiable")
            self._assert_attempt_sources(session, attempt)
            existing_turn = session.scalar(
                select(MockInterviewTurn).where(
                    MockInterviewTurn.attempt_id == attempt_id,
                    MockInterviewTurn.turn_no == turn_no,
                )
            )
            now = datetime.now(timezone.utc)
            lease_until = _as_utc(attempt.provider_lease_until)
            if lease_until is not None and lease_until > now:
                return None
            if existing_turn is not None:
                if existing_turn.question_idempotency_key != question_idempotency_key:
                    raise MockInterviewTurnIdempotencyConflict("question key changed")
                if existing_turn.turn_status == "awaiting_answer":
                    session.expunge(attempt)
                    session.expunge(existing_turn)
                    return MockInterviewQuestionClaim(
                        attempt.generation_revision,
                        "",
                        attempt.transcript_fingerprint,
                        tuple(),
                        existing_turn,
                    )
                if existing_turn.turn_status not in {"awaiting_answer", "generating_question"}:
                    return None
            else:
                previous = session.scalar(
                    select(MockInterviewTurn).where(
                        MockInterviewTurn.attempt_id == attempt_id,
                        MockInterviewTurn.turn_no == turn_no - 1,
                    )
                )
                if turn_no < 2 or previous is None or not previous.answer_text.strip():
                    raise ValueError("previous turn must be answered")
            transcript_fingerprint = attempt.transcript_fingerprint
            frozen_turns = tuple(
                {
                    "turn_no": turn.turn_no,
                    "question": turn.question_text,
                    "answer": turn.answer_text,
                }
                for turn in session.scalars(
                    select(MockInterviewTurn)
                    .where(MockInterviewTurn.attempt_id == attempt_id)
                    .where(MockInterviewTurn.turn_status.in_(["answered", "awaiting_answer"]))
                    .order_by(MockInterviewTurn.turn_no.asc())
                ).all()
            )
            attempt.generation_revision += 1
            attempt.provider_call_token = _token()
            attempt.provider_lease_until = _lease_until()
            attempt.attempt_status = "generating_question"
            if existing_turn is None:
                session.add(
                    MockInterviewTurn(
                        attempt_id=attempt_id,
                        turn_no=turn_no,
                        question_idempotency_key=question_idempotency_key,
                        question_source_snapshot_json=canonical_json(_question_source_snapshot(attempt)),
                        turn_status="generating_question",
                    )
                )
            else:
                existing_turn.turn_status = "generating_question"
            session.commit()
            return MockInterviewQuestionClaim(
                attempt.generation_revision,
                attempt.provider_call_token,
                transcript_fingerprint,
                frozen_turns,
            )

    def complete_question(
        self,
        attempt_id: int,
        turn_no: int,
        revision: int,
        provider_call_token: str,
        transcript_fingerprint: str,
        question_text: str,
        evidence_refs: list[dict[str, str]] | None = None,
    ) -> MockInterviewAttempt | None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            turn = session.scalar(
                select(MockInterviewTurn).where(
                    MockInterviewTurn.attempt_id == attempt_id,
                    MockInterviewTurn.turn_no == turn_no,
                )
            )
            if attempt is None or turn is None:
                return None
            if (
                attempt.generation_revision != revision
                or attempt.provider_call_token != provider_call_token
                or attempt.transcript_fingerprint != transcript_fingerprint
            ):
                return None
            try:
                self._assert_attempt_sources(session, attempt)
            except MockInterviewSourceChanged:
                attempt.attempt_status = "source_conflict"
                attempt.failure_category = "source_conflict"
                attempt.provider_lease_until = None
                session.commit()
                raise
            turn.question_text = question_text
            if evidence_refs is not None:
                source_snapshot = json.loads(turn.question_source_snapshot_json or "{}")
                if not isinstance(source_snapshot, dict):
                    source_snapshot = {}
                source_snapshot["selected_evidence_refs"] = evidence_refs
                turn.question_source_snapshot_json = canonical_json(source_snapshot)
            turn.turn_status = "awaiting_answer"
            attempt.current_turn_no = max(attempt.current_turn_no, turn_no)
            attempt.attempt_status = "awaiting_answer"
            attempt.provider_lease_until = None
            session.commit()
            session.refresh(attempt)
            return attempt

    def mark_provider_unknown(
        self, attempt_id: int, revision: int, provider_call_token: str, operation: str
    ) -> MockInterviewAttempt | None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                return None
            if attempt.generation_revision != revision or attempt.provider_call_token != provider_call_token:
                return None
            try:
                self._assert_attempt_sources(session, attempt)
            except MockInterviewSourceChanged:
                attempt.attempt_status = "source_conflict"
                attempt.failure_category = "source_conflict"
                attempt.provider_lease_until = None
                session.commit()
                raise
            attempt.attempt_status = "provider_unknown"
            attempt.failure_category = "provider_error"
            session.commit()
            session.refresh(attempt)
            return attempt

    def claim_feedback(
        self, attempt_id: int, feedback_idempotency_key: str
    ) -> MockInterviewFeedbackClaim | None:
        _validate_key(feedback_idempotency_key, "feedback_idempotency_key")
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                raise LookupError("mock interview attempt not found")
            if attempt.attempt_status == "contract_failed":
                raise MockInterviewContractFailed("mock_interview_unverifiable")
            self._assert_attempt_sources(session, attempt)
            existing = session.scalar(
                select(MockInterviewFeedbackProposal).where(
                    MockInterviewFeedbackProposal.attempt_id == attempt_id,
                    MockInterviewFeedbackProposal.idempotency_key == feedback_idempotency_key,
                )
            )
            if existing is not None:
                return None
            now = datetime.now(timezone.utc)
            lease_until = _as_utc(attempt.provider_lease_until)
            if lease_until is not None and lease_until > now:
                return None
            attempt.generation_revision += 1
            attempt.provider_call_token = _token()
            attempt.provider_lease_until = _lease_until()
            attempt.attempt_status = "generating_feedback"
            attempt.failure_category = ""
            revision = attempt.generation_revision
            token = attempt.provider_call_token
            transcript_fingerprint = attempt.transcript_fingerprint
            turns = tuple(
                {
                    "turn_no": turn.turn_no,
                    "question": turn.question_text,
                    "answer": turn.answer_text,
                }
                for turn in session.scalars(
                    select(MockInterviewTurn)
                    .where(MockInterviewTurn.attempt_id == attempt_id)
                    .order_by(MockInterviewTurn.turn_no.asc())
                ).all()
            )
            session.commit()
            return MockInterviewFeedbackClaim(
                revision, token, transcript_fingerprint, turns
            )

    def get_feedback(
        self, attempt_id: int, feedback_idempotency_key: str
    ) -> tuple[MockInterviewFeedbackProposal | None, bool]:
        _validate_key(feedback_idempotency_key, "feedback_idempotency_key")
        with self._session_factory() as session:
            record = session.scalar(
                select(MockInterviewFeedbackProposal).where(
                    MockInterviewFeedbackProposal.attempt_id == attempt_id,
                    MockInterviewFeedbackProposal.idempotency_key == feedback_idempotency_key,
                )
            )
            if record is None:
                return None, False
            session.expunge(record)
            return record, False

    def complete_feedback(
        self,
        attempt_id: int,
        feedback_idempotency_key: str,
        revision: int,
        provider_call_token: str,
        transcript_fingerprint: str,
        proposal: dict[str, Any],
        proposal_status: str,
        failure_category: str = "",
    ) -> tuple[MockInterviewFeedbackProposal | None, bool]:
        _validate_key(feedback_idempotency_key, "feedback_idempotency_key")
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                raise LookupError("mock interview attempt not found")
            existing = session.scalar(
                select(MockInterviewFeedbackProposal).where(
                    MockInterviewFeedbackProposal.attempt_id == attempt_id,
                    MockInterviewFeedbackProposal.idempotency_key == feedback_idempotency_key,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing, False
            if (
                attempt.generation_revision != revision
                or attempt.provider_call_token != provider_call_token
                or attempt.transcript_fingerprint != transcript_fingerprint
            ):
                return None, False
            try:
                self._assert_attempt_sources(session, attempt)
            except MockInterviewSourceChanged:
                attempt.attempt_status = "source_conflict"
                attempt.failure_category = "source_conflict"
                attempt.provider_lease_until = None
                session.commit()
                raise
            proposal_json = canonical_json(proposal)
            record = MockInterviewFeedbackProposal(
                attempt_id=attempt_id,
                idempotency_key=feedback_idempotency_key,
                input_snapshot_json=attempt.input_snapshot_json,
                source_fingerprint=attempt.source_fingerprint,
                transcript_fingerprint=transcript_fingerprint,
                proposal_json=proposal_json,
                proposal_hash=sha256_text(proposal_json),
                proposal_status=proposal_status,
                failure_category=failure_category,
            )
            session.add(record)
            attempt.attempt_status = "feedback_ready"
            attempt.provider_lease_until = None
            attempt.completed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record, True

    def mark_contract_failure(
        self,
        attempt_id: int,
        revision: int,
        provider_call_token: str,
        category: str,
        status: str = "contract_failed",
    ) -> MockInterviewAttempt | None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                return None
            if attempt.generation_revision != revision or attempt.provider_call_token != provider_call_token:
                return None
            try:
                self._assert_attempt_sources(session, attempt)
            except MockInterviewSourceChanged:
                attempt.attempt_status = "source_conflict"
                attempt.failure_category = "source_conflict"
                attempt.provider_lease_until = None
                session.commit()
                raise
            attempt.attempt_status = status
            attempt.failure_category = category
            attempt.provider_lease_until = None
            session.commit()
            session.refresh(attempt)
            return attempt

    def feedback_context(
        self, attempt_id: int, application_id: int, event_id: int
    ) -> tuple[MockInterviewAttempt, list[MockInterviewTurn]]:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.application_id != application_id
                or attempt.event_id != event_id
            ):
                raise LookupError("mock interview attempt not found")
            application = session.get(Application, application_id)
            event = session.get(ApplicationEvent, event_id)
            if application is None or application.deleted_at is not None:
                raise LookupError("application not found")
            if (
                event is None
                or event.application_id != application_id
                or event.event_type != "interview"
                or event.scheduled_at is None
            ):
                raise LookupError("event not found")
            self._assert_attempt_sources(session, attempt)
            turns = list(session.scalars(
                select(MockInterviewTurn)
                .where(MockInterviewTurn.attempt_id == attempt_id)
                .order_by(MockInterviewTurn.turn_no.asc())
            ).all())
            session.expunge(attempt)
            for turn in turns:
                session.expunge(turn)
            return attempt, turns

    def discard_attempt(self, application_id: int, event_id: int, attempt_id: int) -> None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            application = session.get(Application, application_id)
            event = session.get(ApplicationEvent, event_id)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if application is None or application.deleted_at is not None:
                raise LookupError("application not found")
            if event is None or event.application_id != application_id or event.event_type != "interview":
                raise LookupError("event not found")
            if attempt is None:
                session.commit()
                return
            if attempt.application_id != application_id or attempt.event_id != event_id:
                raise LookupError("mock interview attempt not found")
            draft_exists = session.scalar(
                select(MockInterviewReviewDraft.id).where(
                    MockInterviewReviewDraft.attempt_id == attempt_id
                )
            )
            voice_snapshot_exists = session.scalar(
                select(VoiceCoachingSnapshot.id).where(
                    VoiceCoachingSnapshot.attempt_id == attempt_id
                )
            )
            if (
                draft_exists is not None
                or voice_snapshot_exists is not None
                or attempt.attempt_status == "confirmed"
            ):
                raise MockInterviewAttemptConfirmed("mock_interview_attempt_confirmed")
            session.execute(
                delete(MockInterviewFeedbackProposal).where(
                    MockInterviewFeedbackProposal.attempt_id == attempt_id
                )
            )
            session.execute(
                delete(MockInterviewTurn).where(MockInterviewTurn.attempt_id == attempt_id)
            )
            session.delete(attempt)
            session.commit()

    def question_context(
        self, attempt_id: int, application_id: int, event_id: int
    ) -> tuple[MockInterviewAttempt, list[MockInterviewTurn]]:
        return self.feedback_context(attempt_id, application_id, event_id)

    def quick_feedback_context(
        self, attempt_id: int, practice_case_id: int
    ) -> tuple[MockInterviewAttempt, list[MockInterviewTurn]]:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.context_kind != "quick_practice"
                or attempt.practice_case_id != practice_case_id
            ):
                raise LookupError("mock interview attempt not found")
            self._assert_attempt_sources(session, attempt)
            turns = list(
                session.scalars(
                    select(MockInterviewTurn)
                    .where(MockInterviewTurn.attempt_id == attempt_id)
                    .order_by(MockInterviewTurn.turn_no.asc())
                ).all()
            )
            session.expunge(attempt)
            for turn in turns:
                session.expunge(turn)
            return attempt, turns

    def attempt_context(
        self, attempt_id: int, application_id: int, event_id: int
    ) -> MockInterviewAttempt:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.application_id != application_id
                or attempt.event_id != event_id
            ):
                raise LookupError("mock interview attempt not found")
            application = session.get(Application, application_id)
            event = session.get(ApplicationEvent, event_id)
            if application is None or application.deleted_at is not None:
                raise LookupError("application not found")
            if (
                event is None
                or event.application_id != application_id
                or event.event_type != "interview"
                or event.scheduled_at is None
            ):
                raise LookupError("event not found")
            self._assert_attempt_sources(session, attempt)
            session.expunge(attempt)
            return attempt

    def quick_attempt_context(self, attempt_id: int, practice_case_id: int) -> MockInterviewAttempt:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.context_kind != "quick_practice"
                or attempt.practice_case_id != practice_case_id
            ):
                raise LookupError("mock interview attempt not found")
            self._assert_attempt_sources(session, attempt)
            session.expunge(attempt)
            return attempt

    def get_attempt(self, attempt_id: int) -> MockInterviewAttempt | None:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is not None:
                session.expunge(attempt)
            return attempt

    def get_turn(self, attempt_id: int, turn_no: int) -> MockInterviewTurn | None:
        with self._session_factory() as session:
            turn = session.scalar(
                select(MockInterviewTurn).where(
                    MockInterviewTurn.attempt_id == attempt_id,
                    MockInterviewTurn.turn_no == turn_no,
                )
            )
            if turn is not None:
                session.expunge(turn)
            return turn

    def validate_event_context(self, application_id: int, event_id: int) -> None:
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            event = session.get(ApplicationEvent, event_id)
            if application is None or application.deleted_at is not None:
                raise LookupError("application not found")
            if (
                event is None
                or event.application_id != application_id
                or event.event_type != "interview"
                or event.scheduled_at is None
            ):
                raise LookupError("event not found")

    def discard_quick_attempt(self, practice_case_id: int, attempt_id: int) -> None:
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if (
                attempt is None
                or attempt.context_kind != "quick_practice"
                or attempt.practice_case_id != practice_case_id
            ):
                session.commit()
                return
            draft_exists = session.scalar(
                select(MockInterviewReviewDraft.id).where(MockInterviewReviewDraft.attempt_id == attempt_id)
            )
            voice_snapshot_exists = session.scalar(
                select(VoiceCoachingSnapshot.id).where(VoiceCoachingSnapshot.attempt_id == attempt_id)
            )
            if draft_exists is not None or voice_snapshot_exists is not None or attempt.attempt_status == "confirmed":
                raise MockInterviewAttemptConfirmed("mock_interview_attempt_confirmed")
            session.execute(delete(MockInterviewFeedbackProposal).where(MockInterviewFeedbackProposal.attempt_id == attempt_id))
            session.execute(delete(MockInterviewTurn).where(MockInterviewTurn.attempt_id == attempt_id))
            session.delete(attempt)
            session.commit()

    @staticmethod
    def _assert_attempt_sources(session: Session, attempt: MockInterviewAttempt) -> None:
        snapshot = json.loads(attempt.input_snapshot_json)
        if attempt.context_kind == "quick_practice":
            case = session.get(InterviewPracticeCase, attempt.practice_case_id)
            if case is None:
                raise MockInterviewSourceChanged("mock_interview_source_conflict")
            try:
                frozen_resume = _json_object(case.resume_content_snapshot_json)
            except ValueError as exc:
                raise MockInterviewSourceChanged("mock_interview_source_conflict") from exc
            expected = {
                "position_name": case.position_name_snapshot,
                "jd": {
                    "text": case.jd_text_snapshot,
                    "content_sha256": case.jd_fingerprint_sha256,
                },
                "resume": {"title": snapshot.get("resume", {}).get("title", ""), "content_json": frozen_resume},
            }
            stored_resume = snapshot.get("resume") if isinstance(snapshot.get("resume"), dict) else {}
            if (
                snapshot.get("context_kind") != "quick_practice"
                or snapshot.get("practice_case_id") != case.id
                or snapshot.get("position_name") != expected["position_name"]
                or snapshot.get("jd") != expected["jd"]
                or stored_resume.get("content_json") != frozen_resume
            ):
                raise MockInterviewSourceChanged("mock_interview_source_conflict")
            return
        jd_snapshot = snapshot.get("jd") if isinstance(snapshot.get("jd"), dict) else {}
        jd_text = str(jd_snapshot.get("text", ""))
        recorded_version_id = snapshot.get("jd_version_id")
        if attempt.application_id is None or attempt.event_id is None:
            raise MockInterviewSourceChanged("mock_interview_source_conflict")
        application_id = attempt.application_id
        event_id = attempt.event_id
        try:
            current = MockInterviewRepository._build_input_snapshot(
                session,
                application_id,
                event_id,
                attempt.resume_id,
                jd_text,
                None,
                jd_version_id=recorded_version_id if isinstance(recorded_version_id, int) else None,
            )
        except (LookupError, ValueError) as exc:
            raise MockInterviewSourceChanged("mock_interview_source_conflict") from exc
        if (
            current.get("application") != snapshot.get("application")
            or current.get("event") != snapshot.get("event")
            or current.get("resume") != snapshot.get("resume")
            or current.get("jd") != snapshot.get("jd")
        ):
            raise MockInterviewSourceChanged("mock_interview_source_conflict")
        _assert_preparation_snapshot(
            session,
            application_id,
            event_id,
            snapshot.get("selected_preparation", []),
        )

    @staticmethod
    def _build_quick_input_snapshot(case: InterviewPracticeCase) -> dict[str, Any]:
        return {
            "schema_version": "mock-interview-input-v1",
            "context_kind": "quick_practice",
            "practice_case_id": case.id,
            "position_name": case.position_name_snapshot,
            "jd": {
                "text": case.jd_text_snapshot,
                "content_sha256": case.jd_fingerprint_sha256,
            },
            "resume": {
                "id": case.resume_id,
                "content_json": _json_object(case.resume_content_snapshot_json),
                "content_sha256": case.resume_fingerprint_sha256,
            },
            "selected_preparation": [],
            "turns": [],
        }

    def create_or_replay_feedback(
        self,
        attempt_id: int,
        feedback_idempotency_key: str,
        proposal: dict[str, Any],
        proposal_status: str,
        failure_category: str = "",
    ) -> tuple[MockInterviewFeedbackProposal, bool]:
        if not feedback_idempotency_key:
            raise ValueError("feedback_idempotency_key is required")
        with self._session_factory() as session:
            self._begin_immediate(session)
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                raise LookupError("mock interview attempt not found")
            existing = session.scalar(
                select(MockInterviewFeedbackProposal).where(
                    MockInterviewFeedbackProposal.attempt_id == attempt_id,
                    MockInterviewFeedbackProposal.idempotency_key == feedback_idempotency_key,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing, False
            proposal_json = canonical_json(proposal)
            record = MockInterviewFeedbackProposal(
                attempt_id=attempt_id,
                idempotency_key=feedback_idempotency_key,
                input_snapshot_json=attempt.input_snapshot_json,
                source_fingerprint=attempt.source_fingerprint,
                transcript_fingerprint=attempt.transcript_fingerprint,
                proposal_json=proposal_json,
                proposal_hash=sha256_text(proposal_json),
                proposal_status=proposal_status,
                failure_category=failure_category,
            )
            session.add(record)
            attempt.attempt_status = "feedback_ready"
            attempt.completed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record, True

    def list_feedback_history(
        self, application_id: int, event_id: int
    ) -> list[MockInterviewFeedbackProposal]:
        with self._session_factory() as session:
            application = session.get(Application, application_id)
            if application is None or application.deleted_at is not None:
                raise LookupError("application not found")
            rows = list(session.scalars(
                select(MockInterviewFeedbackProposal)
                .join(
                    MockInterviewAttempt,
                    MockInterviewAttempt.id == MockInterviewFeedbackProposal.attempt_id,
                )
                .where(
                    MockInterviewAttempt.application_id == application_id,
                    MockInterviewAttempt.event_id == event_id,
                )
                .order_by(MockInterviewFeedbackProposal.created_at.desc())
            ).all())
            for row in rows:
                session.expunge(row)
            return rows

    def list_quick_feedback_history(
        self, practice_case_id: int
    ) -> list[MockInterviewFeedbackProposal]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(MockInterviewFeedbackProposal)
                    .join(MockInterviewAttempt, MockInterviewAttempt.id == MockInterviewFeedbackProposal.attempt_id)
                    .where(
                        MockInterviewAttempt.context_kind == "quick_practice",
                        MockInterviewAttempt.practice_case_id == practice_case_id,
                    )
                    .order_by(MockInterviewFeedbackProposal.created_at.desc())
                ).all()
            )
            for row in rows:
                session.expunge(row)
            return rows

    def history_details(
        self, proposal_id: int
    ) -> tuple[list[MockInterviewTurn], MockInterviewReviewDraft | None]:
        with self._session_factory() as session:
            proposal = session.get(MockInterviewFeedbackProposal, proposal_id)
            if proposal is None:
                return [], None
            turns = list(session.scalars(
                select(MockInterviewTurn)
                .where(MockInterviewTurn.attempt_id == proposal.attempt_id)
                .order_by(MockInterviewTurn.turn_no.asc())
            ).all())
            draft = session.scalar(
                select(MockInterviewReviewDraft).where(
                    MockInterviewReviewDraft.proposal_id == proposal_id
                )
            )
            for item in turns:
                session.expunge(item)
            if draft is not None:
                session.expunge(draft)
            return turns, draft

    def source_status(self, attempt_id: int) -> str:
        with self._session_factory() as session:
            attempt = session.get(MockInterviewAttempt, attempt_id)
            if attempt is None:
                return "source_changed"
            if attempt.context_kind == "quick_practice":
                try:
                    self._assert_attempt_sources(session, attempt)
                except MockInterviewSourceChanged:
                    return "source_changed"
                return "current"
            try:
                self._assert_attempt_sources(session, attempt)
            except MockInterviewSourceChanged:
                return "source_changed"
            snapshot = json.loads(attempt.input_snapshot_json)
            recorded_version_id = snapshot.get("jd_version_id")
            latest_version = session.scalar(
                select(ApplicationJDVersion)
                .where(ApplicationJDVersion.application_id == attempt.application_id)
                .order_by(ApplicationJDVersion.version_number.desc())
            )
            if not isinstance(recorded_version_id, int):
                return "source_changed" if latest_version is not None else "unknown"
            if isinstance(recorded_version_id, int):
                if latest_version is None or latest_version.id != recorded_version_id:
                    return "source_changed"
            return "current"

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _build_input_snapshot(
        session: Session,
        application_id: int,
        event_id: int,
        resume_id: int,
        jd_text: str,
        preparation_proposal_id: int | None,
        preparation_selection: dict[str, Any] | None = None,
        jd_version_id: int | None = None,
    ) -> dict[str, Any]:
        application = session.get(Application, application_id)
        event = session.get(ApplicationEvent, event_id)
        resume = session.get(Resume, resume_id)
        if application is None or application.deleted_at is not None:
            raise LookupError("application not found")
        if (
            event is None
            or event.application_id != application_id
            or event.event_type != "interview"
            or event.scheduled_at is None
        ):
            raise ValueError("event is not a scheduled interview")
        if resume is None or resume.deleted_at is not None:
            raise LookupError("resume not found")
        jd_snapshot: dict[str, Any] = {"text": jd_text}
        if jd_version_id is not None:
            version = session.get(ApplicationJDVersion, jd_version_id)
            if version is None or version.application_id != application_id:
                raise ValueError("JD version is unavailable")
            jd_snapshot.update(
                {
                    "version_id": version.id,
                    "version_number": version.version_number,
                    "content_sha256": version.content_sha256,
                }
            )
        snapshot: dict[str, Any] = {
            "schema_version": "mock-interview-input-v1",
            "application": {
                "company_name": application.company_name,
                "position_name": application.position_name,
            },
            "event": {
                "event_type": event.event_type,
                "subtype": event.subtype,
                "round": event.round,
                "scheduled_at": event.scheduled_at.isoformat(),
                "duration_minutes": event.duration_minutes,
                "status": event.status,
            },
            "resume": {
                "title": resume.title,
                "content_json": _json_object(resume.content_json),
            },
            "jd": jd_snapshot,
            "selected_preparation": _selected_preparation_snapshot(
                session, application_id, event_id, preparation_proposal_id, preparation_selection
            ),
            "turns": [],
        }
        if jd_version_id is not None:
            snapshot["jd_version_id"] = jd_version_id
        return snapshot


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("resume content_json is invalid") from exc
    if not isinstance(parsed, dict):
        raise ValueError("resume content_json must be an object")
    return parsed


def _transcript_fingerprint(turns: list[MockInterviewTurn]) -> str:
    payload = [
        {"turn_no": turn.turn_no, "question": turn.question_text, "answer": turn.answer_text}
        for turn in turns
    ]
    return sha256_text(canonical_json(payload))


def _question_source_snapshot(attempt: MockInterviewAttempt) -> dict[str, Any]:
    return _provider_mock_interview_snapshot(attempt)


def _provider_mock_interview_snapshot(attempt: MockInterviewAttempt) -> dict[str, Any]:
    snapshot = json.loads(attempt.input_snapshot_json)
    return {
        "jd": snapshot.get("jd", {}),
        "resume": snapshot.get("resume", {}),
        "selected_preparation": [
            {
                "items": [
                    {
                        "text": entry.get("text", ""),
                        "evidence_refs": entry.get("evidence_refs", []),
                    }
                    for entry in item.get("items", [])
                    if isinstance(entry, dict)
                ],
                "evidence": item.get("evidence", []),
            }
            for item in snapshot.get("selected_preparation", [])
            if isinstance(item, dict)
        ],
    }


def provider_mock_interview_snapshot(attempt: MockInterviewAttempt) -> dict[str, Any]:
    """Return only source text needed by the model, never DB identifiers or full proposals."""
    return _provider_mock_interview_snapshot(attempt)


def _token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def _lease_until() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_key(value: str, field: str) -> None:
    if not isinstance(value, str) or _ASCII_KEY.fullmatch(value) is None:
        raise ValueError(f"{field} must be an ASCII idempotency key")


def _selected_preparation_snapshot(
    session: Session,
    application_id: int,
    event_id: int,
    proposal_id: int | None,
    selection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if proposal_id is None:
        if selection is not None:
            raise ValueError("preparation_selection requires preparation_proposal_id")
        return []
    if selection is None:
        raise ValueError("preparation_selection is required when using preparation_proposal_id")
    if selection.get("proposal_id") != proposal_id:
        raise ValueError("preparation_selection proposal_id must match preparation_proposal_id")
    item_ids = selection.get("item_ids")
    if (
        not isinstance(item_ids, list)
        or not item_ids
        or len(item_ids) > 8
        or any(not isinstance(item_id, str) or not item_id.strip() for item_id in item_ids)
        or len(set(item_ids)) != len(item_ids)
    ):
        raise ValueError("preparation_selection item_ids must be a non-empty unique list")
    proposal = session.get(InterviewPreparationProposal, proposal_id)
    if (
        proposal is None
        or proposal.application_id != application_id
        or proposal.application_event_id != event_id
        or proposal.attempt_status != "ready"
        or proposal.proposal_status not in {"normal", "safe_empty"}
        or not proposal.proposal_json
    ):
        raise ValueError("preparation proposal is not available for this interview event")
    try:
        value = json.loads(proposal.proposal_json)
    except json.JSONDecodeError as exc:
        raise ValueError("preparation proposal is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("preparation proposal is invalid")
    if not _preparation_sources_current(session, proposal):
        raise ValueError("preparation proposal source changed")
    items_by_id = {
        item["id"]: item
        for field in ("preparation_directions", "story_prompts", "review_points", "interviewer_questions", "items_to_clarify")
        for item in value.get(field, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if any(item_id not in items_by_id for item_id in item_ids):
        raise ValueError("preparation_selection contains an unknown item")
    selected_items = [items_by_id[item_id] for item_id in item_ids]
    evidence: list[dict[str, str]] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    for item in selected_items:
        for ref in item.get("evidence_refs", []):
            key = (ref["source"], ref["path"], ref["excerpt"])
            if key not in seen_evidence:
                seen_evidence.add(key)
                evidence.append({"source": key[0], "path": key[1], "excerpt": key[2]})
    return [
        {
            "source_fingerprint": proposal.source_fingerprint,
            "proposal_hash": proposal.proposal_hash,
            "proposal_id": proposal.id,
            "items": selected_items,
            "evidence": evidence,
        }
    ]


def _preparation_selection_matches(
    snapshot: dict[str, Any], proposal_id: int | None, selection: dict[str, Any] | None
) -> bool:
    selected = snapshot.get("selected_preparation", [])
    if proposal_id is None:
        return selection is None and selected == []
    if not isinstance(selection, dict) or not isinstance(selected, list) or len(selected) != 1:
        return False
    item = selected[0]
    if not isinstance(item, dict) or item.get("proposal_id") != proposal_id:
        return False
    expected_ids = selection.get("item_ids")
    actual_ids = [
        item_id.get("id")
        for item_id in item.get("items", [])
        if isinstance(item_id, dict) and isinstance(item_id.get("id"), str)
    ]
    return isinstance(expected_ids, list) and expected_ids == actual_ids


def _preparation_sources_current(
    session: Session, proposal: InterviewPreparationProposal
) -> bool:
    if not source_is_current_for_mock_interview(session, proposal):
        return False
    try:
        snapshot = json.loads(proposal.input_snapshot_json)
        event_snapshot = snapshot["event"]
        resume_snapshot = snapshot["resume"]
        event = session.get(ApplicationEvent, proposal.application_event_id)
        resume = session.get(Resume, proposal.resume_id)
        if event is None or resume is None or resume.deleted_at is not None:
            return False
        if (
            event.application_id != proposal.application_id
            or event.event_type != event_snapshot.get("event_type")
            or event.subtype != event_snapshot.get("subtype")
            or event.round != event_snapshot.get("round")
            or (event.scheduled_at.isoformat() if event.scheduled_at else None)
            != event_snapshot.get("scheduled_at")
            or event.duration_minutes != event_snapshot.get("duration_minutes")
            or event.status != event_snapshot.get("status")
        ):
            return False
        return canonical_json(_json_object(resume.content_json)) == canonical_json(
            resume_snapshot["content_json"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _assert_preparation_snapshot(
    session: Session,
    application_id: int,
    event_id: int,
    selected: Any,
) -> None:
    if not isinstance(selected, list) or not selected:
        return
    if len(selected) != 1 or not isinstance(selected[0], dict):
        raise MockInterviewSourceChanged("mock_interview_source_conflict")
    item = selected[0]
    fingerprint = item.get("source_fingerprint")
    proposal_hash = item.get("proposal_hash")
    proposal = session.scalar(
        select(InterviewPreparationProposal)
        .where(InterviewPreparationProposal.application_id == application_id)
        .where(InterviewPreparationProposal.application_event_id == event_id)
        .where(InterviewPreparationProposal.source_fingerprint == fingerprint)
        .where(InterviewPreparationProposal.proposal_hash == proposal_hash)
        .where(InterviewPreparationProposal.attempt_status == "ready")
        .where(InterviewPreparationProposal.proposal_status.in_(["normal", "safe_empty"]))
    )
    if proposal is None or not _preparation_sources_current(session, proposal):
        raise MockInterviewSourceChanged("mock_interview_source_conflict")
    try:
        current = _selected_preparation_snapshot(
            session,
            application_id,
            event_id,
            proposal.id,
            {"proposal_id": proposal.id, "item_ids": [entry["id"] for entry in item["items"]]},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MockInterviewSourceChanged("mock_interview_source_conflict") from exc
    if canonical_json(current[0]["items"]) != canonical_json(item.get("items")):
        raise MockInterviewSourceChanged("mock_interview_source_conflict")
