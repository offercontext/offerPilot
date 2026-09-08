from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from offerpilot.ai.types import Assistant, ToolCall
from offerpilot.api import create_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import Application, Conversation, WriteOperation
from offerpilot.review_readiness.candidates import project_readiness_candidates

from tests.review_readiness_support import seed_review_candidate


class _Model:
    def __init__(self, tool_name='update_application_status', args=None) -> None:
        self.calls = 0
        self.tool_name = tool_name
        self.args = args or {'id': 1, 'status': 'interview'}

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Assistant(tool_calls=[ToolCall('presentation-origin', self.tool_name, json.dumps(self.args))])
        raise AssertionError('Presentation and edited confirmation cannot call Provider')


def test_pending_to_receipt_preserves_item_identity_and_does_not_execute_on_read(tmp_path):
    model = _Model()
    app = create_app(data_dir=tmp_path, chat_model=model)
    with TestClient(app) as client:
        assert client.post('/api/applications', json={'company_name': '展示验收', 'position_name': '工程师', 'status': 'applied'}).status_code == 201
        proposed = client.post('/api/chat', json={'message': '修改状态', 'conversation_id': 0}).json()
        operation_id = proposed['pending_action']['operation_id']
        endpoint = f"/api/chat/conversations/{proposed['conversation_id']}/presentation"
        pending = client.get(endpoint)
        assert pending.status_code == 200
        assert pending.headers['cache-control'] == 'no-store'
        pending_card = next(item for item in pending.json()['items'] if item['kind'] == 'action')
        assert pending_card['item_id'] == f'agent_operation:{operation_id}'
        assert pending_card['action']['decision'] == 'undecided'
        assert pending_card['action']['execution'] == 'not_started'
        assert set(pending_card['action']['available_actions']) >= {'approve', 'reject'}
        confirmed = client.post('/api/chat/confirm', json={
            'conversation_id': proposed['conversation_id'], 'operation_id': operation_id,
            'confirmation_token': proposed['pending_action']['confirmation_token'],
            'approved': True, 'edited_args': {'status': 'offer'},
        })
        assert confirmed.status_code == 200
        sql: list[str] = []
        def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
            sql.append(statement.lstrip().split()[0].upper())
        event.listen(app.state.db_engine, 'before_cursor_execute', capture_sql)
        first = client.get(endpoint).json()
        second = client.get(endpoint).json()
        event.remove(app.state.db_engine, 'before_cursor_execute', capture_sql)
        assert not set(sql) & {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}
        assert first == second
        card = next(item for item in first['items'] if item['kind'] == 'action')
        assert card['item_id'] == pending_card['item_id']
        assert card['action']['decision'] == 'modified'
        assert card['action']['execution'] == 'committed'
        assert card['action']['evidence'] == 'verified'
        assert card['action']['summary'] == confirmed.json()['message']
        assert card['action']['undo'] == 'available'
        assert model.calls == 1
        serialized = json.dumps(first)
        for private in ('confirmation_token', 'provider_blocks', 'edited_args', 'authorization_scope_fingerprint'):
            assert private not in serialized
        assert client.get('/api/applications/1').json()['status'] == 'offer'
        undone = client.post('/api/chat/undo-last-write', json={
            'conversation_id': proposed['conversation_id'], 'parent_operation_id': operation_id,
        })
        assert undone.status_code == 200
        after_undo = client.get(endpoint).json()
        undo_card = next(item['action'] for item in after_undo['items'] if item['kind'] == 'action')
        assert undo_card['execution'] == 'committed'
        assert undo_card['undo'] == 'undone'
        assert 'undo' not in undo_card['available_actions']
        assert model.calls == 1


@pytest.mark.parametrize('conversation_id', [1, 999])
def test_presentation_does_not_create_missing_conversations(tmp_path, conversation_id):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get(f'/api/chat/conversations/{conversation_id}/presentation').status_code == 404
        assert client.get('/api/chat/conversations').json() == []


