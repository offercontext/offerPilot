from __future__ import annotations


def _sources(client):  # type: ignore[no-untyped-def]
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
            "idempotency_key": "jd-outcome-api-0001",
        },
    ).json()
    return application, resume, jd


def test_application_outcome_api_freezes_replays_and_summarizes(app_client) -> None:
    application, resume, jd = _sources(app_client)
    archive_payload = {
        "resume_id": resume["id"],
        "jd_version_id": jd["id"],
        "material_kit_id": None,
        "submitted_at": "2026-08-12T09:30:00+08:00",
        "note": "官网投递",
        "idempotency_key": "snapshot-api-key-0001",
    }
    url = f"/api/applications/{application['id']}/submission-snapshots"
    created = app_client.post(url, json=archive_payload)
    replay = app_client.post(url, json=archive_payload)

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    assert created.json()["source_kind"] == "ui"
    assert created.json()["source_states"] == {
        "resume": "current",
        "jd": "current",
        "material": "current",
    }

    outcome_payload = {
        "submission_snapshot_id": created.json()["id"],
        "application_event_id": None,
        "stage": "interview",
        "result": "advanced",
        "feedback_text": "技术深度扎实，表达可以更聚焦",
        "reflection_text": "案例展开过长",
        "next_action_text": "用 STAR 重写项目案例",
        "feedback_tags": ["communication", "technical_depth"],
        "occurred_at": "2026-08-12T15:00:00+08:00",
        "idempotency_key": "outcome-api-key-00001",
    }
    outcome_url = f"/api/applications/{application['id']}/outcomes"
    outcome = app_client.post(outcome_url, json=outcome_payload)

    assert outcome.status_code == 201
    assert outcome.json()["source_kind"] == "ui"
    assert app_client.get(outcome_url).json()[0]["feedback_tags"] == [
        "communication",
        "technical_depth",
    ]
    assert app_client.get(
        f"/api/applications/{application['id']}/outcome-summary"
    ).json()["feedback_tag_counts"] == {"communication": 1, "technical_depth": 1}


def test_application_outcome_api_has_stable_conflict_and_validation_codes(app_client) -> None:
    application, resume, jd = _sources(app_client)
    url = f"/api/applications/{application['id']}/submission-snapshots"
    payload = {
        "resume_id": resume["id"],
        "jd_version_id": jd["id"],
        "material_kit_id": None,
        "submitted_at": "2026-08-12T09:30:00Z",
        "note": "官网投递",
        "idempotency_key": "snapshot-api-key-0002",
    }
    assert app_client.post(url, json=payload).status_code == 201
    conflict = app_client.post(url, json={**payload, "note": "内推"})
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "application_archive_idempotency_conflict"

    invalid = app_client.post(url, json={**payload, "idempotency_key": "short"})
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "application_outcome_invalid_request"
