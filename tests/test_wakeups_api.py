from fastapi.testclient import TestClient

from offerpilot.api import create_app


def test_create_and_list_wakeups(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    created = client.post(
        "/api/wakeups",
        json={
            "kind": "follow_up",
            "due_at": "2026-07-08T09:30:00Z",
            "payload": {"application_id": 7, "message": "follow up"},
        },
    )
    listed = client.get("/api/wakeups")

    assert created.status_code == 201
    assert created.json()["kind"] == "follow_up"
    assert created.json()["status"] == "pending"
    assert created.json()["payload"] == {"application_id": 7, "message": "follow up"}
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created.json()["id"]


def test_dispatch_due_wakeups_marks_only_due_items(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    due = client.post(
        "/api/wakeups",
        json={"kind": "review", "due_at": "2026-07-08T09:30:00Z", "payload": {"id": "due"}},
    ).json()
    future = client.post(
        "/api/wakeups",
        json={"kind": "review", "due_at": "2026-07-09T09:30:00Z", "payload": {"id": "future"}},
    ).json()

    dispatched = client.post("/api/wakeups/dispatch-due", json={"now": "2026-07-08T10:00:00Z"})
    repeated = client.post("/api/wakeups/dispatch-due", json={"now": "2026-07-08T10:00:00Z"})
    listed = client.get("/api/wakeups")

    assert dispatched.status_code == 200
    assert [item["id"] for item in dispatched.json()["dispatched"]] == [due["id"]]
    assert repeated.json()["dispatched"] == []
    statuses = {item["id"]: item["status"] for item in listed.json()}
    assert statuses[due["id"]] == "dispatched"
    assert statuses[future["id"]] == "pending"


def test_wakeup_validation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    missing_kind = client.post("/api/wakeups", json={"due_at": "2026-07-08T09:30:00Z"})
    bad_due_at = client.post("/api/wakeups", json={"kind": "follow_up", "due_at": "soon"})

    assert missing_kind.status_code == 400
    assert missing_kind.json() == {"error": "kind is required"}
    assert bad_due_at.status_code == 400
    assert bad_due_at.json() == {"error": "due_at must be RFC3339"}
