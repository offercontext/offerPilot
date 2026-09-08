"""Bounded real-provider acceptance for P0; writes only to a fresh temp directory.

Run explicitly with ``uv run python scripts/edited-confirmation-real-ai-check.py``.
Two fixed scenarios, at most eight provider requests total, no retries to select
successful samples. The report redacts confirmation credentials. Configuration
is copied before loading so even config migration cannot modify the source.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import offerpilot.ai.client as provider_module  # noqa: E402
from offerpilot.api import create_app  # noqa: E402
from offerpilot.config import load_config, resolve_data_dir, save_config  # noqa: E402
from offerpilot.db import session_factory_for_data_dir  # noqa: E402
from offerpilot.models import ChatMessage, WriteOperation  # noqa: E402


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key in {"confirmation_token", "api_key", "auth_token", "confirmation_secret"}
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def response_evidence(response: Any) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return {"status_code": response.status_code, "body": redact(response.json())}
    events = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            events.append(redact(json.loads(line[5:].strip())))
    return {"status_code": response.status_code, "events": events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=resolve_data_dir())
    parser.add_argument(
        "--text-confirmation-follow-up", action="store_true",
        help="Allow one fixed user confirmation if the model asks only in text; still at most eight calls.",
    )
    args = parser.parse_args()
    source_config = args.config_dir / "config.json"
    if not source_config.is_file():
        print("No source config.json; no provider calls made.")
        return 2
    output = Path(tempfile.mkdtemp(prefix="offerpilot-edited-confirmation-"))
    report: dict[str, Any] = {"provider_limit": 8, "provider_calls": 0, "scenarios": []}
    real_completion = provider_module.completion
    phase = "proposal"
    blocked_calls = 0

    def bounded_completion(**kwargs: Any) -> Any:
        nonlocal blocked_calls
        if phase != "proposal" or report["provider_calls"] >= report["provider_limit"]:
            blocked_calls += 1
            raise RuntimeError("acceptance_provider_boundary")
        report["provider_calls"] += 1
        kwargs.update(timeout=45, num_retries=0, max_tokens=1024)
        return real_completion(**kwargs)

    with patch.object(provider_module, "completion", bounded_completion):
        for name, endpoint in (
            ("sync", "/api/chat/confirm"),
            ("sse", "/api/chat/confirm/stream"),
        ):
            phase = "proposal"
            start_calls = report["provider_calls"]
            start_blocked = blocked_calls
            scenario: dict[str, Any] = {"name": name, "passed": False}
            report["scenarios"].append(scenario)
            data_dir = output / name
            data_dir.mkdir()
            shutil.copyfile(source_config, data_dir / "config.json")
            try:
                config = load_config(data_dir)
                config.auth_enabled = False
                config.skills = []
                config.chat_auto_approve_writes = False
                save_config(data_dir, config)
                with TestClient(create_app(data_dir=data_dir)) as client:
                    created = client.post("/api/offers", json={
                        "company_name": "P0虚构验收公司",
                        "position_name": "虚构后端工程师",
                        "base_monthly": 28000,
                        "months_per_year": 12,
                        "signing_bonus": 0,
                        "perks": "无远程约定",
                    })
                    assert created.status_code == 201
                    offer_id = created.json()["id"]
                    proposed = client.post("/api/chat", json={
                        "conversation_id": 0,
                        "message": (
                            f"请修改已存在的 Offer（id={offer_id}，P0虚构验收公司）："
                            "签字费设为10000元，perks改为每周远程办公两天，其他内容保持。"
                            "请提出update_offer写操作让我确认。"
                        ),
                        "attachments": [{
                            "kind": "offer", "id": str(offer_id), "label": "P0虚构验收Offer",
                        }],
                    })
                    scenario["proposal"] = response_evidence(proposed)
                    pending = proposed.json()
                    if args.text_confirmation_follow_up and pending.get("type") == "message":
                        scenario["initial_proposal"] = scenario["proposal"]
                        follow_up = (
                            "我确认这两项修改。请现在调用 update_offer 提交操作，"
                            "让应用展示系统确认卡；不要只用文字请我再确认。"
                        )
                        scenario["fixed_follow_up_message"] = follow_up
                        proposed = client.post("/api/chat", json={
                            "conversation_id": pending["conversation_id"],
                            "message": follow_up,
                            "attachments": [{
                                "kind": "offer", "id": str(offer_id), "label": "P0虚构验收Offer",
                            }],
                        })
                        scenario["proposal"] = response_evidence(proposed)
                        pending = proposed.json()
                    assert pending.get("type") == "confirmation_required"
                    action = pending["pending_action"]
                    assert action["tool_name"] == "update_offer"
                    assert action["args"]["signing_bonus"] == 10000
                    operation_id = action["operation_id"]
                    request = {
                        "conversation_id": pending["conversation_id"],
                        "operation_id": operation_id,
                        "confirmation_token": action["confirmation_token"],
                        "approved": True,
                        "edited_args": {"signing_bonus": 8000, "perks": "每周远程办公一天"},
                    }
                    phase = "confirmation"
                    calls_before_confirm = report["provider_calls"]
                    confirmed = client.post(endpoint, json=request)
                    scenario["confirmation"] = response_evidence(confirmed)
                    replay_endpoint = "/api/chat/confirm/stream" if name == "sync" else "/api/chat/confirm"
                    replayed = client.post(replay_endpoint, json=request)
                    scenario["cross_transport_replay"] = response_evidence(replayed)
                    offer = client.get(f"/api/offers/{offer_id}").json()
                    scenario["stored_values"] = {
                        key: offer[key] for key in ("signing_bonus", "perks")
                    }
                    with session_factory_for_data_dir(data_dir)() as session:
                        operation = session.get(WriteOperation, operation_id)
                        messages = list(session.scalars(select(ChatMessage).where(
                            ChatMessage.operation_id == operation_id
                        ).order_by(ChatMessage.delivery_ordinal)))
                        scenario["ledger_status"] = operation.status if operation else None
                        scenario["receipt_messages"] = [
                            {"role": item.role, "content": item.content} for item in messages
                        ]
                        assert operation is not None and operation.status == "committed"
                        assert len([item for item in messages if item.role == "assistant"]) == 1
                        assert any("自动续答" in item.content for item in messages)
                    assert confirmed.status_code == replayed.status_code == 200
                    assert offer["signing_bonus"] == 8000 and offer["perks"] == "每周远程办公一天"
                    assert report["provider_calls"] == calls_before_confirm
                    assert blocked_calls == start_blocked
                    scenario["passed"] = True
            except Exception as exc:
                # Exception text can contain provider headers or credentials.
                scenario["error_type"] = type(exc).__name__
            finally:
                scenario["provider_calls"] = report["provider_calls"] - start_calls
                scenario["blocked_provider_attempts"] = blocked_calls - start_blocked
                (output / "report.json").write_text(
                    json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8"
                )
    print(json.dumps({"report": str(output / "report.json"), **{
        key: report[key] for key in ("provider_limit", "provider_calls")
    }, "passed": all(item["passed"] for item in report["scenarios"])}, ensure_ascii=False))
    return 0 if all(item["passed"] for item in report["scenarios"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
