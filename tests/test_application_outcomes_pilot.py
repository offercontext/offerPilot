from __future__ import annotations

from fastapi.testclient import TestClient

from offerpilot.api import create_app


class NoProviderModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("deterministic application outcome flow must not call a Provider")


def _sources(client: TestClient):  # type: ignore[no-untyped-def]
    application = client.post(
        "/api/applications",
        json={"company_name": "云栖智能", "position_name": "AI 应用工程师"},
    ).json()
    resume = client.post(
        "/api/resumes",
        json={"title": "筱哲-岗位版", "content_json": {"raw_text": "五年 AI 产品经验"}},
    ).json()
    jd = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "负责 AI 应用设计与交付",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "jd-pilot-outcome-001",
        },
    ).json()
    return application, resume, jd


def _confirm(client: TestClient, pending: dict) -> object:
    return client.post(
        "/api/chat/confirm",
        json={
            "conversation_id": pending["conversation_id"],
            "confirmation_token": pending["pending_action"]["confirmation_token"],
            "approved": True,
        },
    )


def test_pilot_freezes_submission_snapshot_without_provider(tmp_path) -> None:
    model = NoProviderModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model, title_model=model))
    application, resume, jd = _sources(client)
    pending = client.post(
        "/api/chat",
        json={
            "message": "确认本次实际投递材料",
            "conversation_id": 0,
            "context_type": "application",
            "context_ref": str(application["id"]),
            "pilot_action": {
                "type": "application_submission_snapshot",
                "resumeId": resume["id"],
                "jdVersionId": jd["id"],
                "materialKitId": None,
                "submittedAt": "2026-08-12T09:30:00+08:00",
                "note": "官网投递",
            },
        },
    ).json()

    assert pending["pending_action"]["tool_name"] == "create_application_submission_snapshot"
    confirmed = _confirm(client, pending)

    assert confirmed.status_code == 200
    snapshots = client.get(
        f"/api/applications/{application['id']}/submission-snapshots"
    ).json()
    assert len(snapshots) == 1
    assert snapshots[0]["source_kind"] == "pilot"
    assert model.calls == 0


def test_pilot_records_outcome_and_rejection_writes_nothing(tmp_path) -> None:
    model = NoProviderModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model, title_model=model))
    application, resume, jd = _sources(client)
    snapshot = client.post(
        f"/api/applications/{application['id']}/submission-snapshots",
        json={
            "resume_id": resume["id"], "jd_version_id": jd["id"], "material_kit_id": None,
            "submitted_at": "2026-08-12T09:30:00Z", "note": "", "idempotency_key": "pilot-outcome-snap-01",
        },
    ).json()
    request = {
        "message": "记录本次面试结果",
        "conversation_id": 0,
        "context_type": "application",
        "context_ref": str(application["id"]),
        "pilot_action": {
            "type": "application_outcome_record",
            "snapshotId": snapshot["id"], "eventId": None, "stage": "interview", "result": "advanced",
            "feedbackText": "技术深度扎实", "reflectionText": "表达可更聚焦",
            "nextActionText": "用 STAR 重写案例", "feedbackTags": ["communication"],
            "occurredAt": "2026-08-12T15:00:00+08:00",
        },
    }
    pending = client.post("/api/chat", json=request).json()
    assert pending["pending_action"]["tool_name"] == "record_application_outcome"
    assert _confirm(client, pending).status_code == 200
    assert len(client.get(f"/api/applications/{application['id']}/outcomes").json()) == 1

    rejected = client.post("/api/chat", json={**request, "conversation_id": 0}).json()
    response = client.post(
        "/api/chat/confirm",
        json={
            "conversation_id": rejected["conversation_id"],
            "confirmation_token": rejected["pending_action"]["confirmation_token"],
            "approved": False,
            "rejection_feedback": "暂不记录",
        },
    )
    assert response.status_code == 200
    assert len(client.get(f"/api/applications/{application['id']}/outcomes").json()) == 1
    assert model.calls == 0
