"""Official MCP SDK stdio adapter; stdout is reserved for the protocol."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.external_agents.schemas import INPUTS, OUTPUTS, PROPOSAL_TOOLS
from offerpilot.external_agents.service import ExternalAgentError, ExternalAgentService

DESCRIPTIONS = {
    "list_applications": "List saved applications in the user-granted scope, with ID pagination.",
    "get_application": "Read one saved application in the user-granted scope.",
    "list_events": "List application events (including interviews) in the granted scope, with ID pagination.",
    "get_event": "Read an application event and its current revision before proposing a reschedule.",
    "propose_interview": "Propose an interview. Saves a pending request only. A user must review it separately in OfferPilot; never claim the event is saved before status=committed. Retry an uncertain result using exactly the same idempotency_key and input.",
    "propose_reschedule": "Propose a reschedule for an existing interview using get_event's current revision. No event changes until separate user approval. Retry uncertain results with the same key and input.",
    "get_request": "Read this connection's durable request status and visible committed receipt. Hidden sources omit event text. Does not approve or execute it.",
    "cancel_request": "Cancel this connection's pending request. Terminal requests return their existing state; committed events are never undone.",
}


def open_sessions(data_dir: Path) -> sessionmaker[Session]:
    """Serving never initializes or resets a workspace. The owner runs connect first."""
    database = data_dir / "data.db"
    if not database.is_file():
        raise ExternalAgentError("service_unavailable", retryable=True)
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 1},
        pool_size=2,
        max_overflow=0,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection: Any, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    return sessionmaker(engine, expire_on_commit=False)


def build_server(service: ExternalAgentService) -> Any:
    # The optional dependency is loaded only for this entrypoint. Normal CLI/API
    # installations continue to work without the MCP extra.
    import anyio
    from mcp.server.lowlevel import Server
    from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

    server = Server(
        "offerpilot",
        version="1.0.0",
        instructions=(
            "OfferPilot provides user-scoped saved applications and interview scheduling proposals. "
            "A pending request is not an event. Only the user's separate trusted OfferPilot review "
            "channel can commit it; no tool can confirm and no boolean counts as user consent. "
            "After the user independently reviews, read get_request. Report an exact event only "
            "when status=committed and the receipt includes event; otherwise report the actual "
            "status/source availability without reconstructing hidden event text. Never invoke "
            "the operator review command on the user's behalf, including through an Agent shell. "
            "MCP cancellation interrupts observation; use cancel_request to cancel a durable pending "
            "proposal. Recover uncertain proposals by repeating their original idempotency key and input. "
            "The operator review command must use the same data directory as the user-owned connection configuration."
        ),
    )

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        grant = await anyio.to_thread.run_sync(service.grant, abandon_on_cancel=True)
        return [
            Tool(
                name=name,
                description=DESCRIPTIONS[name],
                inputSchema=schema.model_json_schema(),
                outputSchema=OUTPUTS[name].model_json_schema(),
                annotations=ToolAnnotations(
                    readOnlyHint=name not in PROPOSAL_TOOLS
                    and name not in {"get_request", "cancel_request"},
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for name, schema in INPUTS.items()
            if grant["allow_scheduling"] or name not in PROPOSAL_TOOLS
        ]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            # Cancel the protocol waiter promptly. A started SQLite worker may
            # still finish its atomic proposal/control write; the original key
            # recovers that outcome. Never emit a second reply after SDK cancel.
            data = await anyio.to_thread.run_sync(
                lambda: service.call(name, arguments),
                abandon_on_cancel=True,
            )
            if "request_id" in data:
                data["review_command"] = [
                    "oc",
                    "mcp",
                    "review",
                    data["request_id"],
                ]
            envelope = {"ok": True, "data": data, "error": None}
            envelope = (
                OUTPUTS[name].model_validate(envelope).model_dump(mode="json", exclude_unset=True)
            )
        except ExternalAgentError as exc:
            envelope = {
                "ok": False,
                "data": None,
                "error": {"code": exc.code, "retryable": exc.retryable},
            }
        except Exception:
            # Do not return database paths, saved text or arbitrary exception data.
            envelope = {
                "ok": False,
                "data": None,
                "error": {"code": "internal_error", "retryable": False},
            }
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))],
            structuredContent=envelope,
            isError=not envelope["ok"],
        )

    return server


async def serve(service: ExternalAgentService) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(service)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="OfferPilot scoped MCP server (stdio only)")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--connection-id", required=True)
    args = parser.parse_args()
    sessions = None
    try:
        import mcp  # noqa: F401

        sessions = open_sessions(args.data_dir.resolve())
        service = ExternalAgentService(sessions, args.connection_id)
        service.grant()
        asyncio.run(serve(service))
    except ImportError:
        print('MCP support is missing. Install OfferPilot with the "mcp" extra.', file=sys.stderr)
        raise SystemExit(2) from None
    except ExternalAgentError as exc:
        print(f"OfferPilot MCP: {exc.code}", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        if sessions is not None:
            sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
