from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from offerpilot.ai import client as model_client
from offerpilot.api import _redacted_backup_config, create_app
from offerpilot.backup_archive import stage_archive
from offerpilot.backup_export import export_archive
from tests.test_chat_images import (
    configure, fake_completion, stub_async_transport, terminal, upload,
)

# Reuse the existing offline async transport fixture; no real provider calls.
__all__ = ["stub_async_transport"]


def test_message_image_reads_from_independent_restored_directory(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    configure(source)
    calls = []
    monkeypatch.setattr(model_client, "completion", fake_completion(calls))
    with TestClient(create_app(data_dir=source)) as client:
        ref = upload(client)
        accepted_response = client.post("/api/pilot/runtime/v1/turns", json={
            "message": "", "image_attachment_ids": [ref["id"]], "request_id": str(uuid4()),
        })
        assert accepted_response.status_code == 202, accepted_response.text
        accepted = accepted_response.json()
        assert terminal(client, accepted)["state"] == "completed"
        conversation_id = accepted["conversation_id"]
        expected = client.get("/api/chat/attachments/" + ref["id"]).content
        backup = tmp_path / "backup.zip"
        backup.write_bytes(export_archive(source, _redacted_backup_config(source)))
    restored = tmp_path / "restored"
    stage_archive(backup, restored, source / "data.db")
    source.rename(tmp_path / "unavailable-source")
    count = len(calls)
    with TestClient(create_app(data_dir=restored)) as client:
        messages = client.get(f"/api/chat/conversations/{conversation_id}")
        assert messages.status_code == 200
        assert messages.json()[0]["image_attachments"] == [ref]
        image = client.get("/api/chat/attachments/" + ref["id"])
        assert image.status_code == 200
        assert image.content == expected
        assert (restored / "chat-images" / (ref["id"] + ".image")).read_bytes() == expected
    assert len(calls) == count
