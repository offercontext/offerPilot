import pytest
from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import Config, save_config
from offerpilot.interview_voice import routes
from test_interview_practice_case_api import _QuickPracticeModel, _seed_resume
from test_mock_interview_api import _client


@pytest.fixture
def ready_runtime(monkeypatch):
    async def request(path, payload=None):
        if path == "/health":
            return {"ready": True, "protocol": "offerpilot-voice-v2"}
        return {"sdp": "answer", "type": "answer"}
    monkeypatch.setattr(routes, "runtime_request", request)


def start(tmp_path, quick=False):
    if quick:
        resume = _seed_resume(tmp_path)
        client = TestClient(create_app(data_dir=tmp_path, chat_model=_QuickPracticeModel()))
        case = client.post("/api/interview-practice-cases", json={
            "idempotency_key": "practice", "position_name": "后端工程师",
            "jd_text": "负责 Python 服务", "resume_id": resume,
        }).json()
        base = f"/api/interview-practice-cases/{case['id']}/mock-interview/attempts"
        context = {"context_kind": "quick_practice", "practice_case_id": case["id"]}
        source = {}
    else:
        client, app, event, resume = _client(tmp_path)
        base = f"/api/applications/{app}/events/{event}/mock-interview/attempts"
        context = {"context_kind": "application_event", "application_id": app, "event_id": event}
        source = {"resume_id": resume, "jd_version_id": 1}
    response = client.post(base, json={**source, "attempt_idempotency_key": "attempt",
                                     "initial_question_idempotency_key": "question"})
    assert response.status_code == 201, response.text
    attempt = response.json()["attempt_id"]
    return client, base, attempt, context


@pytest.mark.parametrize("quick", [False, True])
def test_both_contexts_auto_save_recover_and_report_provenance(tmp_path, ready_runtime, quick):
    client, base, attempt, context = start(tmp_path, quick)
    consent_payload = {
        **context, "attempt_id": attempt, "turn_no": 1, "request_key": "grant",
        "consent": True, "consent_revision": "interview-realtime-v1",
    }
    rejected = client.post("/api/interview-voice/sessions", json=consent_payload,
                           headers={"Origin": "https://untrusted.invalid", "Sec-Fetch-Site": "cross-site"})
    assert rejected.status_code == 403 and rejected.json()["error_code"] == "voice_origin_rejected"
    granted = client.post("/api/interview-voice/sessions", json=consent_payload,
                          headers={"Origin": "http://testserver"})
    assert granted.status_code == 200, granted.text
    grant = granted.json()
    path = f"{base}/{attempt}/turns"
    answer = {"turn_no": 1, "answer_text": "我会先拆解接口，再用指标验证稳定性。",
              "turn_idempotency_key": "answer", "input_source": "voice",
              "acceptance_method": "session_auto", "voice_session_id": grant["session_id"],
              "voice_session_epoch": grant["epoch"], "voice_session_token": grant["token"],
              "voice_consent_revision": grant["consent_revision"]}
    assert client.post(path, json=answer).status_code == 200
    access = {"token": grant["token"]}
    voice_base = f"/api/interview-voice/sessions/{grant['session_id']}"
    assert client.post(voice_base + "/close", json=access).status_code == 200
    assert client.post(path, json=answer).status_code == 200
    snapshot = client.post(voice_base + "/snapshot", json=access).json()
    assert snapshot["turns"][0]["acceptance_method"] == "session_auto"
    assert snapshot["turns"][0]["voice_session_epoch"] == grant["epoch"]
    assert grant["token"] not in str(snapshot)
    assert snapshot["active"] is False
    assert client.post(voice_base + "/offer", json={**access, "sdp": "offer", "type": "offer"}).status_code == 409
    assert client.post(path, json={**answer, "answer_text": "不同文字"}).status_code == 409


def test_inspection_capability_is_read_only_exact_path_and_lease_scoped(tmp_path, ready_runtime):
    client, _base, attempt, context = start(tmp_path)
    grant = client.post("/api/interview-voice/sessions", json={
        **context, "attempt_id": attempt, "turn_no": 1, "request_key": "grant",
        "consent": True, "consent_revision": "interview-realtime-v1",
    }).json()
    # Turn workspace auth on after seeding; the runtime never receives this workspace credential.
    save_config(tmp_path, Config(auth_enabled=True, auth_token="workspace-secret"))
    path = "/api/interview-voice/runtime/inspect"
    access = {"session_id": grant["session_id"], "token": grant["token"]}
    response = client.post(path, json=access)
    assert response.status_code == 200
    assert set(response.json()["turn"]) == {"turn_no", "question", "turn_status", "question_idempotency_key"}
    assert client.get(path).status_code == 401
    assert client.post(path + "/extra", json=access).status_code == 401
    assert client.post(path, json={**access, "token": "x" * 64}).status_code == 403
    assert client.post(f"/api/interview-voice/sessions/{grant['session_id']}/close", json={"token": grant["token"]}).status_code == 401
    response = client.post(path, json={**access, "unexpected": grant["token"]})
    assert response.status_code == 422
    assert grant["token"] not in response.text


