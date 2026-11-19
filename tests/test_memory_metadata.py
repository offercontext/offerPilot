from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from offerpilot.confirmed_memory.metadata import MemoryMetadata, temporal_status
from offerpilot.confirmed_memory.models import ConfirmedMemoryMutation, ConfirmedMemoryVersionMetadata
from offerpilot.confirmed_memory.repository import MemoryConflict, MemoryMutation
from tests.test_confirmed_memory import command, repository as memory_repository

repository = memory_repository


def test_legacy_fingerprint_replay_and_metadata_inheritance(repository):
    create = command()
    original = repository.mutate(None, create)
    # A pre-upgrade receipt includes every old default, but no metadata key.
    body = create.model_dump(mode="json", exclude={"metadata"}) | {"memory_id": None}
    fingerprint = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with repository.sessions() as session:
        receipt = session.get(ConfirmedMemoryMutation, str(create.mutation_id))
        assert receipt.fingerprint == fingerprint
        session.execute(delete(ConfirmedMemoryVersionMetadata))
        session.commit()
    assert repository.mutate(None, create) == original
    assert original["metadata"] == {"category": "unclassified", "valid_until": None, "evidence_status": "user_confirmed"}
    edited = repository.mutate(original["id"], command(1, metadata={"category": "goal", "valid_until": "2030-01-01T12:00:00+08:00"}))
    inherited = repository.mutate(original["id"], command(2))
    assert inherited["metadata"] == edited["metadata"]
    assert inherited["metadata"]["valid_until"] == "2030-01-01T04:00:00+00:00"
    cleared = repository.mutate(original["id"], command(3, metadata={"category": "unclassified", "valid_until": None}))
    assert cleared["temporal_status"] == "unknown"
    assert repository.get(original["id"])["versions"][1]["metadata"] == edited["metadata"]


def test_metadata_idempotency_cas_expiry_and_delete(repository):
    create = command(metadata={"category": "preparation", "valid_until": "2000-01-01T00:00:00Z"})
    item = repository.mutate(None, create)
    assert item["state"] == "active" and item["temporal_status"] == "expired"
    assert repository.list(active_only=True) == []
    assert len(repository.list()) == 1
    with pytest.raises(MemoryConflict):
        repository.mutate(None, MemoryMutation.model_validate(create.model_dump() | {"metadata": {"category": "skill", "valid_until": None}}))
    with pytest.raises(MemoryConflict):
        repository.mutate(item["id"], command(0, metadata={"category": "skill", "valid_until": None}))
    repository.mutate(item["id"], command(1, "withdraw", ""))
    reconfirmed = repository.mutate(item["id"], command(2))
    assert reconfirmed["temporal_status"] == "expired"  # confirmation never extends a deadline
    repository.mutate(item["id"], command(3, "delete", ""))
    with repository.sessions() as session:
        assert not session.scalars(select(ConfirmedMemoryVersionMetadata)).all()


@pytest.mark.parametrize("value", ["2030-01-01", "2030-01-01T12:00:00", "bad", 123, True])
def test_deadlines_reject_implicit_zone_or_coercion(value):
    with pytest.raises(ValidationError):
        MemoryMetadata(category="goal", valid_until=value)


def test_deadline_boundary():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert temporal_status("2030-01-01T00:00:00+00:00", now) == "expired"
    assert temporal_status("2030-01-01T00:00:01+00:00", now) == "current"
    assert temporal_status(None, now) == "unknown"


@pytest.mark.parametrize("metadata", [None, {"category": "goal", "valid_until": None, "evidence_status": "verified"}])
def test_metadata_explicit_null_and_evidence_spoof_rejected(metadata):
    with pytest.raises(ValidationError):
        command(metadata=metadata)


def test_nonconfirm_rejects_metadata():
    with pytest.raises(ValidationError):
        command(1, "withdraw", "", metadata={"category": "goal", "valid_until": None})


def test_corrupt_metadata_fails_closed_but_delete_can_erase(repository):
    item = repository.mutate(None, command())
    with repository.sessions() as session:
        record = session.scalar(select(ConfirmedMemoryVersionMetadata))
        record.category = ""
        session.commit()
    with pytest.raises(ValidationError):
        repository.get(item["id"])
    with pytest.raises(ValidationError):
        repository.mutate(item["id"], command(1, "withdraw", ""))
    assert repository.mutate(item["id"], command(1, "delete", ""))["state"] == "deleted"


