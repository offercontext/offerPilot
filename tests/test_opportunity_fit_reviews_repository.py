from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from offerpilot.ai.types import Assistant
from offerpilot.db import init_database
from offerpilot.models import OpportunityFitReview
from offerpilot.models import OpportunityFitReviewSession, OpportunityFitReviewStage
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.opportunity_fit_reviews import (
    OpportunityFitReviewConflictError,
    OpportunityFitReviewNotFound,
    OpportunityFitReviewsRepository,
)
from offerpilot.ai.opportunity_fit_reviews import OpportunityFitModelError
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


def _triage() -> dict[str, object]:
    return {
        "recommendation": "hold",
        "hard_constraints": [
            {
                "id": "location",
                "requirement": "Shanghai office",
                "status": "unknown",
                "explanation": "Availability is not in the resume.",
                "evidence_refs": [
                    {"source": "jd", "path": "/text", "excerpt": "Kubernetes preferred"}
                ],
            }
        ],
        "fit_signals": [
            {
                "id": "api",
                "statement": "Existing API work is relevant.",
                "evidence_refs": [
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ],
        "gaps": [
            {
                "id": "kubernetes",
                "requirement": "Kubernetes production experience",
                "kind": "preferred",
                "candidate_status": "unknown",
                "evidence_refs": [
                    {"source": "jd", "path": "/text", "excerpt": "Kubernetes preferred"},
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ],
        "deadline": {"status": "not_stated", "text": "", "evidence_refs": []},
        "next_questions": ["Can you work in Shanghai?"],
    }


def _deep() -> dict[str, object]:
    return {
        "strengths": [
            {
                "id": "api",
                "statement": "API implementation is a strength.",
                "evidence_refs": [
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ],
        "gaps_to_address": [
            {
                "id": "kubernetes",
                "statement": "Kubernetes experience needs confirmation.",
                "evidence_refs": [
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ],
        "questions_to_clarify": [
            {"id": "location", "statement": "Confirm Shanghai availability.", "evidence_refs": []}
        ],
        "recommended_path": "clarify_first",
        "next_actions": [
            {"id": "assertion", "label": "补充事实", "kind": "add_assertion"}
        ],
    }


class ReviewModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        payload = _triage() if self.calls % 2 == 1 else _deep()
        return Assistant(content=json.dumps(payload, ensure_ascii=False))


def _v2_triage() -> dict[str, object]:
    return {
        "schema_version": 2,
        "stage": "triage",
        "source": {
            "kind": "opportunity_fit",
            "contract_version": "opportunity_fit.v2",
            "snapshot_version": "1",
        },
        "summary": {
            "text": "The API experience is relevant to the role.",
            "rationale": "The resume contains a directly relevant API example.",
            "evidence_refs": [
                {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
            ],
        },
        "conditions": [
            {
                "id": "api-evidence",
                "text": "Confirm the API work scope in discussion.",
                "rationale": "The frozen resume provides one API example.",
                "evidence_refs": [
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ],
        "risks": [
            {
                "id": "kubernetes-risk",
                "text": "Kubernetes experience needs confirmation.",
                "rationale": "The JD prefers Kubernetes while the resume does not cite it.",
                "evidence_refs": [
                    {"source": "jd", "path": "/jd_text", "excerpt": "Kubernetes preferred"}
                ],
            }
        ],
        "questions": [
            {
                "question_id": "opportunity_fit.question.v1.jd_success_criteria",
                "text": "请确认该岗位最重要的成功标准是什么？",
                "evidence_refs": [],
            }
        ],
        "next_steps": [],
    }


def _v2_deep() -> dict[str, object]:
    payload = _v2_triage()
    payload["stage"] = "deep_review"
    return payload


class V2ReviewModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        payload = _v2_triage() if self.calls == 1 else _v2_deep()
        return Assistant(content=json.dumps(payload, ensure_ascii=False))


class FailOnceV2ReviewModel(V2ReviewModel):
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("provider timeout")
        return Assistant(content=json.dumps(_v2_triage(), ensure_ascii=False))


class BlockingV2ReviewModel(V2ReviewModel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider was not released")
        return Assistant(content=json.dumps(_v2_triage(), ensure_ascii=False))


class BlockingDeepV2ReviewModel(V2ReviewModel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider was not released")
        return Assistant(content=json.dumps(_v2_deep(), ensure_ascii=False))


class BlockingErrorV2ReviewModel(V2ReviewModel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider was not released")
        raise TimeoutError("test provider failed")


class BlockingInvalidV2ReviewModel(V2ReviewModel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider was not released")
        return Assistant(content=json.dumps({"schema_version": 2}, ensure_ascii=False))


class DistinctBlockingV2ReviewModel(V2ReviewModel):
    def __init__(self) -> None:
        super().__init__()
        self.first_entered = Event()
        self.second_entered = Event()
        self.release = Event()

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            self.first_entered.set()
        elif self.calls == 2:
            self.second_entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test provider was not released")
        payload = _v2_triage()
        payload["next_steps"] = [
            {
                "id": f"call-{self.calls}",
                "text": "Keep the API example ready for review.",
                "rationale": "The frozen resume contains an API example.",
                "evidence_refs": [
                    {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
                ],
            }
        ]
        return Assistant(content=json.dumps(payload, ensure_ascii=False))


def _ready(tmp_path):
    factory = init_database(tmp_path / "data.db")
    applications = ApplicationsRepository(factory)
    resumes = ResumesRepository(factory)
    application = applications.create(
        ApplicationCreate(company_name="Acme", position_name="Backend", notes="private note")
    )
    resume = resumes.create(
        ResumeCreate(
            title="Backend Resume",
            parsed_data="Built APIs",
            content_json={"raw_text": "Built APIs", "skills": ["Python"]},
        )
    )
    return factory, application, resume


def _add_jd_version(factory, application_id: int) -> int:
    return ApplicationJDService(factory).create_version(
        application_id,
        jd_text="Kubernetes preferred",
        source_url=None,
        source_kind="ui",
        expected_current_version_id=None,
        idempotency_key="repository-jd-version-01",
    ).version.id


def test_create_triage_persists_minimal_immutable_snapshot_and_is_idempotent(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory)
    model = ReviewModel()

    review, created = repository.create_triage(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "Recruiter copy",
        ["I can work in Shanghai."],
        "d4b4b5e8-0a3a-4a3e-8e4d-6bc7a04d36b0",
        model,
    )

    assert created is True
    stored = json.loads(review.source_snapshot_json)
    assert stored["application"] == {
        "id": application.id,
        "company_name": "Acme",
        "position_name": "Backend",
    }
    assert stored["resume"]["content_json"]["raw_text"] == "Built APIs"
    assert stored["candidate_assertions"] == [{"index": 0, "text": "I can work in Shanghai."}]
    assert "notes" not in stored["application"]

    replay, replay_created = repository.create_triage(
        application.id,
        resume.id,
        "different JD",
        "different source",
        [],
        "d4b4b5e8-0a3a-4a3e-8e4d-6bc7a04d36b0",
        model,
    )
    assert replay_created is False
    assert replay.id == review.id
    assert model.calls == 1


def test_create_triage_does_not_leave_record_when_application_deleted_during_model_call(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory)

    class DeletesApplicationModel(ReviewModel):
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            ApplicationsRepository(factory).delete(application.id)
            return super().complete(messages, tools)

    with pytest.raises(OpportunityFitReviewNotFound):
        repository.create_triage(
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            "26b4dd35-75e6-4d3f-8806-3cb7bc9f3e2e",
            DeletesApplicationModel(),
        )

    with factory() as session:
        assert list(session.scalars(select(OpportunityFitReview))) == []


def test_deep_review_reads_saved_snapshot_and_is_idempotent(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory)
    model = ReviewModel()
    review, _created = repository.create_triage(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "1b9f9d39-dfbd-464e-8bb0-4a50b09b5e5c",
        model,
    )

    first, created = repository.create_deep_review(application.id, review.id, model)
    replay, replay_created = repository.create_deep_review(application.id, review.id, model)

    assert created is True
    assert replay_created is False
    assert first.id == replay.id == review.id
    assert json.loads(first.deep_review_json or "{}")["recommended_path"] == "clarify_first"
    assert model.calls == 2


def test_hidden_application_and_resume_are_not_readable(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory)
    ApplicationsRepository(factory).delete(application.id)

    with pytest.raises(OpportunityFitReviewNotFound):
        repository.create_triage(application.id, resume.id, "JD", "copy", [], "f6f71c9f-6e8d-4c9f-9d5f-1cc3d9687382", ReviewModel())


def test_v2_triage_creates_one_root_and_stage_and_replays_same_key(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")
    model = V2ReviewModel()
    key = "f6f71c9f-6e8d-4c9f-9d5f-1cc3d9687382"

    first = repository.create_triage_v2(
        application.id, resume.id, "Kubernetes preferred", "copy", [], key, model
    )
    replay = repository.create_triage_v2(
        application.id, resume.id, "Kubernetes preferred", "copy", [], key, model
    )

    assert first[0].id == replay[0].id
    assert first[1].id == replay[1].id
    assert first[2] is True
    assert replay[2] is False
    assert first[3] == replay[3]
    assert model.calls == 1
    with factory() as session:
        assert len(list(session.scalars(select(OpportunityFitReviewSession)))) == 1
        assert len(list(session.scalars(select(OpportunityFitReviewStage)))) == 1


def test_v2_confirmation_token_is_single_use_and_deep_requires_confirmation(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    jd_version_id = _add_jd_version(factory, application.id)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")
    model = V2ReviewModel()
    key = "be4b3e55-37c6-4870-9875-6b6f2f7f04d4"
    session, triage, created, token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        ["I can work in Shanghai."],
        key,
        model,
        jd_version_id=jd_version_id,
    )

    with pytest.raises(OpportunityFitReviewConflictError):
        repository.create_deep_review_v2(
            application.id,
            session.id,
            triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            "c6e6f3a0-75c7-477c-a560-8fdc67ec6bf6",
            model,
        )

    confirmed = repository.confirm_triage_v2(application.id, session.id, triage.id, token)
    assert confirmed.status == "confirmed"
    with pytest.raises(OpportunityFitReviewConflictError):
        repository.confirm_triage_v2(application.id, session.id, triage.id, token)

    deep, deep_created = repository.create_deep_review_v2(
        application.id,
        session.id,
        triage.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "c6e6f3a0-75c7-477c-a560-8fdc67ec6bf6",
        model,
    )
    assert deep_created is True
    assert deep.stage == "deep_review"
    assert model.calls == 2


def test_v2_deep_rejects_confirmed_parent_without_jd_version(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")
    root, triage, _created, token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "legacy-triage-without-jd-version",
        V2ReviewModel(),
    )
    repository.confirm_triage_v2(application.id, root.id, triage.id, token)

    with pytest.raises(OpportunityFitReviewConflictError, match="source version"):
        repository.create_deep_review_v2(
            application.id,
            root.id,
            triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            "deep-from-legacy-triage",
            V2ReviewModel(),
        )


def test_v2_expired_provider_lease_is_taken_over_before_the_next_provider_call(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")
    model = FailOnceV2ReviewModel()
    key = "a3b06b63-8f2b-45c9-ae1f-43dc4da8874f"

    with pytest.raises(OpportunityFitModelError):
        repository.create_triage_v2(
            application.id, resume.id, "Kubernetes preferred", "copy", [], key, model
        )

    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        old_generation = stage.stage_generation
        old_token = stage.provider_call_token
        stage.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    _root, stage, created, _confirmation_token = repository.create_triage_v2(
        application.id, resume.id, "Kubernetes preferred", "copy", [], key, model
    )

    assert created is False
    assert stage.status == "ready"
    assert stage.stage_generation == old_generation + 1
    assert stage.provider_call_token == ""
    assert stage.proposal_sha256
    assert model.calls == 2
    assert old_token


def test_v2_live_lease_heartbeat_keeps_same_key_replay_pending(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = BlockingV2ReviewModel()
    key = "heartbeat-live-key-00000000000000000000000000000001"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)

        replay = repository.create_triage_v2(
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert replay[1].status == "generating"
        assert replay[2] is False
        assert model.calls == 1
        with factory() as session:
            persisted = session.scalar(select(OpportunityFitReviewStage))
            assert persisted is not None
            assert persisted.stage_generation == 1
            assert persisted.provider_call_token
            assert persisted.lease_expires_at is not None

        model.release.set()
        first = first_future.result(timeout=10)

    assert first[1].status == "ready"
    assert model.calls == 1


def test_v2_deep_review_uses_live_lease_heartbeat(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    jd_version_id = _add_jd_version(factory, application.id)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    triage_model = V2ReviewModel()
    root, triage, _created, token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "deep-heartbeat-triage-key-00000000000000000001",
        triage_model,
        jd_version_id=jd_version_id,
    )
    repository.confirm_triage_v2(application.id, root.id, triage.id, token)
    model = BlockingDeepV2ReviewModel()
    key = "deep-heartbeat-key-00000000000000000000000000000001"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(
            repository.create_deep_review_v2,
            application.id,
            root.id,
            triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)

        replay = repository.create_deep_review_v2(
            application.id,
            root.id,
            triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert replay[0].status == "generating"
        assert replay[1] is False
        assert model.calls == 1

        model.release.set()
        first = first_future.result(timeout=10)

    assert first[0].status == "ready"
    assert model.calls == 1


def test_v2_expired_triage_owner_cannot_finalize_ready(tmp_path, monkeypatch) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = BlockingV2ReviewModel()
    key = "heartbeat-expired-finalize-key-00000000000000001"

    class StoppedHeartbeat:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.LeaseHeartbeat",
        StoppedHeartbeat,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)
        model.release.set()
        result = future.result(timeout=10)

    assert result[1].status == "generating"
    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        assert stage.status == "generating"
        assert stage.provider_call_token
        assert stage.lease_expires_at is not None


def test_v2_expired_deep_owner_cannot_finalize_ready(tmp_path, monkeypatch) -> None:
    factory, application, resume = _ready(tmp_path)
    jd_version_id = _add_jd_version(factory, application.id)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    triage_model = V2ReviewModel()
    root, triage, _created, token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "deep-expired-parent-key-000000000000000001",
        triage_model,
        jd_version_id=jd_version_id,
    )
    repository.confirm_triage_v2(application.id, root.id, triage.id, token)
    model = BlockingDeepV2ReviewModel()

    class StoppedHeartbeat:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.LeaseHeartbeat",
        StoppedHeartbeat,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            repository.create_deep_review_v2,
            application.id,
            root.id,
            triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            "deep-expired-finalize-key-00000000000000001",
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)
        model.release.set()
        result = future.result(timeout=10)

    assert result[0].status == "generating"
    with factory() as session:
        stage = session.scalar(
            select(OpportunityFitReviewStage).where(
                OpportunityFitReviewStage.stage == "deep_review"
            )
        )
        assert stage is not None
        assert stage.status == "generating"
        assert stage.provider_call_token


def test_v2_expired_provider_error_does_not_mark_stale_stage_unknown(tmp_path, monkeypatch) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = BlockingErrorV2ReviewModel()
    key = "heartbeat-expired-error-key-00000000000000001"

    class StoppedHeartbeat:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.LeaseHeartbeat",
        StoppedHeartbeat,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)
        model.release.set()
        with pytest.raises(OpportunityFitModelError, match="model provider request failed"):
            future.result(timeout=10)

    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        assert stage.status == "generating"
        assert stage.provider_call_token


def test_v2_expired_contract_error_does_not_delete_stale_stage(tmp_path, monkeypatch) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = BlockingInvalidV2ReviewModel()
    key = "heartbeat-expired-contract-key-0000000000000001"

    class StoppedHeartbeat:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.LeaseHeartbeat",
        StoppedHeartbeat,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.entered.wait(timeout=5)
        time.sleep(1.2)
        model.release.set()
        with pytest.raises(OpportunityFitModelError):
            future.result(timeout=10)

    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        assert stage.status == "generating"
        assert stage.provider_call_token


def test_v2_provider_error_stops_heartbeat_and_allows_takeover_after_lease(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = FailOnceV2ReviewModel()
    key = "heartbeat-error-key-00000000000000000000000000000001"

    with pytest.raises(OpportunityFitModelError):
        repository.create_triage_v2(
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )

    time.sleep(1.2)
    replay = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        key,
        model,
    )

    assert replay[1].status == "ready"
    assert model.calls == 2


def test_v2_stopped_heartbeat_allows_takeover_but_fences_late_result(tmp_path, monkeypatch) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(
        factory,
        confirmation_secret="test-secret",
        lease_seconds=0.8,
        heartbeat_interval_seconds=0.1,
    )
    model = DistinctBlockingV2ReviewModel()
    key = "heartbeat-takeover-key-00000000000000000000000000001"

    class StoppedHeartbeat:
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.LeaseHeartbeat",
        StoppedHeartbeat,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.first_entered.wait(timeout=5)
        time.sleep(1.2)
        second_future = pool.submit(
            repository.create_triage_v2,
            application.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            key,
            model,
        )
        assert model.second_entered.wait(timeout=5)
        model.release.set()
        first_future.result(timeout=10)
        second = second_future.result(timeout=10)

    assert model.calls == 2
    assert second[1].status == "ready"
    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        assert stage.stage_generation == 2
        assert '"call-2"' in stage.proposal_json
        assert '"call-1"' not in stage.proposal_json


def test_v2_expired_lease_two_connections_only_one_owner_calls_provider(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")
    setup_model = FailOnceV2ReviewModel()
    key = "7dbf4fcb-67ad-4a9d-942e-8e9d1d8b7e26"

    with pytest.raises(OpportunityFitModelError):
        repository.create_triage_v2(
            application.id, resume.id, "Kubernetes preferred", "copy", [], key, setup_model
        )
    with factory() as session:
        stage = session.scalar(select(OpportunityFitReviewStage))
        assert stage is not None
        stage.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    engine_a = create_engine(factory.kw["bind"].url, connect_args={"check_same_thread": False})
    engine_b = create_engine(factory.kw["bind"].url, connect_args={"check_same_thread": False})
    factory_a = sessionmaker(bind=engine_a, expire_on_commit=False)
    factory_b = sessionmaker(bind=engine_b, expire_on_commit=False)
    repository_a = OpportunityFitReviewsRepository(factory_a, confirmation_secret="test-secret")
    repository_b = OpportunityFitReviewsRepository(factory_b, confirmation_secret="test-secret")

    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    class BlockingModel(V2ReviewModel):
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            assert release.wait(timeout=5)
            return Assistant(content=json.dumps(_v2_triage(), ensure_ascii=False))

    model = BlockingModel()

    def invoke(repo):
        return repo.create_triage_v2(
            application.id, resume.id, "Kubernetes preferred", "copy", [], key, model
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(invoke, repository_a)
        assert started.wait(timeout=5)
        second_future = pool.submit(invoke, repository_b)
        second = second_future.result(timeout=5)
        release.set()
        first = first_future.result(timeout=5)

    assert calls == 1
    assert first[1].status == "ready"
    assert second[1].status in {"generating", "ready"}
    with factory() as session:
        stages = list(session.scalars(select(OpportunityFitReviewStage)))
        roots = list(session.scalars(select(OpportunityFitReviewSession)))
        assert len(roots) == 1
        assert len(stages) == 1
        assert stages[0].stage_generation == 2
    engine_a.dispose()
    engine_b.dispose()


def test_v2_deep_same_key_across_roots_returns_idempotency_conflict(tmp_path) -> None:
    factory, application, resume = _ready(tmp_path)
    jd_version_id = _add_jd_version(factory, application.id)
    repository = OpportunityFitReviewsRepository(factory, confirmation_secret="test-secret")

    first_root, first_triage, _created, first_token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "triage-root-one",
        V2ReviewModel(),
        jd_version_id=jd_version_id,
    )
    repository.confirm_triage_v2(application.id, first_root.id, first_triage.id, first_token)
    repository.create_deep_review_v2(
        application.id,
        first_root.id,
        first_triage.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "deep-shared-key",
        V2ReviewModel(),
    )

    second_root, second_triage, _created, second_token = repository.create_triage_v2(
        application.id,
        resume.id,
        "Kubernetes preferred",
        "copy",
        [],
        "triage-root-two",
        V2ReviewModel(),
        jd_version_id=jd_version_id,
    )
    repository.confirm_triage_v2(application.id, second_root.id, second_triage.id, second_token)

    with pytest.raises(OpportunityFitReviewConflictError, match="idempotency"):
        repository.create_deep_review_v2(
            application.id,
            second_root.id,
            second_triage.id,
            resume.id,
            "Kubernetes preferred",
            "copy",
            [],
            "deep-shared-key",
            V2ReviewModel(),
        )
