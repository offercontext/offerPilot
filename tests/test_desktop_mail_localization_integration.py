"""Offline regressions for the merged desktop and private-mail boundaries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import Config, load_config, save_config
from offerpilot.desktop import TOKEN_HEADER, DesktopSecurity, create_desktop_app
from offerpilot.job_mail.model_adapter import OfflineRuleExtractor
from offerpilot.job_mail.transport import FakeIMAPTransport


@pytest.mark.parametrize("web_token", ["web-only-token", ""])
def test_trusted_transport_bypasses_web_login_but_never_mail_origin(tmp_path, web_token):
    save_config(tmp_path, Config(auth_enabled=True, auth_token=web_token))
    transport = FakeIMAPTransport()
    extractor = OfflineRuleExtractor()
    app = create_app(
        data_dir=tmp_path,
        # Deliberately separate authentication from Origin validation so the
        # desktop outer middleware cannot mask a missing inner mail fence.
        transport_authenticator=lambda request: True,
        job_mail_transport=transport,
        job_mail_extractor=extractor,
        job_mail_local_setup_enabled=False,
    )
    assert app.state.job_mail_service.transport is transport
    assert app.state.job_mail_service.extractor is extractor
    assert app.state.job_mail_secure_setup.enabled is False

    with TestClient(app) as client:
        assert client.get("/api/auth/status").json() == {
            "auth_enabled": True, "authenticated": True,
        }
        assert client.get("/api/job-mail/status").status_code == 200
        same_origin = {"Origin": "http://testserver"}
        assert client.get("/api/job-mail/status", headers=same_origin).status_code == 200
        assert client.options("/api/job-mail/imports", headers=same_origin).status_code == 200
        allowed = client.post("/api/job-mail/imports", headers=same_origin, json={
            "subject": "面试邀请", "received_at": "2026-10-09T10:00:00Z",
            "body_text": "面试通知，时间待定",
        })
        assert allowed.status_code == 200
        assert len(allowed.json()["items"]) == 1

        invalid_origins = [
            [("Origin", origin)]
            for origin in ("https://foreign.example", "null", "", "http://testserver/")
        ] + [[("Origin", "http://testserver"), ("Origin", "https://foreign.example")]]
        for headers in invalid_origins:
            for method, path in (
                ("GET", "/api/job-mail/status"),
                ("POST", "/api/job-mail/imports"),
                ("POST", "/api/job-mail/disconnect"),
                ("OPTIONS", "/api/job-mail/imports"),
            ):
                response = client.request(method, path, headers=headers)
                assert response.status_code == 403
                assert response.json()["error_code"] == "job_mail_origin_denied"
        assert client.get("/api/job-mail/suggestions").json()["total"] == 1

    assert load_config(tmp_path).auth_enabled is True
    assert load_config(tmp_path).auth_token == web_token
    assert transport.read_calls == []


@pytest.mark.parametrize("web_token,expected_status", [("web-only-token", 401), ("", 503)])
def test_untrusted_transport_keeps_web_auth_precedence(tmp_path, web_token, expected_status):
    save_config(tmp_path, Config(auth_enabled=True, auth_token=web_token))
    with TestClient(create_app(
        data_dir=tmp_path, transport_authenticator=lambda request: False,
    )) as client:
        for headers in ({}, {"Origin": "http://testserver"}, {"Origin": "null"}):
            assert client.get("/api/job-mail/status", headers=headers).status_code == expected_status
        assert client.get("/api/auth/status").json()["authenticated"] is False
        if web_token:
            authenticated = {"Authorization": f"Bearer {web_token}"}
            assert client.get("/api/job-mail/status", headers=authenticated).status_code == 200
            rejected = client.get("/api/job-mail/status", headers={
                **authenticated, "Origin": "null",
            })
            assert rejected.status_code == 403
            assert rejected.json()["error_code"] == "job_mail_origin_denied"


@pytest.mark.parametrize("web_token", ["web-only-token", ""])
def test_desktop_mail_routes_keep_mandatory_outer_security(tmp_path, web_token):
    data_dir = tmp_path / "data"
    static_dir = tmp_path / "frontend"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>Desktop mail fixture</html>", encoding="utf-8")
    save_config(data_dir, Config(auth_enabled=True, auth_token=web_token))
    security = DesktopSecurity(token="a1" * 32, port=48271)
    app = create_desktop_app(data_dir=data_dir, static_dir=static_dir, security=security)
    headers = {TOKEN_HEADER: security.token}

    with TestClient(app, base_url=security.origin) as client:
        for method in ("GET", "POST", "OPTIONS"):
            assert client.request(method, "/api/job-mail/status").status_code == 401
        assert client.get("/api/job-mail/status", headers={
            "X-OfferPilot-Token": web_token,
        }).status_code == 401
        assert client.get("/api/job-mail/status", headers=headers).status_code == 200
        assert client.get("/api/job-mail/status", headers={
            **headers, "Origin": security.origin,
        }).status_code == 200
        for method, path in (
            ("GET", "/api/job-mail/status"),
            ("POST", "/api/job-mail/imports"),
            ("OPTIONS", "/api/job-mail/imports"),
        ):
            denied = client.request(method, path, headers={**headers, "Origin": "null"})
            assert denied.status_code == 403
            assert denied.json() == {"error": "desktop origin forbidden"}

    assert load_config(data_dir).auth_enabled is True
    assert load_config(data_dir).auth_token == web_token
