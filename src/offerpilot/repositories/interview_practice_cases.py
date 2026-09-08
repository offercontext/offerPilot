from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import InterviewPracticeCase, Resume
from offerpilot.repositories.json_contract import canonical_json, sha256_text


class InterviewPracticeCaseValidationError(ValueError):
    pass


class InterviewPracticeCaseIdempotencyConflict(ValueError):
    pass


def validate_context(
    *,
    context_kind: str,
    application_id: int | None,
    event_id: int | None,
    practice_case_id: int | None,
) -> None:
    if context_kind == "application_event":
        valid = application_id is not None and event_id is not None and practice_case_id is None
    elif context_kind == "quick_practice":
        valid = application_id is None and event_id is None and practice_case_id is not None
    else:
        valid = False
    if not valid:
        raise InterviewPracticeCaseValidationError("mock interview context is invalid")


class InterviewPracticeCaseRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def create_or_replay(
        self,
        *,
        idempotency_key: str,
        position_name: str,
        jd_text: str,
        resume_id: int,
    ) -> tuple[InterviewPracticeCase, bool]:
        position_name = _validate_text("position_name", position_name, maximum=200)
        if not isinstance(jd_text, str) or not jd_text.strip() or len(jd_text) > 100_000:
            raise InterviewPracticeCaseValidationError("jd_text must contain 1-100000 characters")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise InterviewPracticeCaseValidationError("idempotency_key must contain 1-128 characters")
        if not isinstance(resume_id, int) or resume_id < 1:
            raise InterviewPracticeCaseValidationError("resume_id must be a positive integer")

        with self._session_factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            resume = session.scalar(
                select(Resume).where(Resume.id == resume_id, Resume.deleted_at.is_(None))
            )
            if resume is None:
                raise InterviewPracticeCaseValidationError("resume is not available")
            resume_snapshot = _resume_snapshot(resume.content_json, resume.parsed_data)
            request_fingerprint = sha256_text(
                canonical_json(
                    {
                        "position_name": position_name,
                        "jd_text": jd_text,
                        "resume_id": resume_id,
                        "resume_snapshot": resume_snapshot,
                    }
                )
            )
            existing = session.scalar(
                select(InterviewPracticeCase).where(
                    InterviewPracticeCase.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.request_fingerprint_sha256 != request_fingerprint:
                    raise InterviewPracticeCaseIdempotencyConflict(
                        "interview practice case input changed"
                    )
                session.expunge(existing)
                return existing, False

            case = InterviewPracticeCase(
                idempotency_key=idempotency_key,
                request_fingerprint_sha256=request_fingerprint,
                position_name_snapshot=position_name,
                jd_text_snapshot=jd_text,
                jd_fingerprint_sha256=sha256_text(jd_text),
                resume_id=resume_id,
                resume_content_snapshot_json=resume_snapshot,
                resume_fingerprint_sha256=sha256_text(resume_snapshot),
                status="active",
            )
            session.add(case)
            session.commit()
            session.refresh(case)
            session.expunge(case)
            return case, True

    def get(self, case_id: int) -> InterviewPracticeCase | None:
        with self._session_factory() as session:
            case = session.get(InterviewPracticeCase, case_id)
            if case is not None:
                session.expunge(case)
            return case

    def list(self, *, limit: int = 50, before_id: int | None = None) -> list[InterviewPracticeCase]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise InterviewPracticeCaseValidationError("limit must be between 1 and 200")
        with self._session_factory() as session:
            statement = select(InterviewPracticeCase)
            if before_id is not None:
                if not isinstance(before_id, int) or before_id < 1:
                    raise InterviewPracticeCaseValidationError("before_id must be positive")
                statement = statement.where(InterviewPracticeCase.id < before_id)
            rows = list(
                session.scalars(
                    statement.order_by(InterviewPracticeCase.id.desc()).limit(limit)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def archive(self, case_id: int) -> InterviewPracticeCase:
        with self._session_factory() as session:
            case = session.get(InterviewPracticeCase, case_id)
            if case is None:
                raise InterviewPracticeCaseValidationError("practice case not found")
            if case.status != "archived":
                case.status = "archived"
                case.archived_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(case)
            session.expunge(case)
            return case


def _validate_text(name: str, value: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise InterviewPracticeCaseValidationError(f"{name} must contain 1-{maximum} characters")
    return value.strip()


def _resume_snapshot(content_json: str, parsed_data: str) -> str:
    try:
        parsed = json.loads(content_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = {"parsed_data": parsed_data or ""}
    if not isinstance(parsed, dict):
        parsed = {"parsed_data": parsed_data or ""}
    return canonical_json(parsed)