def test_capabilities_fail_closed_and_answers_reject_forged_auto_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("OFFERPILOT_VOICE_RUNTIME_URL", raising=False)
    client, base, attempt, _context = start(tmp_path)
    assert client.get("/api/interview-voice/capabilities").json()["available"] is False
    for value in ("", "   "):
        response = client.post(f"{base}/{attempt}/turns", json={
            "turn_no": 1, "answer_text": value, "turn_idempotency_key": "key",
        })
        assert response.status_code == 422
    response = client.post(f"{base}/{attempt}/turns", json={
        "turn_no": 1, "answer_text": "伪造", "turn_idempotency_key": "key",
        "input_source": "voice", "acceptance_method": "session_auto",
    })
    assert response.status_code == 422


def test_v2_main_refuses_legacy_runtime_and_binds_v2_offer(tmp_path, monkeypatch):
    protocol = "offerpilot-voice-v1"

    async def request(path, payload=None):
        if path == "/health":
            return {"ready": True, "protocol": protocol}
        assert path == "/offer"
        assert payload["protocol"] == "offerpilot-voice-v2"
        return {"sdp": "answer", "type": "answer"}

    monkeypatch.setattr(routes, "runtime_request", request)
    client, _base, attempt, context = start(tmp_path)
    create = {
        **context, "attempt_id": attempt, "turn_no": 1, "request_key": "grant",
        "consent": True, "consent_revision": "interview-realtime-v1",
    }
    capabilities = client.get("/api/interview-voice/capabilities").json()
    assert capabilities["available"] is False
    assert "版本不匹配" in capabilities["reason"]
    assert client.post("/api/interview-voice/sessions", json=create).status_code == 503

    protocol = "offerpilot-voice-v2"
    assert client.get("/api/interview-voice/capabilities").json()["available"] is True
    grant = client.post("/api/interview-voice/sessions", json=create).json()
    offer = client.post(f"/api/interview-voice/sessions/{grant['session_id']}/offer", json={
        "token": grant["token"], "sdp": "offer", "type": "offer",
    })
    assert offer.status_code == 200


@pytest.mark.parametrize("url", ["https://remote.example", "http://127.0.0.1.evil/", "http://localhost/path", "http://user:pw@localhost"])
def test_runtime_proxy_never_targets_nonlocal_or_credentialed_urls(monkeypatch, url):
    monkeypatch.setenv("OFFERPILOT_VOICE_RUNTIME_URL", url)
    with pytest.raises(ValueError):
        routes.runtime_url()


def test_workspace_backup_keeps_accepted_answer_but_revokes_microphone_grant(tmp_path, ready_runtime):
    from contextlib import closing
    from io import BytesIO
    import sqlite3
    from zipfile import ZipFile

    from offerpilot import backup_restore
    from offerpilot.backup_archive import MANIFEST, workspace_files
    from tests.test_memory_metadata_backup import pack

    root = tmp_path / "live"
    client, base, attempt, context = start(root)
    grant = client.post("/api/interview-voice/sessions", json={
        **context, "attempt_id": attempt, "turn_no": 1, "request_key": "grant",
        "consent": True, "consent_revision": "interview-realtime-v1",
    }).json()
    answer = {"turn_no": 1, "answer_text": "保留已经保存的答案", "turn_idempotency_key": "answer",
              "input_source": "voice", "acceptance_method": "session_auto",
              "voice_session_id": grant["session_id"], "voice_session_epoch": grant["epoch"],
              "voice_consent_revision": grant["consent_revision"], "voice_session_token": grant["token"]}
    assert client.post(f"{base}/{attempt}/turns", json=answer).status_code == 200
    before = workspace_files(root)
    export = client.get("/api/backups/export")
    assert export.status_code == 200, export.text
    assert workspace_files(root) == before
    assert client.post(f"/api/interview-voice/sessions/{grant['session_id']}/snapshot",
                       json={"token": grant["token"]}).json()["active"]
    source = tmp_path / "exported"
    with ZipFile(BytesIO(export.content)) as archive:
        archive.extractall(source)  # trusted test-owned archive
    with closing(sqlite3.connect(source / "data.db")) as db:
        assert db.execute("SELECT state FROM interview_voice_sessions").fetchall() == [("revoked",)]
        assert db.execute("SELECT acceptance_method FROM mock_interview_answer_acceptance").fetchall() == [
            ("session_auto",),
        ]
        # Simulate a compatible older exporter that serialized a live grant.
        db.execute("UPDATE interview_voice_sessions SET state='active'")
        db.commit()
    assert (source / MANIFEST).is_file()
    candidate_zip = tmp_path / "candidate.zip"
    pack(source, candidate_zip)
    before = workspace_files(root)
    preview = client.post("/api/backups/restore/preview", content=candidate_zip.read_bytes())
    assert preview.status_code == 200, preview.text
    assert workspace_files(root) == before
    state = backup_restore.read_record(root)
    stage, _, _ = backup_restore.operation_paths(root, state)
    assert workspace_files(stage) == state["files"]
    with closing(sqlite3.connect(stage / "data.db")) as db:
        assert db.execute("SELECT state FROM interview_voice_sessions").fetchall() == [("revoked",)]
        assert db.execute("SELECT answer_text FROM mock_interview_turns").fetchall() == [(answer["answer_text"],)]
        assert db.execute("SELECT acceptance_method FROM mock_interview_answer_acceptance").fetchall() == [
            ("session_auto",),
        ]
