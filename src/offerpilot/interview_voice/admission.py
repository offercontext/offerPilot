from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from offerpilot.models import (
    InterviewVoiceSession, MockInterviewAnswerAcceptance, MockInterviewAttempt, MockInterviewTurn,
)

CONSENT_REVISION = "interview-realtime-v1"
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$"


class VoiceAdmissionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


class AnswerAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_source: Literal["text", "voice"] = "text"
    acceptance_method: Literal["manual", "session_auto"] = "manual"
    voice_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    voice_session_epoch: int | None = Field(default=None, gt=0)
    voice_consent_revision: str | None = Field(default=None, max_length=64)
    voice_session_token: str | None = Field(default=None, min_length=32, max_length=128, repr=False)

    @model_validator(mode="after")
    def scope_auto_acceptance(self) -> AnswerAcceptance:
        fields = (
            self.voice_session_id, self.voice_session_epoch,
            self.voice_consent_revision, self.voice_session_token,
        )
        if self.acceptance_method == "session_auto":
            if self.input_source != "voice" or not all(value is not None for value in fields):
                raise ValueError("自动接受必须关联本场语音授权。")
            if self.voice_consent_revision != CONSENT_REVISION:
                raise ValueError("语音授权版本不匹配，请重新开启。")
        elif any(value is not None for value in fields):
            raise ValueError("人工提交不能携带自动接受授权。")
        return self


class InterviewAnswerIn(AnswerAcceptance):
    turn_no: int = Field(gt=0, le=5)
    answer_text: str = Field(min_length=1, max_length=30_000)
    turn_idempotency_key: str = Field(pattern=KEY_PATTERN)

    @model_validator(mode="after")
    def nonblank_answer(self) -> InterviewAnswerIn:
        if not self.answer_text.strip():
            raise ValueError("回答不能为空。")
        return self

    def acceptance(self) -> AnswerAcceptance:
        return AnswerAcceptance.model_validate(self.model_dump(include=set(AnswerAcceptance.model_fields)))


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_active(row: InterviewVoiceSession, now: datetime) -> bool:
    return row.state == "active" and utc(row.lease_until) > now and utc(row.expires_at) > now


def active_session(session: Session, attempt_id: int, now: datetime) -> InterviewVoiceSession | None:
    rows = session.scalars(select(InterviewVoiceSession).where(
        InterviewVoiceSession.attempt_id == attempt_id,
        InterviewVoiceSession.state == "active",
    )).all()
    return next((row for row in rows if is_active(row, now)), None)


def authenticate_session(
    session: Session, session_id: str, token: str, *, require_active: bool = True,
    now: datetime | None = None,
) -> InterviewVoiceSession:
    row = session.get(InterviewVoiceSession, session_id)
    if row is None or not hmac.compare_digest(row.token_sha256, token_hash(token)):
        raise VoiceAdmissionError("voice_session_invalid", "语音会话凭据无效。", 403)
    if require_active and not is_active(row, now or datetime.now(timezone.utc)):
        raise VoiceAdmissionError("voice_session_inactive", "语音授权已暂停、过期或被其他页面接管。")
    return row


def check_answer_admission(
    session: Session, attempt: MockInterviewAttempt, turn: MockInterviewTurn,
    acceptance: AnswerAcceptance, *, replay: bool,
) -> None:
    """Called inside the answer transaction, including replay after revocation.

    The lease is checked only for a new write. A byte-identical accepted answer may
    still be read back after a disconnect; replay never renews a revoked grant.
    """
    now = datetime.now(timezone.utc)
    if acceptance.acceptance_method == "session_auto":
        row = authenticate_session(
            session, acceptance.voice_session_id or "", acceptance.voice_session_token or "",
            require_active=not replay, now=now,
        )
        if (row.attempt_id != attempt.id or row.epoch != acceptance.voice_session_epoch
                or row.consent_revision != acceptance.voice_consent_revision):
            raise VoiceAdmissionError("voice_session_mismatch", "语音授权与当前练习不匹配。")
    elif not replay and active_session(session, attempt.id, now):
        raise VoiceAdmissionError("voice_session_owned", "请先停止实时对话，再人工提交回答。")

    if replay:
        # Pre-feature history has no measured provenance; do not invent it on replay.
        if turn.voice_acceptance is None and acceptance.acceptance_method == "manual":
            return
        fields = ("input_source", "acceptance_method", "voice_session_id",
                  "voice_session_epoch", "voice_consent_revision")
        if turn.voice_acceptance is None or any(
            getattr(turn.voice_acceptance, name) != getattr(acceptance, name) for name in fields
        ):
            raise VoiceAdmissionError("voice_acceptance_conflict", "原回答的接受方式不能修改。")
        return

    if acceptance.acceptance_method == "session_auto":
        latest = session.scalar(select(MockInterviewTurn).where(
            MockInterviewTurn.attempt_id == attempt.id,
        ).order_by(MockInterviewTurn.turn_no.desc()))
        if latest is None or latest.id != turn.id or attempt.attempt_status != "awaiting_answer":
            raise VoiceAdmissionError("voice_turn_changed", "当前题目已经变化，请先恢复会话状态。")


def set_answer_acceptance(turn: MockInterviewTurn, acceptance: AnswerAcceptance) -> None:
    turn.voice_acceptance = MockInterviewAnswerAcceptance(
        input_source=acceptance.input_source,
        acceptance_method=acceptance.acceptance_method,
        voice_session_id=acceptance.voice_session_id,
        voice_session_epoch=acceptance.voice_session_epoch,
        voice_consent_revision=acceptance.voice_consent_revision,
    )
