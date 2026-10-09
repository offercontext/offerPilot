from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from offerpilot.api import create_app
from offerpilot.config import Config, save_config


def import_mail(client):
    response = client.post("/api/job-mail/imports", json={
        "subject": "面试邀请", "sender": "hr@example.test",
        "received_at": "2026-10-09T10:00:00Z",
        "body_text": "面试时间 2026-10-20T10:00:00Z；时长60分钟\n公司：Example\n岗位：Engineer",
    })
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_api_import_preview_confirm_recovery_never_uses_chat_autoapprove(tmp_path):
    save_config(tmp_path, Config(chat_auto_approve_writes=True))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        application = client.post("/api/applications", json={
            "company_name": "Example", "position_name": "Engineer",
        }).json()
        item = import_mail(client)
        assert client.get("/api/application-events").json() == []
        base = {"operation_id": str(uuid4()), "suggestion_version": item["version"],
                "application_id": application["id"], "edited_fields": {"location": "Meeting room"}}
        endpoint = f'/api/job-mail/suggestions/{item["id"]}'
        no_preview = client.post(endpoint + "/confirm", json={
            **base, "preview_token": str(uuid4()), "explicit_confirmation": True,
        })
        assert no_preview.status_code == 422
        preview = client.post(endpoint + "/preview", json=base)
        assert preview.status_code == 200, preview.text
        payload = {**base, "preview_token": preview.json()["preview_token"]}
        assert client.post(endpoint + "/confirm", json=payload).status_code == 422
        assert client.get("/api/application-events").json() == []
        payload["explicit_confirmation"] = True
        confirmation = client.post(endpoint + "/confirm", json=payload)
        assert confirmation.status_code == 200, confirmation.text
        result = confirmation.json()
        assert result["replayed"] is False
        replay = client.post(endpoint + "/confirm", json=payload)
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        receipt = client.get(f'/api/job-mail/receipts/{base["operation_id"]}')
        assert receipt.json() == result
        assert len(client.get("/api/application-events").json()) == 1
        assert client.get(f'/api/applications/{application["id"]}').json()["status"] == "applied"


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/job-mail/status", None),
    ("get", "/api/job-mail/suggestions", None),
    ("get", "/api/job-mail/receipts/unknown", None),
    ("post", "/api/job-mail/imports", {"body_text": "private mail"}),
    ("post", "/api/job-mail/suggestions/unknown/confirm", {}),
    ("post", "/api/job-mail/connection", {}),
    ("put", "/api/job-mail/settings", {}),
])
def test_mail_routes_share_existing_auth_before_read_or_validation(tmp_path, method, path, body):
    save_config(tmp_path, Config(auth_enabled=True, auth_token="test-token"))
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert client.get("/api/job-mail/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    client.app.state.db_engine.dispose()
    if client.app.state.journal_db_engine is not None:
        client.app.state.journal_db_engine.dispose()


def test_unavailable_credentials_connection_fails_closed_without_echoing_input(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        status = client.get("/api/job-mail/status").json()
        assert status["capabilities"]["real_connection"] is False
        assert status["connection"] is None
        valid = client.post("/api/job-mail/connection", json={
            "provider": "qq", "email": "user@qq.com", "folders": ["INBOX"],
        })
        assert valid.status_code == 409
        assert valid.json()["error_code"] == "secure_connection_unavailable"
        unsafe = client.post("/api/job-mail/connection", json={
            "provider": "qq", "email": "user@qq.com", "folders": ["INBOX"],
            "authorization_code": "never-echo-this-secret",
        })
        assert unsafe.status_code == 422
        assert "never-echo-this-secret" not in unsafe.text
        assert client.get("/api/job-mail/status").json()["connection"] is None


@pytest.mark.parametrize("interval", [4, 1441, 15.5, True, "15"])
def test_settings_reject_invalid_interval_without_connection_or_side_effects(tmp_path, interval):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.put("/api/job-mail/settings", json={
            "sync_mode": "automatic", "interval_minutes": interval,
        })
        assert response.status_code == 422
        assert client.get("/api/job-mail/status").json()["connection"] is None


def test_mail_validation_rejects_tool_instructions_and_implicit_approval(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        item = import_mail(client)
        for extra in ({"chat_auto_approve_writes": True}, {"tool_calls": []}, {"status": "offer"}):
            response = client.post(f'/api/job-mail/suggestions/{item["id"]}/preview', json={
                "operation_id": str(uuid4()), "suggestion_version": 1, "application_id": 1,
                "edited_fields": {}, **extra,
            })
            assert response.status_code == 422
        assert client.get("/api/application-events").json() == []


def test_suggestion_pagination_reports_real_pending_counts(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        first = import_mail(client)
        for i in range(3):
            assert client.post("/api/job-mail/imports", json={
                "subject": f"面试邀请{i}", "sender": "hr@example.test",
                "received_at": "2026-10-09T10:00:00Z", "body_text": f"面试通知{i} 时间待定",
            }).status_code == 200
        client.post(f'/api/job-mail/suggestions/{first["id"]}/ignore', json={"suggestion_version": 1})
        page = client.get("/api/job-mail/suggestions?status=unprocessed&limit=2").json()
        assert len(page["items"]) == 2
        assert page["total"] == 3
        assert page["pending_count"] == 3
        assert page["has_more"] is True
        last = client.get("/api/job-mail/suggestions?status=unprocessed&limit=2&offset=2").json()
        assert len(last["items"]) == 1
        assert last["has_more"] is False
        history = client.get("/api/job-mail/suggestions?status=processed").json()
        assert history["total"] == 1
        assert history["pending_count"] == 3


def test_mail_origin_fence_blocks_cross_site_reads_and_writes_only_for_mail(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        item = import_mail(client)
        private_path = f'/api/job-mail/suggestions/{item["id"]}'
        for origin in ("https://evil.example", "null", "", "http://testserver.evil.example"):
            headers = {"Origin": origin}
            read = client.get(private_path, headers=headers)
            assert read.status_code == 403
            assert read.json()["error_code"] == "job_mail_origin_denied"
            assert "hr@example.test" not in read.text
            assert "Access-Control-Allow-Origin" not in read.headers
            write = client.post("/api/job-mail/imports", headers=headers, json={
                "subject": "different interview", "sender": "private@example.test",
                "received_at": "2026-10-09T10:00:00Z", "body_text": "面试邀请：跨站导入不得发生",
            })
            assert write.status_code == 403
            # Body-free state changes also need the mail origin fence.
            assert client.post("/api/job-mail/disconnect", headers=headers).status_code == 403
            assert client.options("/api/job-mail/imports", headers=headers).status_code == 403
        # Read and browser POST requests work for the actual application origin.
        same = {"Origin": "http://testserver"}
        assert client.get(private_path, headers=same).status_code == 200
        assert client.post("/api/job-mail/imports", headers=same, json={
            "subject": "same origin", "received_at": "2026-10-09T10:00:00Z",
            "body_text": "面试邀请，时间待定",
        }).status_code == 200
        assert client.get("/api/job-mail/suggestions").json()["total"] == 2
        assert client.options("/api/job-mail/imports", headers=same).status_code == 200
        # Preserve historical behavior outside this feature's prefix.
        assert client.get("/api/applications", headers={"Origin": "https://evil.example"}).status_code == 200


def test_mail_origin_fence_does_not_trust_forwarded_headers_or_override_auth(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        forged = {
            "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https", "Forwarded": "host=evil.example;proto=https",
        }
        assert client.get("/api/job-mail/status", headers=forged).status_code == 403
        assert client.get("/api/job-mail/status", headers=[
            ("Origin", "http://testserver"), ("Origin", "https://evil.example"),
        ]).status_code == 403
        save_config(tmp_path, Config(auth_enabled=True, auth_token="test-token"))
        # Auth remains mandatory and takes precedence, even for same-origin requests.
        assert client.get("/api/job-mail/status").status_code == 401
        assert client.get("/api/job-mail/status", headers={"Origin": "http://testserver"}).status_code == 401
        assert client.get("/api/job-mail/status", headers=forged).status_code == 401
        authenticated = {"Authorization": "Bearer test-token"}
        assert client.get("/api/job-mail/status", headers=authenticated).status_code == 200
        assert client.get("/api/job-mail/status", headers={**authenticated, "Origin": "http://testserver"}).status_code == 200
        assert client.get("/api/job-mail/status", headers={**authenticated, **forged}).status_code == 403
