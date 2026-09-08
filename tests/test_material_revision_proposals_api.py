from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.diagnostics import read_recent_log_entries
from offerpilot.models import MaterialRevisionProposal
from offerpilot.repositories.applications import ApplicationsRepository


class ProposalModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(
            content=json.dumps(
                {
                    "summary": "Tailor the backend experience.",
                    "changes": [
                        {
                            "id": "change-fastapi",
                            "path": "/experience/0/highlights/0",
                            "before": "Built APIs",
                            "after": "Built FastAPI APIs",
                            "rationale": "Make the existing API experience specific.",
                            "evidence_refs": [
                                {
                                    "source": "resume",
                                    "path": "/experience/0/highlights/0",
                                    "excerpt": "Built APIs",
                                }
                            ],
                        }
                    ],
                }
            )
        )

def _create_jd(client: TestClient, application_id: int, key: str) -> int:
    response = client.post(
        f"/api/applications/{application_id}/job-description/versions",
        json={
            "jd_text": "FastAPI backend",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def _ready_client(tmp_path) -> tuple[TestClient, dict[str, object], dict[str, object]]:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=ProposalModel()))
    app = client.post(
        "/api/applications", json={"company_name": "Acme", "position_name": "Backend"}
    ).json()
    resume = client.post(
        "/api/resumes",
        json={
            "title": "Backend Resume",
            "text": "Built APIs",
            "content_json": {"experience": [{"highlights": ["Built APIs"]}]},
        },
    ).json()
    jd = client.post(
        f"/api/applications/{app['id']}/job-description/versions",
        json={
            "jd_text": "FastAPI backend",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "material-revision-jd-01",
        },
    ).json()
    kit = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd["id"]},
    )
    assert kit.status_code == 201
    return client, app, resume


def test_api_generates_reviews_and_accepts_selected_change(tmp_path) -> None:
    client, app, resume = _ready_client(tmp_path)
    created = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "Highlight API experience", "user_assertions": []},
    )
    assert created.status_code == 201
    proposal = created.json()
    assert proposal["status"] == "draft"
    assert "source_snapshot" not in proposal
    assert proposal["changes"][0]["evidence_refs"][0]["excerpt"] == "Built APIs"

    accepted = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals/{proposal['id']}/accept",
        json={
            "expected_proposal_sha256": proposal["proposal_sha256"],
            "selected_change_ids": ["change-fastapi"],
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["result_resume"]["parent_resume_id"] == resume["id"]
    assert accepted.json()["result_resume"]["is_master"] is False


def test_api_rejects_material_proposal_when_kit_jd_is_expired(tmp_path) -> None:
    client, app, _resume = _ready_client(tmp_path)
    updated = client.post(
        f"/api/applications/{app['id']}/job-description/versions",
        json={
            "jd_text": "Updated FastAPI backend",
            "source_url": None,
            "expected_current_version_id": 1,
            "idempotency_key": "material-stale-kit-jd-01",
        },
    )
    assert updated.status_code == 201
    response = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "Highlight API experience", "user_assertions": []},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "application_jd_source_conflict"
    assert client.get(f"/api/applications/{app['id']}/material-revision-proposals").json() == []


def test_api_rejects_invalid_model_without_writing_and_hides_deleted_application(tmp_path) -> None:
    class InvalidModel:
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            return Assistant(content=json.dumps({"summary": "bad", "changes": [{"path": "/contact/name"}]}))

    client = TestClient(create_app(data_dir=tmp_path, chat_model=InvalidModel()))
    app = client.post(
        "/api/applications", json={"company_name": "Acme", "position_name": "Backend"}
    ).json()
    resume = client.post(
        "/api/resumes", json={"title": "Backend", "text": "Built APIs", "content_json": {"raw_text": "Built APIs"}}
    ).json()
    jd_version_id = _create_jd(client, int(app["id"]), "material-revision-jd-02")
    kit = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd_version_id},
    )
    assert kit.status_code == 201

    response = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "", "user_assertions": []},
    )
    assert response.status_code == 502
    assert response.json() == {
        "error_code": "material_proposal_unverifiable",
        "error": "AI returned a proposal that could not be verified. Please retry.",
    }
    messages = [entry["message"] for entry in read_recent_log_entries(tmp_path)]
    diagnostic = next(message for message in messages if message.startswith("material_proposal_generation "))
    assert "category=invalid_change_shape" in diagnostic
    assert "repair_attempted=true" in diagnostic
    assert "retry_count=1" in diagnostic
    assert '"evidence_refs":0' in diagnostic
    assert "bad" not in diagnostic
    assert client.get(f"/api/applications/{app['id']}/material-revision-proposals").json() == []

    deleted = client.delete(f"/api/applications/{app['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/applications/{app['id']}/material-revision-proposals").status_code == 404


def test_api_validates_assertions_and_empty_accept_selection(tmp_path) -> None:
    client, app, _resume = _ready_client(tmp_path)
    too_many = ["assertion"] * 11
    response = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "", "user_assertions": too_many},
    )
    assert response.status_code == 422

    created = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "", "user_assertions": []},
    ).json()
    empty = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals/{created['id']}/accept",
        json={
            "expected_proposal_sha256": created["proposal_sha256"],
            "selected_change_ids": [],
        },
    )
    assert empty.status_code == 422


def test_api_rejects_non_finite_strict_model_output_without_creating_draft(tmp_path) -> None:
    class NonFiniteModel(ProposalModel):
        def __init__(self) -> None:
            self.non_finite = False

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            if self.non_finite:
                return Assistant(content='{"summary":"x","changes":[],"ignored":NaN}')
            return super().complete(messages, tools)

    model = NonFiniteModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))
    app = client.post(
        "/api/applications", json={"company_name": "Acme", "position_name": "Backend"}
    ).json()
    resume = client.post(
        "/api/resumes",
        json={"title": "Backend", "text": "Built APIs", "content_json": {"raw_text": "Built APIs"}},
    ).json()
    jd_version_id = _create_jd(client, int(app["id"]), "material-revision-jd-03")
    kit = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd_version_id},
    )
    assert kit.status_code == 201
    model.non_finite = True

    response = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "", "user_assertions": []},
    )

    assert response.status_code == 502
    with session_factory_for_data_dir(tmp_path)() as session:
        assert list(session.scalars(select(MaterialRevisionProposal))) == []


def test_api_returns_404_when_application_is_deleted_during_generation(tmp_path) -> None:
    class DeletesApplicationModel(ProposalModel):
        def __init__(self) -> None:
            self.application_id: int | None = None

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            assert self.application_id is not None
            ApplicationsRepository(session_factory_for_data_dir(tmp_path)).delete(self.application_id)
            return super().complete(messages, tools)

    model = DeletesApplicationModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))
    app = client.post(
        "/api/applications", json={"company_name": "Acme", "position_name": "Backend"}
    ).json()
    model.application_id = int(app["id"])
    resume = client.post(
        "/api/resumes",
        json={"title": "Backend", "text": "Built APIs", "content_json": {"raw_text": "Built APIs"}},
    ).json()
    jd_version_id = _create_jd(client, int(app["id"]), "material-revision-jd-04")
    kit = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd_version_id},
    )
    assert kit.status_code == 201

    response = client.post(
        f"/api/applications/{app['id']}/material-revision-proposals",
        json={"instructions": "", "user_assertions": []},
    )

    assert response.status_code == 404
    with session_factory_for_data_dir(tmp_path)() as session:
        assert list(session.scalars(select(MaterialRevisionProposal))) == []
