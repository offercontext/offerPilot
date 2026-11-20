from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.interview_voice.admission import (
    CONSENT_REVISION, KEY_PATTERN, VoiceAdmissionError, active_session,
    authenticate_session, is_active, token_hash, utc,
)
from offerpilot.models import InterviewVoiceSession, MockInterviewAttempt, MockInterviewTurn
from offerpilot.repositories.mock_interviews import MockInterviewRepository, MockInterviewSourceChanged

LEASE_SECONDS = 45
SESSION_SECONDS = 3600


class VoiceSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    attempt_id: int = Field(gt=0)
    turn_no: int = Field(gt=0, le=5)
    context_kind: Literal["application_event", "quick_practice"]
    application_id: int | None = Field(default=None, gt=0)
    event_id: int | None = Field(default=None, gt=0)
    practice_case_id: int | None = Field(default=None, gt=0)
    request_key: str = Field(pattern=KEY_PATTERN)
    consent: bool
    consent_revision: Literal["interview-realtime-v1"]
    takeover: bool = False

    @model_validator(mode="after")
    def context_matches(self) -> VoiceSessionCreate:
        if self.consent is not True:
            raise ValueError("需要主动授权本场自动记录。")
        real = self.context_kind == "application_event"
        if (real and (not self.application_id or not self.event_id or self.practice_case_id)) or (
            not real and (not self.practice_case_id or self.application_id or self.event_id)
        ):
            raise ValueError("请选择对应的面试上下文。")
        return self


class VoiceSessionAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=32, max_length=128, repr=False)


class VoiceRuntimeInspect(VoiceSessionAccess):
    session_id: str = Field(min_length=1, max_length=128)


class VoiceOffer(VoiceSessionAccess):
    sdp: str = Field(min_length=1, max_length=100_000)
    type: Literal["offer"]


class InterviewVoiceRepository:
    def __init__(self, sessions: sessionmaker[Session], secret: str):
        self.sessions = sessions
        self.secret = secret

    def _token(self, row: InterviewVoiceSession) -> str:
        identity = f"interview-voice:{row.id}:{row.attempt_id}:{row.epoch}:{row.consent_revision}"
        return hmac.new(self.secret.encode(), identity.encode(), hashlib.sha256).hexdigest()

    def create(self, request: VoiceSessionCreate) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            attempt = db.get(MockInterviewAttempt, request.attempt_id)
            if attempt is None or (
                attempt.context_kind, attempt.application_id, attempt.event_id, attempt.practice_case_id
            ) != (
                request.context_kind, request.application_id, request.event_id, request.practice_case_id
            ):
                raise VoiceAdmissionError("voice_context_mismatch", "练习上下文不匹配。", 404)
            MockInterviewRepository._assert_attempt_sources(db, attempt)
            replay = db.scalar(select(InterviewVoiceSession).where(
                InterviewVoiceSession.attempt_id == attempt.id,
                InterviewVoiceSession.request_key == request.request_key,
            ))
            if replay is not None:
                if replay.initial_turn_no != request.turn_no or not is_active(replay, now):
                    raise VoiceAdmissionError("voice_session_inactive", "原开启请求已失效，请重新开启。")
                return self._grant(replay)
            latest = db.scalar(select(MockInterviewTurn).where(
                MockInterviewTurn.attempt_id == attempt.id,
            ).order_by(MockInterviewTurn.turn_no.desc()))
            if (latest is None or latest.turn_no != request.turn_no
                    or latest.turn_status != "awaiting_answer" or latest.answer_text
                    or attempt.attempt_status != "awaiting_answer"):
                raise VoiceAdmissionError("voice_turn_changed", "请先恢复到当前待回答的问题。")
            active = active_session(db, attempt.id, now)
            if active and not request.takeover:
                raise VoiceAdmissionError("voice_session_owned", "其他页面正在实时对话，请明确接管后重试。")
            # BEGIN IMMEDIATE serializes ownership with both creation and answer admission.
            for old in db.scalars(select(InterviewVoiceSession).where(
                InterviewVoiceSession.attempt_id == attempt.id, InterviewVoiceSession.state == "active",
            )):
                old.state = "revoked"
            epoch = int(db.scalar(select(func.max(InterviewVoiceSession.epoch)).where(
                InterviewVoiceSession.attempt_id == attempt.id,
            )) or 0) + 1
            row = InterviewVoiceSession(
                id=secrets.token_urlsafe(24), attempt_id=attempt.id, request_key=request.request_key,
                epoch=epoch, consent_revision=CONSENT_REVISION, initial_turn_no=request.turn_no,
                state="active", created_at=now, expires_at=now + timedelta(seconds=SESSION_SECONDS),
                lease_until=now + timedelta(seconds=LEASE_SECONDS), token_sha256="",
            )
            row.token_sha256 = token_hash(self._token(row))
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._grant(row)

    def _grant(self, row: InterviewVoiceSession) -> dict[str, Any]:
        return {
            "session_id": row.id, "attempt_id": row.attempt_id, "epoch": row.epoch,
            "consent_revision": row.consent_revision, "token": self._token(row),
            "expires_at": utc(row.expires_at).isoformat(), "heartbeat_seconds": 10,
        }

    def heartbeat(self, session_id: str, token: str) -> None:
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = authenticate_session(db, session_id, token, now=now)
            attempt = db.get(MockInterviewAttempt, row.attempt_id)
            if attempt is None:
                raise VoiceAdmissionError("voice_attempt_missing", "练习已删除。", 404)
            try:
                MockInterviewRepository._assert_attempt_sources(db, attempt)
            except MockInterviewSourceChanged as exc:
                row.state = "revoked"
                db.commit()
                raise VoiceAdmissionError("voice_source_changed", "冻结来源已变化，请重新开始练习。") from exc
            row.lease_until = min(now + timedelta(seconds=LEASE_SECONDS), utc(row.expires_at))
            db.commit()

    def close(self, session_id: str, token: str) -> None:
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = authenticate_session(db, session_id, token, require_active=False)
            row.state = "revoked"
            db.commit()

    def snapshot(
        self, session_id: str, token: str, *, active: bool = False,
    ) -> tuple[dict[str, Any], list[MockInterviewTurn]]:
        with self.sessions() as db:
            row = authenticate_session(db, session_id, token, require_active=active)
            attempt = db.get(MockInterviewAttempt, row.attempt_id)
            if attempt is None:
                raise VoiceAdmissionError("voice_attempt_missing", "练习已删除。", 404)
            MockInterviewRepository._assert_attempt_sources(db, attempt)
            turns = list(db.scalars(select(MockInterviewTurn).where(
                MockInterviewTurn.attempt_id == attempt.id,
            ).order_by(MockInterviewTurn.turn_no)).all())
            for turn in turns:
                db.expunge(turn)
            return {
                "session_id": row.id, "epoch": row.epoch, "attempt_id": row.attempt_id,
                "active": is_active(row, datetime.now(timezone.utc)),
                "attempt_status": attempt.attempt_status,
            }, turns
