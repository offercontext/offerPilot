"""Real official-SDK client/server protocol tests; no provider/model calls."""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import jsonschema
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from offerpilot.db import init_database
from offerpilot.external_agents.cli import configuration
from offerpilot.external_agents.service import ExternalAgentService, create_connection
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.application_events import ApplicationEventsRepository


async def cancel_protocol_request(client, request_id, call):
    # SDK v1 does not turn Task.cancel() into a protocol notification. Emit the
    # real wire cancellation and wait for its response before releasing work.
    await client.send_notification(
        types.ClientNotification(
            types.CancelledNotification(
                params=types.CancelledNotificationParams(
                    requestId=request_id, reason="observer cancelled"
                ),
            )
        )
    )
    try:
        await asyncio.wait_for(call, timeout=5)
    except McpError as exc:
        assert exc.error.message == "Request cancelled"
    else:
        raise AssertionError("the MCP request did not acknowledge cancellation")


def test_official_sdk_stdio_read_propose_operator_decide_and_readback(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(sessions).create(ApplicationCreate("云岚", "开发"))
    grant = create_connection(sessions, "DSH", [application.id], allow_scheduling=True)
    owner_service = ExternalAgentService(sessions, grant["connection_id"])
    config = configuration(grant["connection_id"], tmp_path, "mcp")["mcpServers"]["offerpilot"]
    parameters = StdioServerParameters(**config)

    async def scenario():
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=20)
            ) as client:
                initialization = await client.initialize()
                assert initialization.serverInfo.name == "offerpilot"
                tools = await client.list_tools()
                schemas = {tool.name: tool for tool in tools.tools}
                assert len(schemas) == 8
                assert schemas["get_request"].annotations.readOnlyHint is False
                assert not any("confirm" in name or "approve" in name for name in schemas)

                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert result.structuredContent is not None
                    jsonschema.validate(result.structuredContent, schemas[name].outputSchema)
                    text = json.loads(result.content[0].text)
                    assert text == result.structuredContent
                    return result, text

                response, value = await call("list_applications", {})
                assert not response.isError
                assert value["data"]["items"][0]["company_name"] == "云岚"
                args = {
                    "idempotency_key": str(uuid4()),
                    "application_id": application.id,
                    "scheduled_at": "2026-11-01T15:00:00+08:00",
                    "duration_minutes": 60,
                }
                response, rejected = await call("propose_interview", {**args, "confirmed": True})
                assert response.isError and rejected["error"]["code"] == "invalid_input"
                response, pending = await call("propose_interview", args)
                assert not response.isError and pending["data"]["status"] == "pending"
                request_id = pending["data"]["request_id"]
                assert str(tmp_path) not in json.dumps(pending)
                assert pending["data"]["review_command"] == ["oc", "mcp", "review", request_id]
                assert ApplicationEventsRepository(sessions).list() == []
                view = owner_service.review(request_id)
                expected = owner_service.decide(request_id, view["proposal_digest"], "approve")
                _, result = await call("get_request", {"request_id": request_id})
                assert result["data"]["event"] == expected["event"]
                _, event = await call("get_event", {"event_id": expected["event"]["id"]})
                assert event["data"]["scheduled_at"] == "2026-11-01T07:00:00Z"

    asyncio.run(scenario())


def test_readonly_stdio_hides_proposals_but_also_denies_direct_calls(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    grant = create_connection(sessions, "Readonly", [], workspace=True)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "offerpilot.external_agents.server",
            "--data-dir",
            str(tmp_path),
            "--connection-id",
            grant["connection_id"],
        ],
    )

    async def scenario():
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert len(tools.tools) == 6
                assert not any(tool.name.startswith("propose_") for tool in tools.tools)
                result = await client.call_tool(
                    "propose_interview",
                    {
                        "idempotency_key": str(uuid4()),
                        "application_id": 1,
                        "scheduled_at": "2026-11-01T15:00:00Z",
                        "duration_minutes": 60,
                    },
                )
                assert result.isError
                assert result.structuredContent["error"]["code"] == "scheduling_not_granted"

    asyncio.run(scenario())


def test_missing_workspace_fails_without_creating_database(tmp_path):
    import subprocess

    data = tmp_path / "missing"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "offerpilot.external_agents.server",
            "--data-dir",
            str(data),
            "--connection-id",
            str(uuid4()),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert result.stdout == "" and "service_unavailable" in result.stderr
    assert not data.exists()


def test_dsh_patch_has_official_insert_shape(tmp_path):
    config = configuration(str(uuid4()), tmp_path, "dsh")
    assert isinstance(config, list)
    row = config[0]["insert"][0]
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"
    assert row["config"]["serverName"] == "offerpilot"
    assert row["config"]["transport"] == "stdio"
    assert Path(row["config"]["command"]).is_absolute()


