from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from offerpilot.ai.types import Assistant
from offerpilot.ai.opportunity_fit_reviews import ValidatedOpportunityOutput
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.diagnostics import read_recent_log_entries
from offerpilot.models import OpportunityFitReview
from offerpilot.models import OpportunityFitReviewStage
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.opportunity_fit_reviews import (
    OpportunityFitReviewConfirmationExpired,
    OpportunityFitReviewsRepository,
)


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
                "requirement": "Kubernetes preferred",
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


def _v2_payload(stage: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "stage": stage,
        "source": {
            "kind": "opportunity_fit",
            "contract_version": "opportunity_fit.v2",
            "snapshot_version": "1",
        },
        "summary": {
            "text": "The API experience is relevant to the role.",
            "rationale": "The frozen resume provides a directly relevant example.",
            "evidence_refs": [
                {"source": "resume", "path": "/raw_text", "excerpt": "Built APIs"}
            ],
        },
        "conditions": [
            {
                "id": "api",
                "text": "Confirm the scope of API work.",
                "rationale": "The resume contains one API example.",
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


class ReviewModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        return Assistant(
            content=json.dumps(_triage() if self.calls == 1 else _deep(), ensure_ascii=False)
        )


class V2ReviewModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        return Assistant(content=json.dumps(_v2_payload("triage" if self.calls == 1 else "deep_review"), ensure_ascii=False))


def _ready(tmp_path, model=None):
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model or ReviewModel()))
    application = client.post(
        "/api/applications",
        json={"company_name": "Acme", "position_name": "Backend", "notes": "private note"},
    ).json()
    resume = client.post(
        "/api/resumes",
        json={
            "title": "Backend Resume",
            "text": "Built APIs",
            "content_json": {"raw_text": "Built APIs", "skills": ["Python"]},
        },
    ).json()
    jd = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "Kubernetes preferred",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "opportunity-fit-jd-01",
        },
    ).json()
    application["jd_version_id"] = jd["id"]
    return client, application, resume


def test_api_creates_lists_and_deep_reviews_without_snapshot_leak(tmp_path) -> None:
    client, application, resume = _ready(tmp_path)
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    payload = {
        "resume_id": resume["id"],
        "jd_text": "Kubernetes preferred",
        "jd_source_label": "Recruiter copy",
        "candidate_assertions": ["I can work in Shanghai."],
        "idempotency_key": "d4b4b5e8-0a3a-4a3e-8e4d-6bc7a04d36b0",
    }

    created = client.post(path, json=payload)
    assert created.status_code == 410
    assert created.json()["error_code"] == "opportunity_fit_v1_write_disabled"


def test_api_validates_jd_resume_and_assertion_limits(tmp_path) -> None:
    client, application, resume = _ready(tmp_path)
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    base = {
        "resume_id": resume["id"],
        "jd_source_label": "copy",
        "candidate_assertions": [],
        "idempotency_key": "f6f71c9f-6e8d-4c9f-9d5f-1cc3d9687382",
    }

    assert client.post(path, json={**base, "jd_text": " "}).status_code == 410
    assert client.post(path, json={**base, "jd_text": "JD", "candidate_assertions": ["x"] * 11}).status_code == 410
    assert client.post(path, json={**base, "jd_text": "JD", "candidate_assertions": ["x" * 501]}).status_code == 410
    assert client.post(path, json={**base, "jd_text": "JD", "resume_id": 999}).status_code == 410


def test_api_returns_stable_502_without_record_for_unverifiable_model(tmp_path) -> None:
    class InvalidModel:
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            return Assistant(content=json.dumps({"summary": "bad", "extra": True}))

    client, application, resume = _ready(tmp_path, InvalidModel())
    response = client.post(
        f"/api/applications/{application['id']}/opportunity-fit-reviews",
        json={
            "resume_id": resume["id"],
            "jd_text": "JD",
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "26b4dd35-75e6-4d3f-8806-3cb7bc9f3e2e",
        },
    )
    assert response.status_code == 410
    assert response.json()["error_code"] == "opportunity_fit_v1_write_disabled"
    with session_factory_for_data_dir(tmp_path)() as session:
        assert list(session.scalars(select(OpportunityFitReview))) == []


def test_api_rederives_legacy_summary_without_frontend_crash(tmp_path) -> None:
    client, application, resume = _ready(tmp_path)
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    created = client.post(
        path,
        json={
            "resume_id": resume["id"],
            "jd_text": "Kubernetes preferred",
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "26b4dd35-75e6-4d3f-8806-3cb7bc9f3e2e",
        },
    )
    assert created.status_code == 410
    assert created.json()["error_code"] == "opportunity_fit_v1_write_disabled"


