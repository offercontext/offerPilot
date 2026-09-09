from uuid import uuid4

from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Application, ApplicationEvent, Conversation, Resume


def test_context_and_proactive_routes_registered_with_opt_in_defaults(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        policy = client.get("/api/context-policies")
        assert policy.status_code == 200
        assert not policy.json()["policies"]["older_conversation_summary"]["enabled"]
        proactive = client.get("/api/proactive/settings")
        assert proactive.status_code == 200
        settings = proactive.json()["settings"]
        assert not settings["enabled"] and not settings["drafts_enabled"]
        assert client.get("/api/proactive/jobs").json() == {"items": []}
        rejected = client.put("/api/proactive/settings", json={"expected_revision": 0, "confirmed": False, "settings": settings})
        assert rejected.status_code == 409
        accepted = client.put("/api/proactive/settings", json={"expected_revision": 0, "confirmed": True, "settings": settings})
        assert accepted.status_code == 200
        assert client.put("/api/proactive/settings", json={"expected_revision": 0, "confirmed": True, "settings": settings}).status_code == 409
        assert client.post("/api/conversations/999/older-summary", json={"confirmed": True}).status_code == 409


def test_readiness_context_routes_read_confirm_and_clear(tmp_path):
    sessions = session_factory_for_data_dir(tmp_path)
    with sessions() as session:
        application = Application(company_name="Acme", position_name="Backend")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            subtype="technical",
            status="todo",
        )
        resume = Resume(title="Backend resume")
        conversation = Conversation(
            context_type="application",
            context_ref=str(application.id),
        )
        session.add_all((event, resume, conversation))
        session.commit()
        conversation_id = conversation.id

    endpoint = f"/api/chat/conversations/{conversation_id}/readiness-context"
    with TestClient(create_app(data_dir=tmp_path)) as client:
        baseline = client.get(endpoint)
        assert baseline.status_code == 200
        assert baseline.json()["state"] == "not_applicable"
        assert baseline.json()["revision"] == 0

        confirmed = client.put(
            endpoint,
            json={
                "mutation_id": str(uuid4()),
                "expected_revision": 0,
                "confirmed": True,
                "target_event_id": event.id,
                "resume_id": resume.id,
                "ordered_version_ids": [],
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["state"] == "confirmed"
        assert confirmed.json()["revision"] == 1

        cleared = client.post(
            f"{endpoint}/clear",
            json={
                "mutation_id": str(uuid4()),
                "expected_revision": 1,
                "confirmed": True,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["state"] == "withdrawn"
        assert cleared.json()["revision"] == 2
