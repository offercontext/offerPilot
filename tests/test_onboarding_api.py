from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import AIProviderProfile, Config, save_config


class FailingModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider unavailable")


def test_onboarding_derives_real_workspace_steps(tmp_path):
    save_config(
        tmp_path,
        Config(
            providers=[
                AIProviderProfile(
                    id="local",
                    label="Local",
                    provider="openai_compatible",
                    api_key="sk-test",
                )
            ],
            active_provider_id="local",
        ),
    )
    client = TestClient(create_app(data_dir=tmp_path, chat_model=FailingModel()))

    initial = client.get("/api/onboarding")
    assert initial.status_code == 200
    assert initial.json()["steps"] == {
        "configure_ai": True,
        "create_primary_resume": False,
        "create_first_application": False,
        "send_first_pilot_message": False,
    }

    resume = client.post("/api/resumes/from-sample", json={"sample_id": "backend"})
    application = client.post(
        "/api/applications",
        json={"company_name": "验收科技", "position_name": "后端工程师"},
    )
    failed_chat = client.post("/api/chat", json={"message": "你好", "conversation_id": 0})
    assert resume.status_code == 201
    assert application.status_code == 201
    assert failed_chat.status_code == 502

    completed = client.get("/api/onboarding")
    assert completed.status_code == 200
    assert completed.json() == {
        "steps": {
            "configure_ai": True,
            "create_primary_resume": True,
            "create_first_application": True,
            "send_first_pilot_message": True,
        },
        "completed_count": 4,
        "is_complete": True,
        "force_open": False,
    }


def test_onboarding_force_open_is_persisted(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.patch("/api/onboarding", json={"force_open": True})

    assert response.status_code == 200
    assert response.json()["force_open"] is True
    reloaded = TestClient(create_app(data_dir=tmp_path)).get("/api/onboarding")
    assert reloaded.status_code == 200
    assert reloaded.json()["force_open"] is True


def test_onboarding_rejects_non_boolean_force_open(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.patch("/api/onboarding", json={"force_open": "yes"})

    assert response.status_code == 422
    assert response.json()["error"] == "force_open must be boolean"
