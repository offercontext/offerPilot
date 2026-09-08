def _application(client):
    response = client.post(
        "/api/applications",
        json={"company_name": "星云数据", "position_name": "后端工程师"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _save(client, application_id, **overrides):
    payload = {
        "jd_text": "后端工程师负责 API 设计",
        "source_url": None,
        "expected_current_version_id": None,
        "idempotency_key": "jd-api-key-000001",
    }
    payload.update(overrides)
    return client.post(f"/api/applications/{application_id}/job-description/versions", json=payload)


def test_job_description_reads_have_current_history_and_detail_contract(app_client):
    application_id = _application(app_client)

    assert app_client.get(f"/api/applications/{application_id}/job-description").json() == {
        "current": None
    }
    assert app_client.get(
        f"/api/applications/{application_id}/job-description/versions"
    ).json() == []

    created = _save(app_client, application_id)
    assert created.status_code == 201
    version = created.json()
    assert version["source_kind"] == "ui"
    assert version["jd_text"] == "后端工程师负责 API 设计"

    current = app_client.get(f"/api/applications/{application_id}/job-description")
    assert current.status_code == 200
    assert current.json()["current"]["jd_text"] == version["jd_text"]

    history = app_client.get(
        f"/api/applications/{application_id}/job-description/versions"
    )
    assert history.status_code == 200
    assert "jd_text" not in history.json()[0]
    assert history.json()[0]["preview"] == version["jd_text"]

    detail = app_client.get(
        f"/api/applications/{application_id}/job-description/versions/{version['id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["jd_text"] == version["jd_text"]


def test_job_description_save_is_sync_and_replay_or_conflict_is_stable(app_client):
    application_id = _application(app_client)
    first = _save(app_client, application_id)
    assert first.status_code == 201

    replay = _save(app_client, application_id)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]

    conflict = _save(app_client, application_id, jd_text="不同内容")
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "application_jd_idempotency_conflict"

    stale = _save(
        app_client,
        application_id,
        idempotency_key="jd-api-key-000002",
        expected_current_version_id=999,
    )
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "application_jd_stale_current_version"
    assert stale.status_code != 202


def test_job_description_preview_uses_unicode_code_points_and_preserves_whitespace(app_client):
    application_id = _application(app_client)
    jd_text = ("\n\u7b71\u54f2\U0001f642 " * 60)[:300]
    created = _save(
        app_client,
        application_id,
        jd_text=jd_text,
        idempotency_key="jd-api-key-000002",
    )
    assert created.status_code == 201

    history = app_client.get(f"/api/applications/{application_id}/job-description/versions")
    assert history.status_code == 200
    assert history.json()[0]["preview"] == jd_text[:240] + "\u2026"
    assert len(history.json()[0]["preview"]) == 241


def test_job_description_post_does_not_trust_source_kind_and_validates_cas_types(app_client):
    application_id = _application(app_client)
    with_source = _save(app_client, application_id, source_kind="pilot")
    assert with_source.status_code in {400, 422}

    for index, value in enumerate((True, "1", 0, -1), start=2):
        response = _save(
            app_client,
            application_id,
            idempotency_key=f"jd-api-key-{index:06d}",
            expected_current_version_id=value,
        )
        assert response.status_code == 422


def test_job_description_detail_is_scoped_to_application(app_client):
    first_application_id = _application(app_client)
    second_application_id = _application(app_client)
    created = _save(app_client, first_application_id)
    version_id = created.json()["id"]

    response = app_client.get(
        f"/api/applications/{second_application_id}/job-description/versions/{version_id}"
    )
    assert response.status_code == 404
