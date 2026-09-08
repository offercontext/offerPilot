from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.models import Question, QuestionReview


@dataclass
class QuestionCreate:
    question: str
    topic: str = ""
    category: str = ""
    difficulty: str = "medium"
    reference_answer: str = ""
    tags: list[str] | None = None
    source_type: str = "manual"
    status: str = "new"
    application_id: Optional[int] = None


class QuestionsRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def create(self, data: QuestionCreate) -> Question:
        question = _question_from_create(data)
        with self._session_factory() as session:
            session.add(question)
            session.commit()
            session.refresh(question)
            return question

    def bulk_create(self, items: list[QuestionCreate]) -> list[Question]:
        questions = [_question_from_create(item) for item in items]
        with self._session_factory() as session:
            session.add_all(questions)
            session.commit()
            for question in questions:
                session.refresh(question)
            return questions

    def list(
        self,
        topic: str = "",
        category: str = "",
        difficulty: str = "",
        status: str = "",
    ) -> list[Question]:
        statement = select(Question)
        if topic:
            statement = statement.where(Question.topic == topic)
        if category:
            statement = statement.where(Question.category == category)
        if difficulty:
            statement = statement.where(Question.difficulty == difficulty)
        if status:
            statement = statement.where(Question.status == status)
        statement = statement.order_by(Question.created_at.desc(), Question.id.desc())
        with self._session_factory() as session:
            return list(session.scalars(statement))

    def list_due(self, limit: int = 20) -> List[Question]:
        now = datetime.now(timezone.utc)
        statement = (
            select(Question)
            .where(or_(Question.next_review_at.is_(None), Question.next_review_at <= now))
            .order_by(Question.next_review_at.is_not(None), Question.next_review_at.asc(), Question.created_at.asc())
            .limit(limit if limit > 0 else 20)
        )
        with self._session_factory() as session:
            return list(session.scalars(statement))

    def get(self, question_id: int) -> Optional[Question]:
        with self._session_factory() as session:
            return session.get(Question, question_id)

    def update(self, question_id: int, data: QuestionCreate) -> Optional[Question]:
        with self._session_factory() as session:
            question = session.get(Question, question_id)
            if question is None:
                return None
            question.category = data.category
            question.difficulty = data.difficulty or "medium"
            question.question = data.question
            question.reference_answer = data.reference_answer
            question.tags = data.tags or []
            question.status = data.status or "new"
            question.question_hash = question_hash(data.question)
            session.commit()
            session.refresh(question)
            return question

    def delete(self, question_id: int) -> bool:
        with self._session_factory() as session:
            question = session.get(Question, question_id)
            if question is None:
                return False
            session.delete(question)
            session.commit()
            return True

    def add_review(self, question_id: int, rating: int, note: str = "") -> tuple[QuestionReview, Question] | None:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            question = session.get(Question, question_id)
            if question is None:
                return None
            review = QuestionReview(question_id=question_id, rating=rating, note=note)
            question.practice_count += 1
            question.status = "mastered" if rating >= 3 else "practicing"
            question.last_practiced_at = now
            question.next_review_at = now + _next_review_interval(rating)
            session.add(review)
            session.commit()
            session.refresh(review)
            session.refresh(question)
            return review, question

    def stats(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        with self._session_factory() as session:
            questions = list(session.scalars(select(Question)))
            today_reviews = session.scalar(
                select(func.count()).select_from(QuestionReview).where(QuestionReview.created_at >= start_of_day)
            )
        total = len(questions)
        return {
            "total": total,
            "new": sum(1 for question in questions if question.status == "new"),
            "practicing": sum(1 for question in questions if question.status == "practicing"),
            "mastered": sum(1 for question in questions if question.status == "mastered"),
            "due": sum(
                1
                for question in questions
                if question.next_review_at is None or _as_aware(question.next_review_at) <= now
            ),
            "today_reviews": int(today_reviews or 0),
            "streak_days": 1 if today_reviews else 0,
        }

    def hashes(self) -> set[str]:
        with self._session_factory() as session:
            return {value for value in session.scalars(select(Question.question_hash)) if value}


def _question_from_create(data: QuestionCreate) -> Question:
    question = Question(
        application_id=data.application_id,
        topic=data.topic,
        category=data.category,
        difficulty=data.difficulty or "medium",
        question=data.question,
        reference_answer=data.reference_answer,
        source_type=data.source_type or "manual",
        status=data.status or "new",
        question_hash=question_hash(data.question),
    )
    question.tags = data.tags or []
    return question


def question_hash(text: str) -> str:
    normalized = "".join(
        char.lower()
        for char in unicodedata.normalize("NFKC", text)
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def _next_review_interval(rating: int) -> timedelta:
    if rating <= 1:
        return timedelta(days=1)
    if rating == 2:
        return timedelta(days=3)
    return timedelta(days=7)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
