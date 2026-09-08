from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import Config, load_config, save_config


def test_auth_enabled_requires_bearer_token_for_api_routes(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token="secret-token"))
    client = TestClient(create_app(data_dir=tmp_path))

    unauthorized = client.get("/api/applications")
    authorized = client.get(
        "/api/applications",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"error": "unauthorized"}
    assert authorized.status_code == 200


def test_auth_enabled_accepts_offerpilot_token_header(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token="secret-token"))
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/applications", headers={"X-OfferPilot-Token": "secret-token"})

    assert response.status_code == 200


def test_auth_health_check_stays_public(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token="secret-token"))
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_status_is_public_and_reports_authentication(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True, auth_token="secret-token"))
    client = TestClient(create_app(data_dir=tmp_path))

    unauthenticated = client.get("/api/auth/status")
    authenticated = client.get(
        "/api/auth/status",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert unauthenticated.status_code == 200
    assert unauthenticated.json() == {"auth_enabled": True, "authenticated": False}
    assert authenticated.status_code == 200
    assert authenticated.json() == {"auth_enabled": True, "authenticated": True}


def test_auth_status_reports_disabled_auth_as_authenticated(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json() == {"auth_enabled": False, "authenticated": True}


def test_auth_enabled_without_token_reports_misconfigured(tmp_path):
    save_config(tmp_path, Config(auth_enabled=True))
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/api/applications")

    assert response.status_code == 503
    assert response.json() == {"error": "auth token is not configured"}


def test_put_settings_preserves_blank_auth_token(tmp_path):
    save_config(tmp_path, Config(auth_token="existing-token"))
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.put(
        "/api/settings",
        json={
            "chat_auto_approve_writes": False,
            "base_url": "https://example.test/v1",
            "model": "model",
            "auth_token": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["has_auth_token"] is True
    assert "auth_token" not in response.json()
    assert load_config(tmp_path).auth_token == "existing-token"
