import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app


class JSONModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(
            content=json.dumps(
                {
                    "resume_advice": {
                        "summary": "Strong Go fit",
                        "highlights": ["Go"],
                        "rewrite_bullets": ["Built APIs"],
                        "gaps": [],
                        "notes": "",
                    },
                    "messages": [
                        {
                            "type": "recruiter_email",
                            "title": "Intro",
                            "body": "Hello",
                            "notes": "",
                        }
                    ],
                    "checklist": [{"id": "select_resume", "label": "Select resume", "done": False}],
                }
            )
        )


def test_get_application_material_kit_missing(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    app = client.post(
        "/api/applications",
        json={"company_name": "Acme", "position_name": "Backend"},
    ).json()
    response = client.get(f"/api/applications/{app['id']}/material-kit")

    assert response.status_code == 404
    assert response.json() == {"error": "Material kit not found"}


def test_generate_update_and_conflict_material_kit(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, chat_model=JSONModel()))
    app = client.post(
        "/api/applications",
        json={"company_name": "Acme", "position_name": "Backend"},
    ).json()
    jd = client.post(
        f"/api/applications/{app['id']}/job-description/versions",
        json={
            "jd_text": "Go backend JD",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "material-jd-key-0001",
        },
    ).json()
    resume = client.post(
        "/api/resumes",
        json={"name": "Backend", "text": "Built Go APIs"},
    ).json()

    missing_resume = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"jd_version_id": jd["id"]},
    )
    assert missing_resume.status_code == 400
    assert missing_resume.json() == {"error": "resume_id is required"}

    created_response = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd["id"]},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["status"] == "draft"
    assert created["resume_id"] == resume["id"]
    assert json.loads(created["content_json"])["resume_advice"]["summary"] == "Strong Go fit"

    conflict = client.post(
        f"/api/applications/{app['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd["id"]},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "Material kit already exists"}

    updated_response = client.put(
        f"/api/material-kits/{created['id']}",
        json={"status": "ready", "content_json": {"checklist": [{"id": "x", "done": True}]}},
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["status"] == "ready"
    assert json.loads(updated["content_json"]) == {"checklist": [{"id": "x", "done": True}]}

    next_jd = client.post(
        f"/api/applications/{app['id']}/job-description/versions",
        json={
            "jd_text": "Updated Go backend JD",
            "source_url": None,
            "expected_current_version_id": jd["id"],
            "idempotency_key": "material-jd-key-0002",
        },
    )
    assert next_jd.status_code == 201
    stale_update = client.put(
        f"/api/material-kits/{created['id']}",
        json={"status": "draft"},
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["error_code"] == "application_jd_source_conflict"


def test_material_kit_source_cas_rejects_jd_change_after_provider_claim(tmp_path):
    class BlockingModel(JSONModel):
        def __init__(self) -> None:
            self.entered = Event()
            self.release = Event()
            self.calls = 0

        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.entered.set()
            assert self.release.wait(5)
            return super().complete(messages, tools)

    model = BlockingModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))
    app = client.post(
        "/api/applications", json={"company_name": "Acme", "position_name": "Backend"}
    ).json()
    jd = client.post(
        f"/api/applications/{app['id']}/job-description/versions",
        json={
            "jd_text": "Go backend JD",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "material-barrier-jd-0001",
        },
    ).json()
    resume = client.post(
        "/api/resumes", json={"name": "Backend", "text": "Built Go APIs"}
    ).json()
    path = f"/api/applications/{app['id']}/material-kit/generate"
    payload = {"resume_id": resume["id"], "jd_version_id": jd["id"]}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, json=payload)
        assert model.entered.wait(5)
        changed = client.post(
            f"/api/applications/{app['id']}/job-description/versions",
            json={
                "jd_text": "Rust backend JD",
                "source_url": None,
                "expected_current_version_id": jd["id"],
                "idempotency_key": "material-barrier-jd-0002",
            },
        )
        assert changed.status_code == 201
        model.release.set()
        result = future.result(timeout=5)

    assert result.status_code == 409
    assert result.json()["error_code"] == "application_jd_source_conflict"
    assert client.get(f"/api/applications/{app['id']}/material-kit").status_code == 404
    assert model.calls == 1
