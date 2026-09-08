import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app


class JSONModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(
            content=json.dumps(
                {
                    "resume_advice": {
                        "summary": "Strong Go fit",
                        "highlights": ["Go"],
                        "rewrite_bullets": ["Built APIs"],
                        "gaps": [],
                        "notes": "",
                    },
                    "messages": [],
                    "checklist": [],
                }
            )
        )


def _create_ready_application(tmp_path) -> tuple[TestClient, dict[str, object], dict[str, object]]:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=JSONModel()))
    application = client.post(
        "/api/applications",
        json={
            "company_name": "Acme",
            "position_name": "Go Engineer",
            "job_url": "https://jobs.example.test/go",
            "status": "pending",
        },
    ).json()
    resume = client.post(
        "/api/resumes",
        json={"name": "Backend Resume", "text": "Built Go services"},
    ).json()
    jd_version = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "Build Go services",
            "source_url": None,
            "expected_current_version_id": None,
            "idempotency_key": "evidence-bundle-jd-0001",
        },
    )
    assert jd_version.status_code == 201
    material_kit = client.post(
        f"/api/applications/{application['id']}/material-kit/generate",
        json={"resume_id": resume["id"], "jd_version_id": jd_version.json()["id"]},
    )
    assert material_kit.status_code == 201
    return client, application, material_kit.json()


def _preview(client: TestClient, application_id: int) -> dict[str, object]:
    response = client.get(f"/api/applications/{application_id}/evidence-bundles/preview")
    assert response.status_code == 200
    return response.json()


def _confirm(
    client: TestClient,
    application_id: int,
    bundle_sha256: str,
    *,
    idempotency_key: str | None = None,
    submitted_at: str = "2026-07-14T01:02:03Z",
):
    return client.post(
        f"/api/applications/{application_id}/evidence-bundles",
        json={
            "submitted_at": submitted_at,
            "idempotency_key": idempotency_key or str(uuid4()),
            "expected_bundle_sha256": bundle_sha256,
        },
    )


