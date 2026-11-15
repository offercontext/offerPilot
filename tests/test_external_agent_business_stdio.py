import asyncio
from datetime import timedelta
from uuid import uuid4

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from offerpilot.external_agents.cli import configuration
from offerpilot.external_agents.catalog import BUSINESS_PROPOSALS, permitted
from offerpilot.external_agents.schemas import INPUTS, OUTPUTS
from tests.test_external_agent_business import business as business


def test_explicit_grants_expose_validated_business_roundtrips_over_stdio(business, tmp_path):
    _, owner, first, _ = business
    config = configuration(owner.connection_id, tmp_path, "mcp")["mcpServers"]["offerpilot"]

    async def scenario():
        async with stdio_client(StdioServerParameters(**config)) as (reader, writer):
            async with ClientSession(
                reader, writer, read_timeout_seconds=timedelta(seconds=20)
            ) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                assert len(tools) == 23
                assert not any("approve" in name or "confirm" in name for name in tools)

                async def call(tool, **args):
                    result = await client.call_tool(tool, args)
                    assert result.structuredContent is not None
                    jsonschema.validate(result.structuredContent, tools[tool].outputSchema)
                    assert not result.isError, result.structuredContent
                    return result.structuredContent["data"]

                async def write(tool, **args):
                    pending = await call(tool, idempotency_key=str(uuid4()), **args)
                    assert pending["status"] == "pending"
                    view = owner.review(pending["request_id"])
                    approved = owner.decide(view["request_id"], view["proposal_digest"], "approve")
                    result = await call("get_request", request_id=pending["request_id"])
                    assert result["status"] == "committed"
                    assert result["resource_id"] == approved["resource_id"]
                    return result

                created = await write(
                    "propose_application_create", company_name="链路公司", position_name="岗位"
                )
                application = await call(
                    "get_application", application_id=created["application"]["id"]
                )
                changed = await write(
                    "propose_application_update",
                    application_id=application["id"],
                    expected_revision=application["revision"],
                    changes={"notes": "跟进记录"},
                )
                assert changed["application"]["notes"] == "跟进记录"
                resume = (
                    await write(
                        "propose_resume_create", title="事实简历", text="真实保存的测试文本"
                    )
                )["resume"]
                read_resume = await call("get_resume", resume_id=resume["id"])
                assert read_resume["text"] == "真实保存的测试文本"
                assert len((await call("list_resumes"))["items"]) == 1
                note = (
                    await write(
                        "propose_note_create", application_id=first.id, questions="协议边界问题"
                    )
                )["note"]
                assert (await call("get_note", note_id=note["id"]))["questions"] == "协议边界问题"
                assert len((await call("list_notes", application_id=first.id))["items"]) == 1
                assert (await call("search_knowledge", query="数据库"))["items"] == []
                error = await client.call_tool(
                    "propose_application_update",
                    {
                        "application_id": application["id"],
                        "expected_revision": application["revision"],
                        "idempotency_key": str(uuid4()),
                        "changes": {"notes": "错误"},
                        "confirmed": True,
                    },
                )
                assert error.isError and error.structuredContent["error"]["code"] == "invalid_input"

    asyncio.run(scenario())


def test_new_tool_catalog_cannot_default_to_implicit_authorization():
    legacy = {"allow_scheduling": True}
    assert not permitted("future_undefined_tool", legacy)
    assert set(INPUTS) == set(OUTPUTS)
    assert all(not permitted(name, legacy) for name in BUSINESS_PROPOSALS)
