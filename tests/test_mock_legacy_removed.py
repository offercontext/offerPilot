from fastapi.testclient import TestClient

from offerpilot.api import create_app


def test_legacy_mock_collection_and_item_routes_are_unavailable(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    assert client.get("/api/mock/sessions").status_code == 404
    assert client.post("/api/mock/sessions", json={"role": "工程师"}).status_code in {404, 405}
    assert client.get("/api/mock/sessions/1").status_code == 404
    assert client.post("/api/mock/sessions/1/end", json={}).status_code in {404, 405}
    assert client.delete("/api/mock/sessions/1").status_code in {404, 405}