def test_api_does_not_retry_provider_failure(tmp_path) -> None:
    class ProviderFailure:
        calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise RuntimeError("provider unavailable")

    model = ProviderFailure()
    client, application, resume = _ready(tmp_path, model)
    response = client.post(
        f"/api/applications/{application['id']}/opportunity-fit-reviews",
        json={
            "resume_id": resume["id"],
            "jd_text": "JD",
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "26b4dd35-75e6-4d3f-8806-3cb7bc9f3e2e",
        },
    )
    assert response.status_code == 410
    assert model.calls == 0


def test_api_v2_requires_confirmation_before_deep_and_preserves_v1_contract(tmp_path) -> None:
    model = V2ReviewModel()
    client, application, resume = _ready(tmp_path, model)
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    payload = {
        "schema_version": 2,
        "resume_id": resume["id"],
        "jd_version_id": application["jd_version_id"],
        "jd_source_label": "copy",
        "candidate_assertions": ["I can work in Shanghai."],
        "idempotency_key": "c6e6f3a0-75c7-477c-a560-8fdc67ec6bf6",
    }
    created = client.post(path, json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["schema_version"] == 2
    assert body["stage"] == "triage"
    assert "recommendation" not in body["proposal"]
    assert "source_snapshot_json" not in body
    detail = client.get(f"{path}/{body['review_id']}", params={"schema_version": 2})
    assert detail.status_code == 200
    assert detail.json()["schema_version"] == 2
    assert detail.json()["review_id"] == body["review_id"]

    deep_payload = {
        **payload,
        "parent_triage_stage_id": body["stage_id"],
        "idempotency_key": "f9b4a7cc-6e4c-4a87-9f64-3e22d3491e5b",
    }
    deep_payload.pop("jd_version_id")
    deep = client.post(f"{path}/{body['review_id']}/deep-review", json=deep_payload)
    assert deep.status_code == 409

    confirmed = client.post(
        f"{path}/{body['review_id']}/triage/{body['stage_id']}/confirm",
        json={"confirmation_token": body["confirmation_token"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["stage_status"] == "confirmed"

    deep = client.post(f"{path}/{body['review_id']}/deep-review", json=deep_payload)
    assert deep.status_code == 201
    assert deep.json()["stage"] == "deep_review"
    assert model.calls == 2


def test_opportunity_fit_deep_review_v1_post_is_disabled_without_provider_call(tmp_path) -> None:
    class CountingModel(V2ReviewModel):
        calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().complete(messages, tools)

    model = CountingModel()
    client, application, _ = _ready(tmp_path, model)
    response = client.post(
        f"/api/applications/{application['id']}/opportunity-fit-reviews/999/deep-review",
        json={"parent_triage_stage_id": 1, "idempotency_key": "v1-deep-disabled-01"},
    )

    assert response.status_code == 410
    assert response.json()["error_code"] == "opportunity_fit_v1_write_disabled"
    assert model.calls == 0

    no_payload = client.post(
        f"/api/applications/{application['id']}/opportunity-fit-reviews/999/deep-review",
    )
    assert no_payload.status_code == 410
    assert no_payload.json()["error_code"] == "opportunity_fit_v1_write_disabled"
    assert model.calls == 0


def test_v2_confirmation_checks_application_before_consuming_token(tmp_path) -> None:
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    other = client.post(
        "/api/applications",
        json={"company_name": "Other", "position_name": "Backend"},
    ).json()
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    created_response = client.post(
        path,
        json={
            "schema_version": 2,
            "resume_id": resume["id"],
            "jd_version_id": application["jd_version_id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "3b0d4a20-2d22-4aab-9d3f-2fdb1b8b93f7",
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()

    wrong = client.post(
        f"/api/applications/{other['id']}/opportunity-fit-reviews/{created['review_id']}"
        f"/triage/{created['stage_id']}/confirm",
        json={"confirmation_token": created["confirmation_token"]},
    )
    assert wrong.status_code == 404

    correct = client.post(
        f"{path}/{created['review_id']}/triage/{created['stage_id']}/confirm",
        json={"confirmation_token": created["confirmation_token"]},
    )
    assert correct.status_code == 200
    assert correct.json()["stage_status"] == "confirmed"


def test_v2_deep_rejects_expired_confirmed_triage_parent(tmp_path) -> None:
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    triage = client.post(
        path,
        json={
            "schema_version": 2,
            "resume_id": resume["id"],
            "jd_version_id": application["jd_version_id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "c6e6f3a0-75c7-477c-a560-8fdc67ec6bf6",
        },
    )
    assert triage.status_code == 201, triage.text
    triage_body = triage.json()
    confirmed = client.post(
        f"{path}/{triage_body['review_id']}/triage/{triage_body['stage_id']}/confirm",
        json={"confirmation_token": triage_body["confirmation_token"]},
    )
    assert confirmed.status_code == 200
    updated = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "Updated backend JD",
            "source_url": None,
            "expected_current_version_id": application["jd_version_id"],
            "idempotency_key": "deep-stale-parent-jd-01",
        },
    )
    assert updated.status_code == 201
    deep = client.post(
        f"{path}/{triage_body['review_id']}/deep-review",
        json={
            "schema_version": 2,
            "parent_triage_stage_id": triage_body["stage_id"],
            "resume_id": resume["id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "f9b4a7cc-6e4c-4a87-9f64-3e22d3491e5b",
        },
    )
    assert deep.status_code == 409
    assert deep.json()["error_code"] == "opportunity_fit_source_conflict"


def test_v2_source_cas_rejects_jd_change_after_provider_claim(tmp_path, monkeypatch) -> None:
    entered = Event()
    release = Event()

    def blocked_triage(_model, _snapshot):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(5)
        return ValidatedOpportunityOutput(payload=_v2_payload("triage"))

    monkeypatch.setattr(
        "offerpilot.repositories.opportunity_fit_reviews.generate_triage_v2",
        blocked_triage,
    )
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    payload = {
        "schema_version": 2,
        "resume_id": resume["id"],
        "jd_version_id": application["jd_version_id"],
        "jd_source_label": "copy",
        "candidate_assertions": [],
        "idempotency_key": "9a3c1f3a-7c7a-4d9a-9bb5-000000000001",
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, json=payload)
        if not entered.wait(5):
            early = future.result()
            raise AssertionError(
                f"triage provider barrier was not reached: {early.status_code} {early.text}"
            )
        changed = client.post(
            f"/api/applications/{application['id']}/job-description/versions",
            json={
                "jd_text": "Rust backend JD",
                "source_url": None,
                "expected_current_version_id": application["jd_version_id"],
                "idempotency_key": "9a3c1f3a-7c7a-4d9a-9bb5-000000000002",
            },
        )
        assert changed.status_code == 201
        release.set()
        result = future.result(timeout=5)

    assert result.status_code == 409
    assert result.json()["error_code"] == "application_jd_source_conflict"
    history = client.get(path, params={"schema_version": 2})
    assert history.status_code == 200
    history_body = history.json()
    assert history_body
    assert history_body[0]["latest_stage"]["stage_status"] == "source_conflict"


def test_v2_deep_source_cas_rejects_jd_change_after_provider_claim(tmp_path) -> None:
    entered = Event()
    release = Event()

    class DeepBarrierModel(V2ReviewModel):
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 2:
                entered.set()
                assert release.wait(5)
            return Assistant(
                content=json.dumps(
                    _v2_payload("triage" if self.calls == 1 else "deep_review"),
                    ensure_ascii=False,
                )
            )

    client, application, resume = _ready(tmp_path, DeepBarrierModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    triage = client.post(
        path,
        json={
            "schema_version": 2,
            "resume_id": resume["id"],
            "jd_version_id": application["jd_version_id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "7be2f7d9-9b87-4c0c-a7a5-000000000001",
        },
    )
    assert triage.status_code == 201, triage.text
    triage_body = triage.json()
    confirmed = client.post(
        f"{path}/{triage_body['review_id']}/triage/{triage_body['stage_id']}/confirm",
        json={"confirmation_token": triage_body["confirmation_token"]},
    )
    assert confirmed.status_code == 200

    deep_payload = {
        "schema_version": 2,
        "parent_triage_stage_id": triage_body["stage_id"],
        "resume_id": resume["id"],
        "idempotency_key": "7be2f7d9-9b87-4c0c-a7a5-000000000002",
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            f"{path}/{triage_body['review_id']}/deep-review",
            json=deep_payload,
        )
        if not entered.wait(5):
            early = future.result(timeout=5)
            raise AssertionError(f"deep provider barrier was not reached: {early.status_code} {early.text}")
        changed = client.post(
            f"/api/applications/{application['id']}/job-description/versions",
            json={
                "jd_text": "Rust backend JD",
                "source_url": None,
                "expected_current_version_id": application["jd_version_id"],
                "idempotency_key": "deep-cas-jd-000000000001",
            },
        )
        assert changed.status_code == 201, changed.text
        release.set()
        result = future.result(timeout=5)

    assert result.status_code == 409
    assert result.json()["error_code"] == "opportunity_fit_source_conflict"
    history = client.get(
        f"{path}/{triage_body['review_id']}",
        params={"schema_version": 2},
    )
    assert history.status_code == 200
    stages = history.json()["stages"]
    assert stages[-1]["stage"] == "deep_review"
    assert stages[-1]["stage_status"] == "source_conflict"


def test_v2_confirmation_token_survives_settings_save_and_app_restart(tmp_path) -> None:
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    created = client.post(
        path,
        json={
            "schema_version": 2,
            "resume_id": resume["id"],
            "jd_version_id": application["jd_version_id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "c3a1df1f-68be-4d6a-9a58-cc5f3f0b4f4f",
        },
    )
    assert created.status_code == 201
    body = created.json()

    settings = client.put("/api/settings", json={"log_level": "DEBUG"})
    assert settings.status_code == 200

    restarted = TestClient(create_app(data_dir=tmp_path, chat_model=V2ReviewModel()))
    confirmed = restarted.post(
        f"{path}/{body['review_id']}/triage/{body['stage_id']}/confirm",
        json={"confirmation_token": body["confirmation_token"]},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["stage_status"] == "confirmed"


def test_v2_expired_confirmation_returns_stable_error_and_allows_new_key(tmp_path) -> None:
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    payload = {
        "schema_version": 2,
        "resume_id": resume["id"],
        "jd_version_id": application["jd_version_id"],
        "jd_source_label": "copy",
        "candidate_assertions": [],
        "idempotency_key": "7b8f2df6-03c6-4a2b-a8f9-cf4bca93a9ef",
    }
    created = client.post(path, json=payload)
    assert created.status_code == 201
    body = created.json()
    with session_factory_for_data_dir(tmp_path)() as session:
        stage = session.get(OpportunityFitReviewStage, body["stage_id"])
        assert stage is not None
        stage.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    replay = client.post(path, json=payload)
    assert replay.status_code == 410
    assert replay.json()["error_code"] == "opportunity_fit_triage_confirmation_expired"


def test_v2_confirmation_expiry_between_peek_and_write_returns_410(tmp_path, monkeypatch) -> None:
    client, application, resume = _ready(tmp_path, V2ReviewModel())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    payload = {
        "schema_version": 2,
        "resume_id": resume["id"],
        "jd_version_id": application["jd_version_id"],
        "jd_source_label": "copy",
        "candidate_assertions": [],
        "idempotency_key": "d6fb6bd6-41cc-4f3f-b7b2-936ec5d8e8c5",
    }
    monkeypatch.setattr(OpportunityFitReviewsRepository, "peek_triage_v2", lambda *_args, **_kwargs: None)

    def expired_create(*_args, **_kwargs):
        raise OpportunityFitReviewConfirmationExpired()

    monkeypatch.setattr(OpportunityFitReviewsRepository, "create_triage_v2", expired_create)

    response = client.post(path, json=payload)

    assert response.status_code == 410
    assert response.json()["error_code"] == "opportunity_fit_triage_confirmation_expired"


def test_api_v2_contract_failure_does_not_leave_history_stage(tmp_path) -> None:
    class InvalidV2Model:
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            invalid = _v2_payload("triage")
            invalid["extra"] = "PRIVATE_MODEL_OUTPUT"
            return Assistant(content=json.dumps(invalid, ensure_ascii=False))

    client, application, resume = _ready(tmp_path, InvalidV2Model())
    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    response = client.post(
        path,
        json={
            "schema_version": 2,
            "resume_id": resume["id"],
            "jd_version_id": application["jd_version_id"],
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "f4fbcae0-98fa-4cba-93d2-f7b7d4ccbbcb",
        },
    )

    assert response.status_code == 502
    assert response.json()["error_code"] == "opportunity_fit_unverifiable"
    assert client.get(path).json() == []
    entries = read_recent_log_entries(tmp_path)
    assert any(entry["message"] == "opportunity_fit_unexpected_field" for entry in entries)
    assert all("extra" not in entry["message"] for entry in entries)
    assert all("PRIVATE_MODEL_OUTPUT" not in entry["message"] for entry in entries)


def test_api_hides_soft_deleted_application(tmp_path) -> None:
    client, application, resume = _ready(tmp_path)
    ApplicationsRepository(session_factory_for_data_dir(tmp_path)).delete(application["id"])

    path = f"/api/applications/{application['id']}/opportunity-fit-reviews"
    assert client.get(path).status_code == 404
    assert client.post(
        path,
        json={
            "resume_id": resume["id"],
            "jd_text": "JD",
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "1b9f9d39-dfbd-464e-8bb0-4a50b09b5e5c",
        },
    ).status_code == 410


def test_api_rejects_non_human_application_source(tmp_path) -> None:
    client, _application, resume = _ready(tmp_path)
    applications = ApplicationsRepository(session_factory_for_data_dir(tmp_path))
    created = applications.create(
        ApplicationCreate(company_name="AI Created", position_name="Backend", source="ai")
    )
    path = f"/api/applications/{created.id}/opportunity-fit-reviews"
    response = client.post(
        path,
        json={
            "resume_id": resume["id"],
            "jd_text": "JD",
            "jd_source_label": "copy",
            "candidate_assertions": [],
            "idempotency_key": "1b9f9d39-dfbd-464e-8bb0-4a50b09b5e5c",
        },
    )
    assert response.status_code == 410