def test_preview_confirm_and_detail_preserve_an_immutable_snapshot(tmp_path):
    client, application, material_kit = _create_ready_application(tmp_path)

    preview = _preview(client, int(application["id"]))
    assert preview["ready"] is True
    assert preview["issues"] == []
    assert preview["sources"]["application"] == {
        "id": application["id"],
        "company_name": "Acme",
        "position_name": "Go Engineer",
        "job_url": "https://jobs.example.test/go",
        "source": "web",
    }
    assert preview["sources"]["jd"]["characters"] == len("Build Go services")
    assert "character_count" not in preview["sources"]["jd"]
    assert preview["sources"]["resume"]["id"]
    assert preview["sources"]["resume"]["title"] == "Backend Resume"
    assert preview["sources"]["material_kit"]["id"] == material_kit["id"]
    assert "content_json" not in preview["sources"]["resume"]
    assert "content_json" not in preview["sources"]["material_kit"]

    confirmed = _confirm(client, int(application["id"]), str(preview["bundle_sha256"]))
    assert confirmed.status_code == 201
    bundle = confirmed.json()
    assert bundle["confirmation_kind"] == "user_asserted"
    assert "snapshot" in bundle

    updated = client.put(
        f"/api/material-kits/{material_kit['id']}",
        json={"content_json": {"body": "Changed"}},
    )
    assert updated.status_code == 200

    detail = client.get(
        f"/api/applications/{application['id']}/evidence-bundles/{bundle['id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["confirmation_kind"] == "user_asserted"
    assert detail.json()["snapshot"]["jd"]["text"] == "Build Go services"


def test_confirmation_replays_the_original_bundle_for_the_same_idempotency_key(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    preview = _preview(client, int(application["id"]))
    idempotency_key = str(uuid4())

    created = _confirm(
        client,
        int(application["id"]),
        str(preview["bundle_sha256"]),
        idempotency_key=idempotency_key,
    )
    replayed = _confirm(
        client,
        int(application["id"]),
        "stale-hash-is-ignored-for-a-replay",
        idempotency_key=idempotency_key,
    )

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json() == created.json()


def test_confirmation_replays_equivalent_uuid_spellings_with_one_bundle(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    app_id = int(application["id"])
    preview = _preview(client, app_id)
    idempotency_key = str(uuid4())

    created = _confirm(
        client,
        app_id,
        str(preview["bundle_sha256"]),
        idempotency_key=idempotency_key,
    )
    replayed = _confirm(
        client,
        app_id,
        "stale-hash-is-ignored-for-a-replay",
        idempotency_key=idempotency_key.upper(),
    )
    listed = client.get(f"/api/applications/{app_id}/evidence-bundles")

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json() == created.json()
    assert [item["sequence"] for item in listed.json()] == [1]


def test_stale_material_kit_update_is_rejected_before_confirmation(tmp_path):
    client, application, material_kit = _create_ready_application(tmp_path)
    preview = _preview(client, int(application["id"]))
    current = client.get(f"/api/applications/{application['id']}/job-description").json()
    current_id = current["current"]["id"]
    updated_jd = client.post(
        f"/api/applications/{application['id']}/job-description/versions",
        json={
            "jd_text": "Build Rust services",
            "source_url": None,
            "expected_current_version_id": current_id,
            "idempotency_key": "evidence-bundle-jd-0002",
        },
    )
    assert updated_jd.status_code == 201
    updated = client.put(
        f"/api/material-kits/{material_kit['id']}",
        json={"content_json": {"body": "Changed"}},
    )
    assert updated.status_code == 409

    response = _confirm(client, int(application["id"]), str(preview["bundle_sha256"]))

    assert response.status_code == 201


def test_confirmation_rejects_invalid_inputs_and_unready_sources(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    app_id = int(application["id"])
    preview = _preview(client, app_id)

    invalid_uuid = _confirm(client, app_id, str(preview["bundle_sha256"]), idempotency_key="not-a-uuid")
    future_timestamp = _confirm(
        client,
        app_id,
        str(preview["bundle_sha256"]),
        submitted_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    )
    missing_expected_hash = client.post(
        f"/api/applications/{app_id}/evidence-bundles",
        json={"idempotency_key": str(uuid4())},
    )
    missing_source_application = client.post(
        "/api/applications",
        json={"company_name": "No Kit", "position_name": "Backend"},
    ).json()
    unready_sources = _confirm(
        client,
        int(missing_source_application["id"]),
        "any-hash",
    )

    assert invalid_uuid.status_code == 422
    assert invalid_uuid.json() == {"error": "idempotency_key must be a UUID"}
    assert future_timestamp.status_code == 422
    assert future_timestamp.json() == {"error": "submitted_at cannot be in the future"}
    assert missing_expected_hash.status_code == 422
    assert missing_expected_hash.json() == {"error": "expected_bundle_sha256 is required"}
    assert unready_sources.status_code == 422


def test_confirmation_rejects_fractional_seconds_beyond_microseconds(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    app_id = int(application["id"])
    preview = _preview(client, app_id)

    response = _confirm(
        client,
        app_id,
        str(preview["bundle_sha256"]),
        submitted_at="2026-07-14T01:02:03.123456789Z",
    )

    assert response.status_code == 422
    assert response.json() == {"error": "submitted_at must be an RFC3339 timestamp"}


def test_confirmation_accepts_lowercase_rfc3339_separators(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    app_id = int(application["id"])
    preview = _preview(client, app_id)

    response = _confirm(
        client,
        app_id,
        str(preview["bundle_sha256"]),
        submitted_at="2026-07-14t01:02:03z",
    )

    assert response.status_code == 201
    assert response.json()["submitted_at"] == "2026-07-14T01:02:03Z"


def test_missing_hidden_and_wrong_nested_bundles_are_not_found(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    preview = _preview(client, int(application["id"]))
    bundle = _confirm(client, int(application["id"]), str(preview["bundle_sha256"])).json()
    other = client.post(
        "/api/applications",
        json={"company_name": "Other", "position_name": "Backend"},
    ).json()

    missing_preview = client.get("/api/applications/999/evidence-bundles/preview")
    wrong_nested = client.get(f"/api/applications/{other['id']}/evidence-bundles/{bundle['id']}")
    deleted = client.delete(f"/api/applications/{application['id']}")
    hidden_list = client.get(f"/api/applications/{application['id']}/evidence-bundles")

    assert missing_preview.status_code == 404
    assert wrong_nested.status_code == 404
    assert wrong_nested.json() == {"error": "Evidence bundle not found"}
    assert deleted.status_code == 200
    assert hidden_list.status_code == 404


def test_list_descends_by_sequence_and_detail_is_read_only(tmp_path):
    client, application, _material_kit = _create_ready_application(tmp_path)
    app_id = int(application["id"])
    first_preview = _preview(client, app_id)
    first = _confirm(client, app_id, str(first_preview["bundle_sha256"]))
    second_preview = _preview(client, app_id)
    second = _confirm(client, app_id, str(second_preview["bundle_sha256"]))
    assert first.status_code == 201
    assert second.status_code == 201
    second_bundle = second.json()

    listed = client.get(f"/api/applications/{app_id}/evidence-bundles")
    put = client.put(f"/api/applications/{app_id}/evidence-bundles/{second_bundle['id']}", json={})
    deleted = client.delete(f"/api/applications/{app_id}/evidence-bundles/{second_bundle['id']}")

    assert listed.status_code == 200
    assert [item["sequence"] for item in listed.json()] == [2, 1]
    assert all("snapshot" not in item for item in listed.json())
    assert put.status_code == 405
    assert deleted.status_code == 405
