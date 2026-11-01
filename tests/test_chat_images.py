from io import BytesIO

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from uuid import uuid4
import time

from offerpilot.api import create_app
from offerpilot.config import Config, AIProviderProfile, save_config, load_config
from offerpilot.models import ChatAttachment, ChatMessageAttachment
from offerpilot.ai import client as model_client

from offerpilot.chat_images import ChatImageStore, ImageInputError
from offerpilot.db import init_database


@pytest.fixture(autouse=True)
def stub_async_transport(monkeypatch):
    """Use each test's synchronous fake through the new controlled I/O path."""
    async def complete(**kwargs):
        result = model_client.completion(**kwargs)
        class Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(result)
                except StopIteration:
                    raise StopAsyncIteration from None

            async def aclose(self):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
        return Stream()
    monkeypatch.setattr(model_client, "acompletion", complete)


def picture(size=(80, 40), kind="PNG"):
    out = BytesIO()
    Image.new("RGB", size, "white").save(out, format=kind)
    return out.getvalue()


def test_upload_normalizes_and_verifies_immutable_content(tmp_path):
    store = ChatImageStore(init_database(tmp_path / "data.db"), tmp_path)
    ref = store.upload(picture(), context_type="workspace", context_ref="")
    assert ref.width == 80 and ref.height == 40
    assert store.read(ref).startswith(b"\x89PNG")
    assert "base64" not in str(ref.as_dict())
    store.path(ref.id).write_bytes(picture((40, 80)))
    with pytest.raises(ImageInputError, match="image_content_changed"):
        store.read(ref)


def test_rejects_invalid_animated_and_oversized_images(tmp_path):
    store = ChatImageStore(init_database(tmp_path / "data.db"), tmp_path)
    for content in (b"<svg></svg>", b"not a png", b"x" * (10 * 1024 * 1024 + 1)):
        with pytest.raises(ImageInputError):
            store.upload(content, context_type="workspace", context_ref="")
    out = BytesIO()
    Image.new("RGB", (10, 10), "red").save(
        out, format="WEBP", save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")], duration=100, loop=0,
    )
    with pytest.raises(ImageInputError, match="image_animation_unsupported"):
        store.upload(out.getvalue(), context_type="workspace", context_ref="")


def configure(tmp_path, *, vision=True, auth=False):
    save_config(tmp_path, Config(
        auth_enabled=auth, auth_token="test-token" if auth else "",
        providers=[AIProviderProfile(id="vision", model="test-vision", api_key="test-key",
                                     supports_vision=vision, context_window=131072, max_output_tokens=4096)],
        active_provider_id="vision",
    ))


