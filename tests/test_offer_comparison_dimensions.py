from __future__ import annotations

from fastapi.testclient import TestClient

from offerpilot.api import create_app


def _create_offer(client: TestClient, company: str) -> dict:
    response = client.post(
        "/api/offers",
        json={"company_name": company, "position_name": "Backend", "months_per_year": 12},
    )
    assert response.status_code == 201
    return response.json()


def test_dimensions_values_archive_and_clear_are_explicit(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    offer = _create_offer(client, "Nebula Data")

    blank_dimension = client.post("/api/offers/comparison-dimensions", json={"label": "  "})
    assert blank_dimension.status_code == 422
    assert blank_dimension.json()["error_code"] == "offer_comparison_dimension_label_required"

    created = client.post(
        "/api/offers/comparison-dimensions", json={"label": "通勤"}
    )
    assert created.status_code == 201
    dimension = created.json()
    assert dimension["label"] == "通勤"
    assert "score" not in dimension
    assert client.get("/api/offers/comparison-dimensions").json() == [dimension]

    blank_value = client.put(
        f"/api/offers/{offer['id']}/comparison-values/{dimension['id']}",
        json={"value_text": "  "},
    )
    assert blank_value.status_code == 422
    assert blank_value.json()["error_code"] == "offer_comparison_value_required"

    saved = client.put(
        f"/api/offers/{offer['id']}/comparison-values/{dimension['id']}",
        json={"value_text": "地铁 35 分钟"},
    )
    assert saved.status_code == 200
    assert saved.json()["value_text"] == "地铁 35 分钟"
    assert client.get(f"/api/offers/{offer['id']}/comparison-values").json() == [saved.json()]

    cleared = client.delete(
        f"/api/offers/{offer['id']}/comparison-values/{dimension['id']}"
    )
    assert cleared.status_code == 200
    assert cleared.json()["value_text"] is None
    assert client.delete(
        f"/api/offers/{offer['id']}/comparison-values/{dimension['id']}"
    ).status_code == 200
    client.put(
        f"/api/offers/{offer['id']}/comparison-values/{dimension['id']}",
        json={"value_text": "地铁 35 分钟"},
    )

    archived = client.patch(
        f"/api/offers/comparison-dimensions/{dimension['id']}",
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert client.get("/api/offers/comparison-dimensions").json() == []
    assert client.get(
        f"/api/offers/{offer['id']}/comparison-values"
    ).json()[0]["dimension_id"] == dimension["id"]


def test_structured_comparison_preserves_offer_order_and_missing_values(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    first = _create_offer(client, "A")
    second = _create_offer(client, "B")
    commute = client.post(
        "/api/offers/comparison-dimensions", json={"label": "通勤"}
    ).json()
    growth = client.post(
        "/api/offers/comparison-dimensions", json={"label": "成长空间"}
    ).json()
    client.put(
        f"/api/offers/{first['id']}/comparison-values/{commute['id']}",
        json={"value_text": "地铁 35 分钟"},
    )

    response = client.get(
        f"/api/offers/comparison?ids={second['id']},{first['id']}"
        f"&dimension_ids={growth['id']},{commute['id']}"
    )
    assert response.status_code == 200
    payload = response.json()
    assert [offer["id"] for offer in payload["offers"]] == [second["id"], first["id"]]
    assert [dimension["id"] for dimension in payload["dimensions"]] == sorted(
        [growth["id"], commute["id"]]
    )
    commute_read = next(item for item in payload["dimensions"] if item["id"] == commute["id"])
    assert commute_read["values"] == [
        {"offer_id": first["id"], "value_text": "地铁 35 分钟"},
        {"offer_id": second["id"], "value_text": None},
    ]
    assert {
        (item["offer_id"], item["label"])
        for item in payload["missing"]
    } == {
        (first["id"], "成长空间"),
        (second["id"], "成长空间"),
        (second["id"], "通勤"),
    }


def test_structured_comparison_rejects_duplicate_ninth_and_missing_ids(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    first = _create_offer(client, "A")
    second = _create_offer(client, "B")
    dimensions = [
        client.post(
            "/api/offers/comparison-dimensions", json={"label": f"维度 {index}"}
        ).json()
        for index in range(9)
    ]
    ids = ",".join(str(item["id"]) for item in dimensions)
    too_many = client.get(
        f"/api/offers/comparison?ids={first['id']},{second['id']}&dimension_ids={ids}"
    )
    assert too_many.status_code == 422
    assert too_many.json()["error_code"] == "offer_comparison_too_many_dimensions"

    duplicate = client.get(
        f"/api/offers/comparison?ids={first['id']},{second['id']}"
        f"&dimension_ids={dimensions[0]['id']},{dimensions[0]['id']}"
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error_code"] == "offer_comparison_invalid_dimensions"

    missing_offer = client.get(
        f"/api/offers/comparison?ids={first['id']},999&dimension_ids={dimensions[0]['id']}"
    )
    assert missing_offer.status_code == 404
    assert missing_offer.json()["error_code"] == "offer_comparison_offer_not_found"


def test_structured_comparison_route_is_not_consumed_by_dynamic_offer_route(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    first = _create_offer(client, "A")
    second = _create_offer(client, "B")
    response = client.get(f"/api/offers/comparison?ids={first['id']},{second['id']}")
    assert response.status_code == 200
    assert set(response.json()) == {"offers", "dimensions", "missing"}
