"""Prepare fictional data and verify MCP outcomes; never approve on a user's behalf."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from offerpilot.db import init_database
from offerpilot.external_agents.cli import configuration
from offerpilot.external_agents.service import create_connection
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository


async def exercise(root: Path, *, prepare: bool, expected: str) -> dict[str, object]:
    if prepare:
        # A new, explicitly named directory prevents initialization/reset of any
        # existing user database. Keep every acceptance attempt independently.
        root.mkdir(parents=True, exist_ok=False)
        sessions = init_database(root / "data.db")
        try:
            application = ApplicationsRepository(sessions).create(
                ApplicationCreate("外部 Agent 验收虚构公司", "验收开发岗位")
            )
            grant = create_connection(
                sessions, "manual acceptance", [application.id], allow_scheduling=True
            )
            fixture = {
                "connection_id": grant["connection_id"],
                "application_id": application.id,
                "idempotency_key": str(uuid4()),
            }
        finally:
            sessions.kw["bind"].dispose()
        for target, name in (("mcp", "mcp.json"), ("dsh", "offerpilot.cordis.yml")):
            (root / name).write_text(
                json.dumps(
                    configuration(str(fixture["connection_id"]), root, target),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    else:
        fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "offerpilot.external_agents.server",
            "--data-dir",
            str(root),
            "--connection-id",
            str(fixture["connection_id"]),
        ],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            if prepare:
                pending = await client.call_tool(
                    "propose_interview",
                    {
                        "idempotency_key": fixture["idempotency_key"],
                        "application_id": fixture["application_id"],
                        "scheduled_at": "2026-11-01T15:00:00+08:00",
                        "duration_minutes": 60,
                        "location": "验收专用线上会议",
                        "notes": "虚构验收数据，不联系招聘方",
                    },
                )
                if pending.isError or pending.structuredContent is None:
                    raise RuntimeError("MCP proposal failed")
                fixture["request_id"] = pending.structuredContent["data"]["request_id"]
                (root / "fixture.json").write_text(
                    json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            result = await client.call_tool("get_request", {"request_id": fixture["request_id"]})
            if result.isError or result.structuredContent is None:
                raise RuntimeError("MCP receipt lookup failed")
            data = result.structuredContent["data"]
            events = await client.call_tool(
                "list_events", {"application_id": fixture["application_id"]}
            )
            if events.isError or events.structuredContent is None:
                raise RuntimeError("MCP event readback failed")
            items = events.structuredContent["data"]["items"]
    # Expected business failures belong outside the SDK's task-group lifetime,
    # so the CLI reports the actual state without an ExceptionGroup traceback.
    if data["status"] != expected:
        raise RuntimeError(
            f"Expected {expected}, observed {data['status']}; no approval is simulated"
        )
    if expected == "committed":
        saved = data["event"]
        if len(items) != 1 or items[0] != saved:
            raise RuntimeError("Committed event does not match actual event list")
        if saved["scheduled_at"] != "2026-11-01T07:00:00Z" or saved["duration_minutes"] != 60:
            raise RuntimeError("Readback differs from reviewed schedule")
    elif items:
        raise RuntimeError("An event was written without a committed receipt")
    return {
        "status": data["status"],
        "request_id": fixture["request_id"],
        "event_count": len(items),
        "data_dir": str(root),
        "review_command": ["oc", "mcp", "review", fixture["request_id"], "--data-dir", str(root)],
        "human_confirmation": "requires the user; this script cannot approve",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--expected", choices=("pending", "committed", "rejected", "cancelled", "expired")
    )
    args = parser.parse_args()
    expected = args.expected or ("pending" if args.action == "prepare" else "committed")
    try:
        result = asyncio.run(
            exercise(args.data_dir.resolve(), prepare=args.action == "prepare", expected=expected)
        )
    except FileExistsError:
        parser.exit(2, "验收目录已存在；请选择一个新目录，旧数据不会覆盖。\n")
    except FileNotFoundError:
        parser.exit(2, "验收目录未准备好，请先使用 prepare 创建独立验收数据。\n")
    except RuntimeError as exc:
        parser.exit(2, f"验收未通过：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