@pytest.mark.parametrize("action,initial_state,current_version,request_version", [
    ("confirm", "active", 1, 0), ("confirm", "active", 2, 1),
    ("withdraw", "withdrawn", 2, 1), ("delete", "deleted", 2, 1),
])
def test_preupgrade_receipts_replay_all_actions(repository, action, initial_state, current_version, request_version):
    from offerpilot.confirmed_memory.models import ConfirmedMemory, ConfirmedMemoryVersion
    memory_id = "11111111-1111-4111-8111-111111111111"
    mutation_id = "22222222-2222-4222-8222-222222222222"
    content = "旧版记录" if action == "confirm" else ""
    target = None if request_version == 0 else memory_id
    # Construct a persisted old-schema receipt directly, without calling the new writer.
    payload = {"mutation_id": mutation_id, "action": action, "expected_version": request_version,
               "confirmed": True, "content": content}
    body = payload | {"memory_id": target}
    fingerprint = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with repository.sessions() as session:
        session.add(ConfirmedMemory(id=memory_id, current_version=current_version, state=initial_state))
        session.flush()
        session.add(ConfirmedMemoryVersion(id="33333333-3333-4333-8333-333333333333", memory_id=memory_id,
                    version=current_version if action == "confirm" else 1,
                    content="" if action == "delete" else "旧版记录", confirmed_at="2000-01-01T00:00:00+00:00"))
        session.add(ConfirmedMemoryMutation(id=mutation_id, memory_id=memory_id, fingerprint=fingerprint, version=current_version))
        session.commit()
    saved = repository.mutate(target, MemoryMutation.model_validate(payload))
    assert saved["state"] == initial_state and saved["current_version"] == current_version
    if action != "delete":
        assert saved["metadata"]["category"] == "unclassified"
        assert saved["temporal_status"] == "unknown"


def test_out_of_range_utc_deadlines_http_rejected_without_private_echo(tmp_path):
    from fastapi.testclient import TestClient
    from offerpilot.api import create_app
    from uuid import uuid4
    client = TestClient(create_app(data_dir=tmp_path))
    for deadline in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
        response = client.post("/api/confirmed-memory", json={
            "mutation_id": str(uuid4()), "action": "confirm", "expected_version": 0,
            "confirmed": True, "content": "metadata-private-sentinel",
            "metadata": {"category": "goal", "valid_until": deadline},
        })
        assert response.status_code == 422
        assert "metadata-private-sentinel" not in response.text
        assert client.get("/api/confirmed-memory").json() == []


@pytest.mark.parametrize("content", ["周三下午方便联系", "目标是求职，项目经历包含技能练习，近期准备复习"])
def test_legacy_reads_never_backfill_categories_and_user_correction_versions(repository, content):
    original = repository.mutate(None, command(content=content))
    # Reproduce an actual pre-metadata confirmed version, not a default metadata row.
    with repository.sessions() as session:
        session.execute(delete(ConfirmedMemoryVersionMetadata))
        session.commit()
    for _ in range(2):
        assert repository.list()[0]["metadata"]["category"] == "unclassified"
        view = repository.get(original["id"])
        assert view["metadata"]["category"] == "unclassified"
        assert view["versions"][0]["metadata"]["category"] == "unclassified"
    with repository.sessions() as session:
        assert not session.scalars(select(ConfirmedMemoryVersionMetadata)).all()
    correction = command(1, content=content, metadata={"category": "experience", "valid_until": None})
    corrected = repository.mutate(original["id"], correction)
    assert corrected["metadata"]["category"] == "experience"
    assert repository.mutate(original["id"], correction) == corrected
    history = repository.get(original["id"])["versions"]
    assert [v["metadata"]["category"] for v in history] == ["unclassified", "experience"]
    assert [v["content"] for v in history] == [content, content]
    with repository.sessions() as session:
        assert len(session.scalars(select(ConfirmedMemoryVersionMetadata)).all()) == 1
