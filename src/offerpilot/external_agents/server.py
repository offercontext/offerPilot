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
from offerpilot.external_agents.catalog import permitted

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
DESCRIPTIONS.update(
    {
        "propose_application_create": "Propose a saved application with company, role, URL, stage and notes. Requires explicit workspace create grant. No application is saved before independent user review.",
        "propose_application_update": "Propose a partial application edit or stage change using its current revision. Closing needs a reason; closed applications cannot reopen. Omitted fields are preserved.",
        "propose_application_delete": "Propose soft deletion of an application. Approval hides its related business content; does not physically erase history. Requires current revision.",
        "list_resumes": "List current resumes only with the separate global resume read grant. Never inferred from application scope.",
        "get_resume": "Read current resume content and revision with a separate global resume grant; local file paths are omitted.",
        "propose_resume_create": "Propose a new resume. Explicit global resume write grant and separate user review required; the first resume becomes master unless explicitly invalid.",
        "propose_resume_update": "Propose selected resume fields using current revision. Omitted fields stay unchanged. Master transitions also recheck the library snapshot at approval.",
        "propose_resume_copy": "Propose an editable non-master copy of the current resume with a new title, preserving its parent lineage.",
        "propose_resume_delete": "Propose soft deletion of a resume using its revision. Nonempty master resumes cannot be deleted. Never executes before separate user review.",
        "list_notes": "List original interview notes in granted applications. Unbound notes require an additional explicit workspace grant.",
        "get_note": "Read an original interview note and revision within the granted application scope.",
        "propose_note_create": "Propose an original interview note. A bound interview must belong to the granted application and can have only one note. Unbound notes require a separate workspace grant.",
        "propose_note_update": "Propose selected original note fields using its revision. Application ownership cannot change. Explicit null application_event_id unbinds the event; omission preserves it.",
        "propose_note_delete": "Propose deletion of an original interview note with its revision; the independent user preview shows the affected record.",
        "search_knowledge": "Search current confirmed Knowledge Notes and independent Evidence with an explicit global Knowledge read grant. Returns provenance, versions and hashes; omits oversized complete items and reports omitted_count. Does not ingest, generate or write Knowledge.",
    }
)


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
            "OfferPilot provides explicitly scoped business reads, Knowledge retrieval and maintenance proposals. "
            "Resume and Knowledge access require separate global grants. A pending request is not saved business data. Only the user's separate trusted OfferPilot review "
            "channel can commit it; no tool can confirm and no boolean counts as user consent. "
            "After the user independently reviews, read get_request. Report saved data only "
            "when status=committed and the receipt includes the resource; otherwise report the actual "
            "status/source availability without reconstructing hidden resource text. Never invoke "
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
            if permitted(name, grant)
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
