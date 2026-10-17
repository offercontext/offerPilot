import mimetypes

import pytest
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


@pytest.mark.parametrize(
    ("filename", "content", "expected_type"),
    [
        ("app.js", b"export const synthetic = true;", "text/javascript"),
        ("ort-runtime.mjs", b"export default function synthetic() {}", "text/javascript"),
        ("ort-runtime.MJS", b"export default function synthetic() {}", "text/javascript"),
        ("ort-runtime.wasm", b"\x00asm\x01\x00\x00\x00", "application/wasm"),
    ],
)
def test_executable_static_types_ignore_host_mime_overrides(
    tmp_path, monkeypatch, filename, content, expected_type
):
    # Windows registry MIME entries can override Python's built-in mappings.
    # Pollute the same lookup used by FileResponse, without touching the registry.
    mimetypes.init()
    extension = "." + filename.rsplit(".", 1)[1].lower()
    monkeypatch.setitem(mimetypes.types_map, extension, "text/plain")
    assert mimetypes.guess_type(filename)[0] == "text/plain"
    dist = tmp_path / "web-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / filename).write_bytes(content)
    client = TestClient(create_app(data_dir=tmp_path / "data", static_dir=dist))

    response = client.get(f"/assets/{filename}")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].split(";", 1)[0] == expected_type
    assert mimetypes.guess_type(filename)[0] == "text/plain", "serving must not rewrite globals"


@pytest.mark.parametrize(
    "url",
    [
        "/%2e%2e/private.mjs",
        "/assets/%2e%2e/%2e%2e/private.wasm",
        "/assets/missing.mjs",
        "/applications/123",
    ],
)
def test_executable_suffix_does_not_change_static_root_or_spa_boundary(tmp_path, url):
    dist = tmp_path / "web-dist"
    dist.mkdir()
    index = b"<!doctype html><title>synthetic SPA fallback</title>"
    (dist / "index.html").write_bytes(index)
    (tmp_path / "private.mjs").write_bytes(b"must not leave the static root")
    (tmp_path / "private.wasm").write_bytes(b"must not leave the static root")
    client = TestClient(create_app(data_dir=tmp_path / "data", static_dir=dist))

    response = client.get(url)

    assert response.status_code == 200
    assert response.content == index
    assert response.headers["content-type"].split(";", 1)[0] == "text/html"


@pytest.mark.parametrize("url", ["/api", "/api/missing.mjs", "/api/assets/missing.wasm"])
def test_executable_suffix_does_not_turn_unknown_api_into_static_response(tmp_path, url):
    dist = tmp_path / "web-dist"
    (dist / "api" / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("synthetic SPA fallback", encoding="utf-8")
    (dist / "api" / "missing.mjs").write_text("export default 'must not serve';", encoding="utf-8")
    (dist / "api" / "assets" / "missing.wasm").write_bytes(b"must not serve")
    client = TestClient(create_app(data_dir=tmp_path / "data", static_dir=dist))

    response = client.get(url)

    assert response.status_code == 404
    assert response.json() == {"error": "not found"}
    assert response.headers["content-type"].split(";", 1)[0] == "application/json"
