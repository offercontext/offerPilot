"""Synthetic business data, executed only against the archived, pinned master source."""
from datetime import datetime
from pathlib import Path
import sys

from offerpilot.ai.write_operations import build_terminal_payload, load_or_create_ledger_key
from offerpilot.db import init_database
from offerpilot.models import ApplicationEvent, ChatMessage, Conversation, WriteOperation
from offerpilot.repositories.application_creation import ApplicationCreationService
from offerpilot.repositories.applications import ApplicationCreate


def seed(data_dir: Path) -> None:
    sessions = init_database(data_dir / "data.db")
    result, replay = ApplicationCreationService(sessions).create(
        ApplicationCreate(company_name="Synthetic Upgrade Co", position_name="Engineer"),
        {"jd_text": "Synthetic existing JD, never fetched", "source_url": ""},
        "master-upgrade-creation-0001",
    )
    assert not replay
    key = load_or_create_ledger_key(data_dir, sessions)
    payload = build_terminal_payload(
        status="rejected", result_contract="rejection_json_v1",
        result={"rejected": True}, visible_result="Synthetic rejected confirmation",
        transport={}, undo=None, failure_category=None, failure_code=None,
    )
    now = datetime(2026, 10, 1, 12)
    fingerprint = "hmac-sha256:" + "0" * 64
    with sessions() as session:
        conversation = Conversation(title="Existing application conversation",
                                    context_type="application", context_ref=str(result["id"]))
        session.add(conversation)
        session.flush()
        session.add_all([
            ApplicationEvent(application_id=result["id"], event_type="interview",
                             notes="Existing interview", scheduled_at=now),
            ChatMessage(conversation_id=conversation.id, role="user",
                        content="Synthetic historical message", provider_blocks='[{"synthetic":true}]'),
            WriteOperation(
                id="11111111-1111-4111-8111-111111111111", operation_role="primary",
                tool_call_id="synthetic-rejected-call", tool_name="confirm_interview_story",
                adapter_kind="product_action", status="rejected", fingerprint_key_id=key.key_id,
                proposal_fingerprint=fingerprint, confirmation_token_fingerprint=fingerprint,
                authorization_scope_fingerprint=fingerprint,
                operation_request_fingerprint=fingerprint,
                result_contract=payload.result_contract, result_json=payload.result_json,
                visible_result=payload.visible_result, transport_json=payload.transport_json,
                terminal_payload_sha256=payload.digest, rejected_at=now,
                delivery_status="not_applicable", delivery_generation=0,
                delivery_outcome="none", delivery_message_count=0, delivered_at=now,
            ),
        ])
        session.commit()
    sessions.kw["bind"].dispose()


if __name__ == "__main__":
    seed(Path(sys.argv[1]))
