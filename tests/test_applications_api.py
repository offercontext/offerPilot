from fastapi.testclient import TestClient
from sqlalchemy import text

from offerpilot.api import create_app
from offerpilot.db import init_database
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


def test_create_application_defaults_and_list(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend"},
    )
    listed_response = client.get("/api/applications")

    assert response.status_code == 201
    created = response.json()
    listed = listed_response.json()
    assert created["status"] == "applied"
    assert created["source"] == "web"
    assert listed[0]["company_name"] == "ByteDance"


def test_application_status_options_are_exposed_from_backend(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/application-statuses")

    assert response.status_code == 200
    assert [item["value"] for item in response.json()] == [
        "pending",
        "applied",
        "written_test",
        "interview",
        "offer",
        "closed",
    ]
    assert response.json()[0]["label"] == "待投递"


def test_create_application_requires_company_and_position(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/applications", json={"company_name": "ByteDance"})

    assert response.status_code == 400
    assert response.json() == {"error": "company_name and position_name are required"}


def test_create_application_normalizes_legacy_status(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend", "status": "assessment"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "written_test"


def test_create_application_rejects_invalid_status(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend", "status": "onsite"},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "invalid application status: onsite"}


def test_list_applications_rejects_invalid_status_filter(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/applications", params={"status": "onsite"})

    assert response.status_code == 422
    assert response.json() == {"error": "invalid application status: onsite"}


def test_get_application_not_found(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/applications/404")

    assert response.status_code == 404
    assert response.json() == {"error": "Application not found"}


def test_update_application_full_object(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend", "notes": "first"},
    ).json()

    response = client.put(
        f"/api/applications/{created['id']}",
        json={
            "company_name": "Tencent",
            "position_name": "Frontend",
            "job_url": "https://example.test",
            "status": "offer",
            "notes": "second",
        },
    )

    assert response.status_code == 200
    assert response.json()["company_name"] == "Tencent"
    assert response.json()["notes"] == "second"


def test_update_application_status_preserves_existing_fields(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/applications",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "job_url": "https://example.test/job",
            "status": "applied",
            "notes": "keep me",
        },
    ).json()

    response = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "interview"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "interview"
    assert response.json()["company_name"] == "ByteDance"
    assert response.json()["position_name"] == "Backend"
    assert response.json()["job_url"] == "https://example.test/job"
    assert response.json()["notes"] == "keep me"


def test_update_application_to_closed_requires_reason(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend", "status": "interview"},
    ).json()

    missing_reason = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "closed"},
    )
    closed = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "closed", "closed_reason": "岗位关闭"},
    )

    assert missing_reason.status_code == 400
    assert missing_reason.json() == {"error": "closed_reason is required when closing an application"}
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_reason"] == "岗位关闭"
    assert closed.json()["closed_at"] is not None


def test_closed_reason_must_be_fresh_when_entering_closed(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/applications",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "status": "applied",
            "closed_reason": "stale",
        },
    ).json()

    missing_reason = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "closed"},
    )
    closed = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "closed", "closed_reason": "岗位关闭"},
    )
    reopened = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "interview"},
    )

    assert created["closed_reason"] == ""
    assert missing_reason.status_code == 400
    assert closed.status_code == 200
    assert reopened.status_code == 400
    assert reopened.json() == {"error": "closed application cannot be reopened"}


def test_api_does_not_reuse_stale_closed_reason_when_entering_closed(tmp_path):
    data_dir = tmp_path / "data"
    session_factory = init_database(data_dir / "data.db")
    repo = ApplicationsRepository(session_factory)
    app = repo.create(
        ApplicationCreate(company_name="ByteDance", position_name="Backend", status="interview")
    )
    with session_factory() as session:
        session.execute(
            text("UPDATE applications SET closed_reason = 'stale reason' WHERE id = :id"),
            {"id": app.id},
        )
        session.commit()

    client = TestClient(create_app(data_dir=data_dir))
    missing_reason = client.put(f"/api/applications/{app.id}", json={"status": "closed"})
    with_reason = client.put(
        f"/api/applications/{app.id}",
        json={"status": "closed", "closed_reason": "岗位关闭"},
    )

    assert missing_reason.status_code == 400
    assert missing_reason.json() == {"error": "closed_reason is required when closing an application"}
    assert with_reason.status_code == 200
    assert with_reason.json()["closed_reason"] == "岗位关闭"


def test_deleted_application_is_hidden_from_reads_and_dashboard(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    kept = client.post(
        "/api/applications",
        json={"company_name": "Keep", "position_name": "Backend", "status": "applied"},
    ).json()
    deleted = client.post(
        "/api/applications",
        json={"company_name": "Delete", "position_name": "Frontend", "status": "offer"},
    ).json()

    response = client.delete(f"/api/applications/{deleted['id']}")
    listed = client.get("/api/applications")
    dashboard = client.get("/api/dashboard").json()

    assert response.status_code == 200
    assert response.json() == {"message": "Deleted"}
    assert client.get(f"/api/applications/{deleted['id']}").status_code == 404
    assert client.put(f"/api/applications/{deleted['id']}", json={"status": "interview"}).status_code == 404
    assert [item["id"] for item in listed.json()] == [kept["id"]]
    assert dashboard["total"] == 1
    assert all(item["id"] != deleted["id"] for items in dashboard["board"].values() for item in items)


def test_status_first_timestamps_are_set_once(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/applications",
        json={"company_name": "ByteDance", "position_name": "Backend", "status": "applied"},
    ).json()

    first_interview = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "interview"},
    ).json()
    client.put(f"/api/applications/{created['id']}", json={"status": "written_test"})
    second_interview = client.put(
        f"/api/applications/{created['id']}",
        json={"status": "interview"},
    ).json()

    assert created["first_applied_at"] is not None
    assert created["first_interview_at"] is None
    assert first_interview["first_interview_at"] is not None
    assert second_interview["first_interview_at"] == first_interview["first_interview_at"]


def test_update_application_preserves_existing_source(tmp_path):
    repo = ApplicationsRepository(init_database(tmp_path / "data.db"))
    created = repo.create(
        ApplicationCreate(company_name="ByteDance", position_name="Backend", source="cli")
    )
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.put(
        f"/api/applications/{created.id}",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "status": "offer",
        },
    )

    assert response.status_code == 200
    assert response.json()["source"] == "cli"


def test_dashboard_groups_by_status(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    client.post(
        "/api/applications",
        json={"company_name": "A", "position_name": "Backend", "status": "interview"},
    )
    client.post(
        "/api/applications",
        json={"company_name": "B", "position_name": "Frontend", "status": "offer"},
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["board"]["interview"]) == 1
    assert len(response.json()["board"]["offer"]) == 1
