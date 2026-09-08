from fastapi.testclient import TestClient

from offerpilot.api import create_app


def test_serves_static_frontend_assets_and_spa_fallback(tmp_path):
    dist = tmp_path / "web-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><div id='root'></div></html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('offerpilot')", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path / "data", static_dir=dist))

    index_response = client.get("/")
    asset_response = client.get("/assets/app.js")
    fallback_response = client.get("/applications/123")
    api_response = client.get("/api/does-not-exist")

    assert index_response.status_code == 200
    assert "root" in index_response.text
    assert asset_response.status_code == 200
    assert "offerpilot" in asset_response.text
    assert fallback_response.status_code == 200
    assert "root" in fallback_response.text
    assert api_response.status_code == 404
