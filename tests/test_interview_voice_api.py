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
            return {"ready": True, "protocol": "offerpilot-voice-v1"}
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
    granted = client.post("/api/interview-voice/sessions", json={
        **context, "attempt_id": attempt, "turn_no": 1, "request_key": "grant",
        "consent": True, "consent_revision": "interview-realtime-v1",
    })
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


@pytest.mark.parametrize("url", ["https://remote.example", "http://127.0.0.1.evil/", "http://localhost/path", "http://user:pw@localhost"])
def test_runtime_proxy_never_targets_nonlocal_or_credentialed_urls(monkeypatch, url):
    monkeypatch.setenv("OFFERPILOT_VOICE_RUNTIME_URL", url)
    with pytest.raises(ValueError):
        routes.runtime_url()
