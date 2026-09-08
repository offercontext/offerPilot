from __future__ import annotations

from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Application, ApplicationEvent, MockInterviewAttempt, MockInterviewTurn


class ForbiddenModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("voice coaching history must not call a provider")


def _seed(data_dir) -> tuple[int, int, int]:
    factory = session_factory_for_data_dir(data_dir)
    with factory() as session:
        application = Application(company_name="云栖智能", position_name="后端工程师")
        session.add(application)
        session.flush()
        event = ApplicationEvent(
            application_id=application.id,
            event_type="interview",
            subtype="technical",
            round=1,
            status="done",
        )
        session.add(event)
        session.flush()
        attempt = MockInterviewAttempt(
            application_id=application.id,
            event_id=event.id,
            resume_id=9,
            idempotency_key="voice-api-attempt-key",
            input_snapshot_json="{}",
            source_fingerprint="voice-api-source",
            attempt_status="active",
            transcript_fingerprint="",
        )
        session.add(attempt)
        session.flush()
        session.add(
            MockInterviewTurn(
                attempt_id=attempt.id,
                turn_no=1,
                question_idempotency_key="voice-api-question-key",
                turn_idempotency_key="voice-api-turn-key",
                question_text="请介绍一次线上故障排查。",
                answer_text="然后我先定位指标，再修复连接池。",
                answer_sha256="stored-answer-sha",
                turn_status="answered",
            )
        )
        session.commit()
        return application.id, event.id, attempt.id


def _body(**overrides):
    body = {
        "idempotency_key": "voice-api-save-key-0001",
        "total_duration_ms": 72_000,
        "voiced_duration_ms": 25_000,
        "pause_count": 1,
        "longest_pause_ms": 3_000,
        "speech_rate_cpm": 118,
        "filler_occurrences": [
            {"text": "然后", "count": 1, "transcript_offsets": [0]}
        ],
        "reflection_text": "下次先给结论。",
        "focus_kind": "long_pause_control",
        "origin_snapshot_id": None,
    }
    body.update(overrides)
    return body


def _turn_url(ids: tuple[int, int, int]) -> str:
    application_id, event_id, attempt_id = ids
    return (
        f"/api/applications/{application_id}/events/{event_id}/mock-interview/"
        f"attempts/{attempt_id}/turns/1/voice-coaching-snapshot"
    )


def test_voice_coaching_api_create_replay_list_trends_and_delete(tmp_path) -> None:
    model = ForbiddenModel()
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))
    ids = _seed(tmp_path)

    created = client.post(_turn_url(ids), json=_body())
    replay = client.post(_turn_url(ids), json=_body())
    fetched = client.get(_turn_url(ids))

    assert created.status_code == 201
    assert replay.status_code == 200
    assert fetched.status_code == 200
    assert replay.json()["id"] == created.json()["id"] == fetched.json()["id"]
    assert created.json()["question_text"] == "请介绍一次线上故障排查。"
    assert created.json()["confirmed_answer_text"] == "然后我先定位指标，再修复连接池。"
    assert created.json()["measurement_source"] == "local_browser_measurement"

    listed = client.get("/api/interview/voice-coaching/snapshots", params={"limit": 20})
    trends = client.get("/api/interview/voice-coaching/trends")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]
    assert trends.status_code == 200
    assert trends.json()["snapshot_count"] == 1
    assert trends.json()["metrics"]["longest_pause_ms"]["current_median"] == 3_000

    deleted = client.delete(
        f"/api/interview/voice-coaching/snapshots/{created.json()['id']}"
    )
    deleted_replay = client.delete(
        f"/api/interview/voice-coaching/snapshots/{created.json()['id']}"
    )
    assert deleted.status_code == 204
    assert deleted_replay.status_code == 204
    assert client.get(_turn_url(ids)).status_code == 404
    assert model.calls == 0


def test_voice_coaching_api_has_stable_validation_conflict_and_not_found_codes(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=ForbiddenModel()))
    ids = _seed(tmp_path)

    invalid = client.post(_turn_url(ids), json={"idempotency_key": "short"})
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "voice_coaching_invalid_payload"

    created = client.post(_turn_url(ids), json=_body())
    assert created.status_code == 201
    changed = client.post(_turn_url(ids), json=_body(longest_pause_ms=4_000))
    assert changed.status_code == 409
    assert changed.json()["error_code"] == "voice_coaching_idempotency_conflict"

    second_key = client.post(
        _turn_url(ids), json=_body(idempotency_key="voice-api-save-key-0002")
    )
    assert second_key.status_code == 409
    assert second_key.json()["error_code"] == "voice_coaching_snapshot_exists"

    missing = client.get(_turn_url((ids[0], ids[1], ids[2] + 999)))
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "voice_coaching_source_not_found"


def test_voice_coaching_api_rejects_unknown_fields_and_invalid_measurements(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=ForbiddenModel()))
    ids = _seed(tmp_path)

    for body in (
        _body(extra="not allowed"),
        _body(total_duration_ms=0),
        _body(voiced_duration_ms=80_000),
        _body(filler_occurrences=[{"text": "然后", "count": 1, "transcript_offsets": [4]}]),
    ):
        response = client.post(_turn_url(ids), json=body)
        assert response.status_code == 422
        assert response.json()["error_code"] == "voice_coaching_invalid_payload"


def test_attempt_discard_cannot_delete_an_immutable_voice_snapshot(tmp_path) -> None:
    client = TestClient(create_app(data_dir=tmp_path, chat_model=ForbiddenModel()))
    ids = _seed(tmp_path)
    created = client.post(_turn_url(ids), json=_body())
    assert created.status_code == 201

    application_id, event_id, attempt_id = ids
    discarded = client.delete(
        f"/api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}"
    )

    assert discarded.status_code == 409
    assert discarded.json()["error_code"] == "mock_interview_attempt_confirmed"
    fetched = client.get(_turn_url(ids))
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created.json()["id"]