def test_legacy_pending_uses_its_original_read_only_presentation_port(tmp_path):
    model = _Model()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model, title_model=model)) as client:
        client.post('/api/applications', json={'company_name': '展示验收', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={
            'message': '保存 JD：职位：后端工程师\n负责 API 设计', 'conversation_id': 0,
            'context_type': 'application', 'context_ref': '1',
        }).json()
        assert proposed['pending_action']['tool_name'] == 'save_application_jd_version'
        before = model.calls
        action = next(item['action'] for item in client.get(
            f"/api/chat/conversations/{proposed['conversation_id']}/presentation"
        ).json()['items'] if item['kind'] == 'action')
        assert action['decision'] == 'undecided'
        assert set(action['available_actions']) >= {'approve', 'modify', 'reject'}
        assert model.calls == before


@pytest.mark.parametrize(('tool_name', 'args', 'edits'), [
    ('create_application', {'company_name': '新公司', 'position_name': '工程师'}, {'position_name': '架构师'}),
    ('create_application_event', {'application_id': 1, 'event_type': 'interview', 'scheduled_at': '2026-09-10T09:00:00Z', 'duration_minutes': 60, 'location': '会议室'}, {'location': '线上'}),
    ('add_note', {'application_id': 1, 'company': '展示验收', 'position': '工程师', 'date': '2026-09-08', 'self_reflection': '测试复盘'}, {'self_reflection': '先说结论'}),
    ('create_offer', {'application_id': 1, 'base_monthly': 5000, 'months_per_year': 12}, {'base_monthly': 6000}),
])
def test_required_undo_display_checks_exact_created_sources(tmp_path, tool_name, args, edits):
    model = _Model(tool_name, args)
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        client.post('/api/applications', json={'company_name': '展示验收', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={'message': 'hello', 'conversation_id': 0}).json()
        assert 'pending_action' in proposed, proposed
        pending = proposed['pending_action']
        assert client.post('/api/chat/confirm', json={
            'conversation_id': proposed['conversation_id'], 'operation_id': pending['operation_id'],
            'confirmation_token': pending['confirmation_token'], 'approved': True, 'edited_args': edits,
        }).status_code == 200
        endpoint = f"/api/chat/conversations/{proposed['conversation_id']}/presentation"
        action = next(item['action'] for item in client.get(endpoint).json()['items'] if item['kind'] == 'action')
        assert action['execution'] == 'committed'
        assert action['undo'] == 'available'
        assert client.post('/api/chat/undo-last-write', json={
            'conversation_id': proposed['conversation_id'], 'parent_operation_id': pending['operation_id'],
        }).status_code == 200
        undone = next(item['action'] for item in client.get(endpoint).json()['items'] if item['kind'] == 'action')
        assert undone['execution'] == 'committed'
        assert undone['undo'] == 'undone'
        assert model.calls == 1


@pytest.mark.parametrize('damage', ['source_deleted', 'scope_changed', 'pending_changed', 'payload_changed', 'delivery_missing'])
def test_stale_sources_and_missing_evidence_never_restore_commands(tmp_path, damage):
    model = _Model()
    with TestClient(create_app(data_dir=tmp_path, chat_model=model)) as client:
        client.post('/api/applications', json={'company_name': '展示验收', 'position_name': '工程师', 'status': 'applied'})
        proposed = client.post('/api/chat', json={'message': '修改状态', 'conversation_id': 0}).json()
        pending = proposed['pending_action']
        conversation_id = proposed['conversation_id']
        if damage in {'payload_changed', 'delivery_missing'}:
            assert client.post('/api/chat/confirm', json={
                'conversation_id': conversation_id, 'operation_id': pending['operation_id'],
                'confirmation_token': pending['confirmation_token'], 'approved': True,
                'edited_args': {'status': 'offer'},
            }).status_code == 200
        factory = session_factory_for_data_dir(tmp_path)
        with factory.begin() as session:
            conversation = session.get(Conversation, conversation_id)
            operation = session.get(WriteOperation, pending['operation_id'])
            if damage == 'source_deleted':
                from datetime import datetime, timezone
                session.get(Application, 1).deleted_at = datetime.now(timezone.utc)
            elif damage == 'scope_changed':
                conversation.context_type = 'application'
                conversation.context_ref = '1'
                conversation.scope_revision += 1
            elif damage == 'pending_changed':
                conversation.pending_args = '{"id":1,"status":"offer"}'
            elif damage == 'payload_changed':
                # Deliberate corruption in this isolated test database only.
                session.execute(text('DROP TRIGGER trg_write_operation_terminal_immutable'))
                operation.visible_result = 'untrusted result'
            else:
                session.execute(text('DROP TRIGGER trg_write_operation_delivery_immutable'))
                operation.delivery_manifest_sha256 = 'sha256:' + '0' * 64
        first = client.get(f'/api/chat/conversations/{conversation_id}/presentation').json()
        second = client.get(f'/api/chat/conversations/{conversation_id}/presentation').json()
        assert first == second
        action = next(item['action'] for item in first['items'] if item['kind'] == 'action')
        assert not set(action['available_actions']) & {'approve', 'modify'}
        if damage == 'delivery_missing':
            assert action['execution'] == 'committed'
            assert action['evidence'] == 'verified'
            assert action['decision'] == 'modified'
        elif damage == 'payload_changed':
            assert action['execution'] == 'unknown'
            assert action['evidence'] == 'unavailable'
        else:
            assert action['execution'] != 'committed'
        assert model.calls == 1


@pytest.mark.parametrize('decision', ['approve', 'modify', 'reject', 'corrupt_input'])
def test_product_action_presentation_reads_real_bundle_and_receipt(tmp_path, decision):
    session_factory = session_factory_for_data_dir(tmp_path)
    seeded = seed_review_candidate(session_factory)
    with session_factory() as session:
        candidate = project_readiness_candidates(
            seeded["note_id"], seeded["proposal_id"], session
        ).candidates[0]

    with TestClient(create_app(data_dir=tmp_path)) as client:
        proposed = client.post(
            f"/api/interview-notes/{seeded['note_id']}/readiness-focus-actions",
            json={
                "proposal_id": seeded["proposal_id"],
                "focus_id": seeded["focus_id"],
                "expected_note_revision": seeded["note_revision"],
                "expected_candidate_fingerprint": candidate.candidate_fingerprint,
                "idempotency_key": str(uuid4()),
                "user_note": "下次先给结论。",
            },
        )
        assert proposed.status_code == 201, proposed.json()
        operation_id = proposed.json()["operation_id"]
        pending = client.get(f"/api/product-actions/{operation_id}/presentation")
        assert pending.status_code == 200, pending.json()
        assert pending.headers["cache-control"] == "no-store"
        assert pending.json()["decision"] == "undecided"
        assert pending.json()["execution"] == "not_started"
        assert set(pending.json()["available_actions"]) == {
            "approve",
            "modify",
            "reject",
        }

        terminal = client.post(
            f"/api/product-actions/{operation_id}/decisions",
            json={
                "confirmation_token": proposed.json()["confirmation_token"],
                "decision": 'approve' if decision == 'corrupt_input' else decision,
                **({'edited_payload': {'user_note': '新的准备重点'}} if decision == 'modify' else {}),
            },
        )
        assert terminal.status_code == 200, terminal.json()
        if decision == 'corrupt_input':
            with session_factory.begin() as session:
                session.execute(text('DROP TRIGGER trg_write_operation_terminal_immutable'))
                session.get(WriteOperation, operation_id).input_fingerprint = 'hmac-sha256:' + '0' * 64
            assert client.get(f'/api/product-actions/{operation_id}').status_code == 503
            assert client.get(f'/api/product-actions/{operation_id}/presentation').status_code == 503
            return
        presented = client.get(f"/api/product-actions/{operation_id}/presentation")
        assert presented.status_code == 200, presented.json()
        payload = presented.json()
        assert payload['decision'] == {'approve': 'approved', 'modify': 'modified', 'reject': 'rejected'}[decision]
        if decision == 'reject':
            assert payload['execution'] == 'not_started'
            assert payload['available_actions'] == []
            return
        assert payload["execution"] == "committed"
        assert payload["evidence"] == "verified"
        assert payload["summary"] == "已保存为下次准备重点。"
        assert payload["undo"] == "available"
        assert payload["available_actions"] == ["undo"]

        undone = client.post(
            f"/api/applications/{seeded['application_id']}/readiness-signals/"
            f"{terminal.json()['result']['signal_id']}/undo",
            json={"parent_operation_id": operation_id},
        )
        assert undone.status_code == 200, undone.json()
        after_undo = client.get(
            f"/api/product-actions/{operation_id}/presentation"
        )
        assert after_undo.status_code == 200, after_undo.json()
        assert after_undo.json()["undo"] == "undone"
        assert after_undo.json()["available_actions"] == []