def test_transport_cancel_before_proposal_can_recover_with_the_original_key(tmp_path):
    from sqlalchemy import func, select
    from offerpilot.external_agents.models import ExternalAgentRequest

    sessions = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(sessions).create(ApplicationCreate("云岚", "开发"))
    grant = create_connection(sessions, "Cancel", [application.id], allow_scheduling=True)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "offerpilot.external_agents.server",
            "--data-dir",
            str(tmp_path),
            "--connection-id",
            grant["connection_id"],
        ],
    )

    async def scenario():
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=20)
            ) as client:
                await client.initialize()
                args = {
                    "idempotency_key": str(uuid4()),
                    "application_id": application.id,
                    "scheduled_at": "2026-11-01T15:00:00Z",
                    "duration_minutes": 60,
                }
                with sessions() as locker:
                    locker.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    request_id = (
                        client._request_id
                    )  # pin the next wire ID in this SDK integration test
                    call = asyncio.create_task(client.call_tool("propose_interview", args))
                    await asyncio.sleep(0.15)
                    await cancel_protocol_request(client, request_id, call)
                    locker.rollback()
                result = await client.call_tool("propose_interview", args)
                assert not result.isError
                pending = result.structuredContent["data"]
                assert pending["status"] == "pending"
                repeated = await client.call_tool("propose_interview", args)
                assert repeated.structuredContent["data"] == pending
                cancelled = await client.call_tool(
                    "cancel_request", {"request_id": pending["request_id"]}
                )
                assert cancelled.structuredContent["data"]["status"] == "cancelled"
                assert ApplicationEventsRepository(sessions).list() == []
                with sessions() as session:
                    assert (
                        session.scalar(select(func.count()).select_from(ExternalAgentRequest)) == 1
                    )

    asyncio.run(scenario())


def test_committed_proposal_lost_to_transport_cancellation_recovers_exact_receipt(tmp_path):
    from textwrap import dedent
    from sqlalchemy import func, select
    from offerpilot.external_agents.models import ExternalAgentRequest

    sessions = init_database(tmp_path / "data.db")
    application = ApplicationsRepository(sessions).create(ApplicationCreate("云岚", "开发"))
    grant = create_connection(sessions, "Delivery loss", [application.id], allow_scheduling=True)
    marker = tmp_path / "proposal-committed"
    release = tmp_path / "release-result"
    script = tmp_path / "paused_server.py"
    # Instrument only the test process at the real service -> MCP delivery seam.
    # No production test switch or alternative transaction path is introduced.
    script.write_text(
        dedent("""
        import asyncio, sys, time
        from pathlib import Path
        from offerpilot.external_agents.server import open_sessions, serve
        from offerpilot.external_agents.service import ExternalAgentService
        root = Path(sys.argv[1])
        service = ExternalAgentService(open_sessions(root), sys.argv[2])
        original = service.call
        def paused(tool, arguments):
            result = original(tool, arguments)
            marker = root / "proposal-committed"
            if tool == "propose_interview" and not marker.exists():
                marker.write_text(result["request_id"], encoding="utf-8")
                deadline = time.monotonic() + 10
                while not (root / "release-result").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
            return result
        service.call = paused
        asyncio.run(serve(service))
    """),
        encoding="utf-8",
    )
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(script), str(tmp_path), grant["connection_id"]]
    )

    async def scenario():
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=20)
            ) as client:
                await client.initialize()
                args = {
                    "idempotency_key": str(uuid4()),
                    "application_id": application.id,
                    "scheduled_at": "2026-11-01T15:00:00Z",
                    "duration_minutes": 60,
                }
                request_id = client._request_id  # no other request is sent before this one
                call = asyncio.create_task(client.call_tool("propose_interview", args))
                try:
                    deadline = asyncio.get_running_loop().time() + 8
                    while not marker.exists() and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.01)
                    assert marker.exists(), "production transaction did not commit the proposal"
                    with sessions() as session:
                        assert (
                            session.scalar(select(func.count()).select_from(ExternalAgentRequest))
                            == 1
                        )
                    await cancel_protocol_request(client, request_id, call)
                finally:
                    release.write_text("release", encoding="utf-8")
                recovered = await client.call_tool("propose_interview", args)
                assert not recovered.isError
                data = recovered.structuredContent["data"]
                assert data["request_id"] == marker.read_text(encoding="utf-8")
                assert data["status"] == "pending"
                with sessions() as session:
                    assert (
                        session.scalar(select(func.count()).select_from(ExternalAgentRequest)) == 1
                    )
                assert ApplicationEventsRepository(sessions).list() == []

    asyncio.run(scenario())