def upload(client, **data):
    response = client.post("/api/chat/attachments", files={"file": ("test.png", picture(), "image/png")}, data=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_settings_vision_roundtrip_and_omitted_field_preservation(tmp_path):
    configure(tmp_path)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        profile = client.get("/api/settings").json()["providers"][0]
        assert profile["supports_vision"] is True
        profile.pop("supports_vision")
        assert client.put("/api/settings", json={"providers": [profile]}).status_code == 200
        assert load_config(tmp_path).active_provider().supports_vision is True
        profile["supports_vision"] = False
        assert client.put("/api/settings", json={"providers": [profile]}).status_code == 200
        assert client.get("/api/chat/image-capability").json()["enabled"] is False
        assert client.post("/api/chat/attachments", files={"file": ("x.png", picture())}).status_code == 422


def test_preview_requires_auth_and_does_not_expose_storage_path(tmp_path):
    configure(tmp_path, auth=True)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get("/api/chat/image-capability").status_code == 401
        client.headers["X-OfferPilot-Token"] = "test-token"
        ref = upload(client)
        assert not any(key in ref for key in ("path", "storage_key", "url"))
        response = client.get("/api/chat/attachments/" + ref["id"])
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        client.headers.pop("X-OfferPilot-Token")
        assert client.get("/api/chat/attachments/" + ref["id"]).status_code == 401


def fake_completion(calls):
    def complete(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([{"choices": [{"delta": {"content": "图片已读取"}}]}])
        return {"choices": [{"message": {"content": "图片已读取"}}]}
    return complete


def terminal(client, accepted):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        body = client.get("/api/pilot/runtime/v1/turns/" + accepted["turn_id"]).json()
        if body["state"] not in {"running", "queued", "accepted"}:
            return body
        time.sleep(0.03)
    pytest.fail(f"image turn did not finish: {body}")


def test_runtime_image_only_persists_and_followup_resends_history(tmp_path, monkeypatch):
    configure(tmp_path)
    calls = []
    monkeypatch.setattr(model_client, "completion", fake_completion(calls))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        ref = upload(client)
        second_ref = upload(client)
        refs = [ref, second_ref]
        payload = {"message": "", "image_attachment_ids": [item["id"] for item in refs], "request_id": str(uuid4())}
        response = client.post("/api/pilot/runtime/v1/turns", json=payload)
        assert response.status_code == 202, response.text
        accepted = response.json()
        done = terminal(client, accepted)
        assert done["state"] == "completed", done
        assert any(isinstance(m["content"], list) for m in calls[0]["messages"])
        conversation_id = accepted["conversation_id"]
        saved = client.get(f"/api/chat/conversations/{conversation_id}").json()
        assert saved[0]["image_attachments"] == refs
        timeline = client.get(f"/api/chat/conversations/{conversation_id}/presentation").json()
        assert any(item.get("image_attachments") == refs for item in timeline["items"])
        count = len(calls)
        replay = client.post("/api/pilot/runtime/v1/turns", json=payload)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert len(calls) == count
        assert client.post("/api/pilot/runtime/v1/turns", json={**payload,
            "image_attachment_ids": list(reversed(payload["image_attachment_ids"]))}).status_code == 409
        other = upload(client)
        assert client.post("/api/pilot/runtime/v1/turns", json={**payload, "image_attachment_ids": [other["id"]]}).status_code == 409
        follow = client.post("/api/pilot/runtime/v1/turns", json={
            "message": "刚才图片里有什么", "conversation_id": conversation_id, "request_id": str(uuid4()),
        })
        assert follow.status_code == 202, follow.text
        assert terminal(client, follow.json())["state"] == "completed"
        assert any(isinstance(m["content"], list) for m in calls[-1]["messages"])
    with TestClient(create_app(data_dir=tmp_path)) as restarted:
        saved = restarted.get(f"/api/chat/conversations/{conversation_id}").json()
        assert saved[0]["image_attachments"] == refs
        assert restarted.get("/api/chat/attachments/" + ref["id"]).status_code == 200


def test_gc_keeps_bound_images_and_collects_expired_drafts(tmp_path, monkeypatch):
    configure(tmp_path)
    monkeypatch.setattr(model_client, "completion", fake_completion([]))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        kept = upload(client)
        orphan = upload(client)
        response = client.post("/api/chat", json={"message": "看图", "image_attachment_ids": [kept["id"]], "request_id": str(uuid4())})
        assert response.status_code == 200, response.text
        store = ChatImageStore(init_database(tmp_path / "data.db"), tmp_path)
        assert store.collect(now=int(time.time()) + 86402) == 1
        assert store.path(kept["id"]).exists()
        assert not store.path(orphan["id"]).exists()
        with store.sessions() as session:
            assert len(list(session.scalars(select(ChatMessageAttachment)))) == 1
            assert session.get(ChatAttachment, kept["id"]).bound


def test_new_database_and_reopen_keep_image_migration(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    init_database(tmp_path / "data.db")
    with sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM schema_migrations WHERE version='0034_chat_image_messages'")) == 1


def test_gc_waits_for_admission_binding_and_keeps_the_accepted_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from offerpilot.models import Conversation, ChatMessage

    store = ChatImageStore(init_database(tmp_path / 'data.db'), tmp_path)
    ref = store.upload(picture(), context_type='workspace', context_ref='')
    started = Event()
    def collect():
        started.set()
        return store.collect(now=int(time.time()) + 86402)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.sessions() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            conversation = Conversation(title='图片受理与回收竞争')
            session.add(conversation)
            session.flush()
            message = ChatMessage(conversation_id=conversation.id, role='user', content='')
            session.add(message)
            session.flush()
            refs = store.resolve_in_session(session, (ref.id,), conversation)
            future = executor.submit(collect)
            assert started.wait(2)
            assert not future.done()
            store.bind_in_session(session, refs, conversation.id, message.id)
            session.commit()
        assert future.result(timeout=10) == 0
    assert store.path(ref.id).exists()


def test_image_scope_cannot_cross_conversations_and_deleted_preview_is_hidden(tmp_path, monkeypatch):
    configure(tmp_path)
    monkeypatch.setattr(model_client, "completion", fake_completion([]))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        application = client.post('/api/applications', json={'company_name': '图片归属测试', 'position_name': '工程师'}).json()
        ref = upload(client, context_type='application', context_ref=str(application['id']))
        first = client.post('/api/chat', json={'message': '', 'context_type': 'application',
            'context_ref': str(application['id']), 'image_attachment_ids': [ref['id']]}).json()
        draft = upload(client, conversation_id=str(first['conversation_id']))
        assert client.patch(f"/api/chat/conversations/{first['conversation_id']}",
            json={'context_type': 'workspace', 'context_ref': ''}).status_code == 200
        reused = client.post('/api/chat', json={'message': '上下文已清除，继续讨论原图',
            'conversation_id': first['conversation_id'], 'image_attachment_ids': [ref['id'], draft['id']]})
        assert reused.status_code == 200, reused.text
        second = client.post('/api/chat', json={'message': '你好'}).json()
        rejected = client.post('/api/chat', json={'message': '看图', 'conversation_id': second['conversation_id'], 'image_attachment_ids': [ref['id']]})
        assert rejected.status_code == 422
        assert rejected.json()['error_code'] == 'image_scope_mismatch'
        assert client.delete(f"/api/chat/conversations/{first['conversation_id']}").status_code == 200
        assert client.get('/api/chat/attachments/' + ref['id']).status_code == 404
        assert not (tmp_path / 'chat-images' / (ref['id'] + '.image')).exists()


def test_collector_runs_without_uploads_and_stops(tmp_path):
    from threading import Event
    from offerpilot.chat_images import ChatImageCollector
    store = ChatImageStore(init_database(tmp_path / 'data.db'), tmp_path)
    ran = Event()
    calls = []
    def collect():
        calls.append(1)
        if len(calls) >= 2:
            ran.set()
        return 0
    store.collect = collect
    worker = ChatImageCollector(store, interval_seconds=0.01)
    worker.start()
    assert ran.wait(timeout=2)
    worker.stop()
    assert not worker._thread.is_alive()


def test_images_use_only_vision_fallbacks_and_errors_are_content_free(tmp_path, monkeypatch):
    from offerpilot.ai.types import Message
    from offerpilot.ai.client import ConfiguredAIClient, ProviderCallError
    store = ChatImageStore(init_database(tmp_path / 'data.db'), tmp_path)
    ref = store.upload(picture(), context_type='workspace', context_ref='')
    primary = AIProviderProfile(id='main', model='vision-main', api_key='key', supports_vision=True, context_window=131072, max_output_tokens=4096)
    text_only = primary.model_copy(update=dict(id='text', model='text-only', supports_vision=False))
    vision = primary.model_copy(update=dict(id='fallback', model='vision-fallback'))
    client = ConfiguredAIClient(Config(providers=[primary, text_only, vision], active_provider_id='main', fallback_provider_ids=['text', 'fallback']), image_reader=store.read_for_model)
    calls = []
    def complete(**kwargs):
        calls.append(kwargs['model'])
        if kwargs['model'].endswith('vision-main'):
            raise RuntimeError('private data:image/png;base64,SECRET_IMAGE')
        return {'choices': [{'message': {'content': 'ok'}}]}
    monkeypatch.setattr(model_client, 'completion', complete)
    assert client.complete([Message('user', '', images=(ref,))], []).content == 'ok'
    assert calls == ['openai/vision-main', 'openai/vision-fallback']
    calls.clear()
    assert client.complete([Message('user', 'hello')], []).content == 'ok'
    assert calls == ['openai/vision-main', 'openai/text-only']
    def fail(**kwargs):
        raise RuntimeError('private data:image/png;base64,SECRET_IMAGE')
    monkeypatch.setattr(model_client, 'completion', fail)
    with pytest.raises(ProviderCallError) as error:
        client.complete([Message('user', '', images=(ref,))], [])
    assert 'SECRET_IMAGE' not in str(error.value)


def test_backup_contains_images_but_no_image_content_in_message_json(tmp_path, monkeypatch):
    import zipfile
    from offerpilot.api import _build_backup_archive
    configure(tmp_path)
    monkeypatch.setattr(model_client, 'completion', fake_completion([]))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        ref = upload(client)
        response = client.post('/api/chat', json={'message': '', 'image_attachment_ids': [ref['id']]})
        assert response.status_code == 200
        stored = client.get(f"/api/chat/conversations/{response.json()['conversation_id']}").text
        assert 'base64' not in stored and 'data:image' not in stored
    with zipfile.ZipFile(BytesIO(_build_backup_archive(tmp_path))) as archive:
        assert f"chat-images/{ref['id']}.image" in archive.namelist()


def test_omitted_history_images_restore_text_fallback_candidates(tmp_path, monkeypatch):
    configure(tmp_path)
    calls = []
    monkeypatch.setattr(model_client, 'completion', fake_completion(calls))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        ref = client.post('/api/chat/attachments', files={'file': ('large.png', picture((2500, 2500)), 'image/png')}).json()
        first = client.post('/api/chat', json={'message': '看图', 'image_attachment_ids': [ref['id']]})
        assert first.status_code == 200, first.text
        cfg = load_config(tmp_path)
        primary = cfg.active_provider()
        text_fallback = primary.model_copy(update={'id': 'text', 'model': 'text-fallback', 'supports_vision': False})
        small_vision = primary.model_copy(update={'id': 'small', 'model': 'small-vision', 'context_window': 32768})
        save_config(tmp_path, cfg.model_copy(update={'providers': [primary, text_fallback, small_vision],
            'fallback_provider_ids': ['text', 'small']}))
        calls.clear()
        def complete(**kwargs):
            calls.append(kwargs)
            if kwargs['model'].endswith('test-vision'):
                raise RuntimeError('primary unavailable')
            message = {'content': '本轮未读取原图'}
            return iter([{'choices': [{'delta': message}]}]) if kwargs.get('stream') else {'choices': [{'message': message}]}
        monkeypatch.setattr(model_client, 'completion', complete)
        follow = client.post('/api/chat', json={'message': '继续讨论', 'conversation_id': first.json()['conversation_id']})
        assert follow.status_code == 200, follow.text
        assert [call['model'] for call in calls] == ['openai/test-vision', 'openai/text-fallback']
        assert not any(isinstance(message['content'], list) for message in calls[-1]['messages'])
        assert any('历史图片' in message['content'] and '未提供原图' in message['content'] for message in calls[-1]['messages'])


def test_image_tool_proposal_survives_restart_and_requires_confirmation(tmp_path, monkeypatch):
    configure(tmp_path)
    image_calls = []
    def completion(**kwargs):
        has_image = any(isinstance(item.get('content'), list) for item in kwargs['messages'])
        if has_image:
            image_calls.append(kwargs['messages'])
        if has_image and len(image_calls) == 1:
            message = {'tool_calls': [{'index': 0, 'id': 'image-write', 'type': 'function',
                'function': {'name': 'update_application_status', 'arguments': '{"id":1,"status":"interview"}'}}]}
        else:
            message = {'content': '已处理'}
        return iter([{'choices': [{'delta': message}]}]) if kwargs.get('stream') else {'choices': [{'message': message}]}
    monkeypatch.setattr(model_client, 'completion', completion)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        response = client.post('/api/applications', json={'company_name': '图片通知测试', 'position_name': '工程师', 'status': 'applied'})
        assert response.status_code == 201
        ref = upload(client)
        response = client.post('/api/chat', json={'message': '把图片通知对应的投递状态改为面试', 'image_attachment_ids': [ref['id']]})
        assert response.status_code == 200, response.text
        pending = response.json()
        assert pending['type'] == 'confirmation_required'
        assert client.get('/api/applications/1').json()['status'] == 'applied'
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get('/api/applications/1').json()['status'] == 'applied'
        assert client.get('/api/chat/attachments/' + ref['id']).status_code == 200
        store = ChatImageStore(init_database(tmp_path / 'data.db'), tmp_path)
        assert store.collect(now=int(time.time()) + 86402) == 0
        response = client.post('/api/chat/confirm', json={'conversation_id': pending['conversation_id'],
            'confirmation_token': pending['pending_action']['confirmation_token'], 'approved': True})
        assert response.status_code == 200, response.text
        assert client.get('/api/applications/1').json()['status'] == 'interview'
        saved = client.get(f"/api/chat/conversations/{pending['conversation_id']}").json()
        assert saved[0]['image_attachments'] == [ref]
        assert len(image_calls) >= 2


@pytest.mark.parametrize("kind", ["timeout", "http"])
def test_private_image_error_preserves_safe_diagnostics(tmp_path, monkeypatch, kind):
    from offerpilot.ai.types import Message
    from offerpilot.ai.client import ConfiguredAIClient, ProviderCallError
    store = ChatImageStore(init_database(tmp_path / "data.db"), tmp_path)
    ref = store.upload(picture(), context_type="workspace", context_ref="")
    provider = AIProviderProfile(id="vision", model="vision", api_key="key",
                                 supports_vision=True, context_window=131072)
    client = ConfiguredAIClient(Config(providers=[provider], active_provider_id="vision"),
                                image_reader=store.read_for_model)
    class HttpFailure(Exception):
        status_code = 503
        request_id = "private-request-id"
    error = TimeoutError("SECRET_IMAGE") if kind == "timeout" else HttpFailure("SECRET_IMAGE")
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(model_client, "completion", fail)
    audit = []
    monkeypatch.setattr(model_client, "_try_audit_provider_result", lambda *args, **kwargs: audit.append(kwargs))
    with pytest.raises(ProviderCallError) as caught:
        client.complete([Message("user", "", images=(ref,))], [])
    diagnostic = caught.value.diagnostic
    assert diagnostic["failure_category"] == ("network_timeout" if kind == "timeout" else "provider_http_5xx")
    assert diagnostic["timeout"] is (kind == "timeout")
    assert diagnostic["http_status"] == (None if kind == "timeout" else 503)
    assert audit[0]["failure_category"] == diagnostic["failure_category"]
    assert "SECRET_IMAGE" not in str(caught.value) + str(diagnostic) + str(audit)
    assert "private-request-id" not in str(diagnostic) + str(audit)


@pytest.mark.parametrize("phase", ["open", "read"])
def test_private_async_stream_failure_redacts_and_closes(tmp_path, monkeypatch, phase):
    from offerpilot.ai.types import Message
    from offerpilot.ai.client import ConfiguredAIClient, ProviderCallError
    from offerpilot.ai.provider_stream import provider_control_scope
    store = ChatImageStore(init_database(tmp_path / "data.db"), tmp_path)
    ref = store.upload(picture(), context_type="workspace", context_ref="")
    provider = AIProviderProfile(id="vision", model="vision", api_key="key",
                                 supports_vision=True, context_window=131072)
    client = ConfiguredAIClient(Config(providers=[provider], active_provider_id="vision"),
                                image_reader=store.read_for_model)
    closed = []
    class Stream:
        emitted = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.emitted:
                self.emitted = True
                return {"choices": [{"delta": {"content": "safe"}}]}
            raise RuntimeError("data:image/png;base64,SECRET_IMAGE")
        async def aclose(self):
            closed.append(True)
    async def complete(**kwargs):
        if phase == "open":
            raise RuntimeError("data:image/png;base64,SECRET_IMAGE")
        return Stream()
    monkeypatch.setattr(model_client, "acompletion", complete)
    deltas = []
    with provider_control_scope(lambda: None):
        with pytest.raises(ProviderCallError) as caught:
            client.stream_complete([Message("user", "", images=(ref,))], [], deltas.append)
    assert "SECRET_IMAGE" not in str(caught.value) + str(caught.value.diagnostic)
    assert deltas == ([] if phase == "open" else ["safe"])
    assert closed == ([] if phase == "open" else [True])


@pytest.mark.parametrize("value", [None, "img_" + "1" * 32, ["https://example.com/private.png"],
    ["../../private"], ["img_" + "1" * 32] * 2, ["img_" + str(i) * 32 for i in range(5)]])
def test_image_ids_reject_external_paths_duplicates_and_excess(value):
    from offerpilot.image_contracts import image_ids
    with pytest.raises(ImageInputError):
        image_ids(value)


def test_image_message_and_call_byte_limits():
    from offerpilot.image_contracts import ImageReference, image_references, require_image_call_limits
    refs = tuple(ImageReference("img_" + str(i) * 32, "a" * 64, "image/png",
                                8 * 1024 * 1024, 100, 100) for i in range(3))
    with pytest.raises(ImageInputError, match="image_total_size_exceeded"):
        image_references(refs)
    with pytest.raises(ImageInputError, match="image_context_size_exceeded"):
        require_image_call_limits(refs)


def test_upload_strips_metadata_and_rejects_oversized_dimensions():
    from PIL.PngImagePlugin import PngInfo
    from offerpilot.chat_images import normalize_image
    source = BytesIO()
    metadata = PngInfo()
    metadata.add_text("private", "sensitive-metadata")
    Image.new("RGB", (10, 10), "white").save(source, format="PNG", pnginfo=metadata)
    content, _, _, _ = normalize_image(source.getvalue())
    assert b"sensitive-metadata" not in content
    with Image.open(BytesIO(content)) as image:
        assert not image.info
    with pytest.raises(ImageInputError, match="image_dimensions_exceeded"):
        normalize_image(picture((10001, 1)))


def test_image_migration_preserves_pre_image_text_history(tmp_path):
    from offerpilot.models import ChatMessage, Conversation
    db_path = tmp_path / 'data.db'
    sessions = init_database(db_path)
    with sessions() as session:
        conversation = Conversation(title='Legacy text conversation')
        session.add(conversation)
        session.flush()
        message = ChatMessage(conversation_id=conversation.id, role='user', content='Keep original text')
        session.add(message)
        session.commit()
        message_id = message.id
        # Reconstruct the previous schema only inside this synthetic test DB.
        session.execute(text('ALTER TABLE chat_messages DROP COLUMN image_refs_json'))
        session.execute(text("DELETE FROM schema_migrations WHERE version='0034_chat_image_messages'"))
        session.commit()
    upgraded = init_database(db_path)
    with upgraded() as session:
        message = session.get(ChatMessage, message_id)
        assert message.content == 'Keep original text'
        assert message.image_attachments == []
        assert session.scalar(text("SELECT count(*) FROM schema_migrations WHERE version='0034_chat_image_messages'")) == 1
