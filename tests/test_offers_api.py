from fastapi.testclient import TestClient

from offerpilot.api import create_app


def test_offer_api_crud_and_total_cash(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    created_response = client.post(
        "/api/offers",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "base_monthly": 35000,
            "months_per_year": 16,
            "signing_bonus": 50000,
        },
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["status"] == "pending"
    assert created["total_cash"] == 35000 * 16 + 50000

    get_response = client.get(f"/api/offers/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["company_name"] == "ByteDance"

    updated_response = client.put(
        f"/api/offers/{created['id']}",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "status": "accepted",
            "base_monthly": 38000,
            "months_per_year": 16,
            "signing_bonus": 50000,
        },
    )

    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["status"] == "accepted"
    assert updated["total_cash"] == 38000 * 16 + 50000

    list_response = client.get("/api/offers")
    assert list_response.status_code == 200
    assert [offer["id"] for offer in list_response.json()] == [created["id"]]

    delete_response = client.delete(f"/api/offers/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}


def test_offer_api_validation_and_application_binding(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    missing_company = client.post(
        "/api/offers",
        json={"position_name": "Backend", "months_per_year": 12},
    )
    assert missing_company.status_code == 422
    assert missing_company.json() == {"error": "company_name is required"}

    negative_money = client.post(
        "/api/offers",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "base_monthly": -1,
            "months_per_year": 12,
        },
    )
    assert negative_money.status_code == 422
    assert negative_money.json() == {
        "error": "base_monthly and signing_bonus must be non-negative"
    }

    missing_app = client.post(
        "/api/offers",
        json={
            "company_name": "ByteDance",
            "position_name": "Backend",
            "months_per_year": 12,
            "application_id": 999,
        },
    )
    assert missing_app.status_code == 422
    assert missing_app.json() == {"error": "application not found"}


def test_offer_compare_requires_two_distinct_visible_offers_and_preserves_order(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    first = client.post(
        "/api/offers",
        json={"company_name": "A", "position_name": "Backend", "months_per_year": 12},
    ).json()
    second = client.post(
        "/api/offers",
        json={"company_name": "B", "position_name": "Backend", "months_per_year": 12},
    ).json()

    response = client.get(f"/api/offers/compare?ids={second['id']},{first['id']}")

    assert response.status_code == 200
    assert [offer["id"] for offer in response.json()] == [second["id"], first["id"]]

    duplicate = client.get(f"/api/offers/compare?ids={first['id']},{first['id']}")
    assert duplicate.status_code == 422
    assert duplicate.json()["error_code"] == "offer_comparison_requires_two_offers"

    missing = client.get(f"/api/offers/compare?ids={second['id']},999")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "offer_comparison_offer_not_found"

    missing_ids = client.get("/api/offers/compare")
    assert missing_ids.status_code == 400
    assert missing_ids.json() == {"error": "ids query param is required"}

    bad_id = client.get("/api/offers/compare?ids=abc")
    assert bad_id.status_code == 422
    assert bad_id.json()["error_code"] == "offer_comparison_invalid_ids"


def test_offer_update_keeps_application_id_immutable(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    app_a = client.post(
        "/api/applications",
        json={"company_name": "A", "position_name": "Backend"},
    ).json()
    app_b = client.post(
        "/api/applications",
        json={"company_name": "B", "position_name": "Backend"},
    ).json()
    offer = client.post(
        "/api/offers",
        json={
            "application_id": app_a["id"],
            "company_name": "A",
            "position_name": "Backend",
            "months_per_year": 12,
        },
    ).json()

    updated = client.put(
        f"/api/offers/{offer['id']}",
        json={
            "application_id": app_b["id"],
            "company_name": "A",
            "position_name": "Backend",
            "status": "accepted",
            "months_per_year": 12,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["application_id"] == app_a["id"]
