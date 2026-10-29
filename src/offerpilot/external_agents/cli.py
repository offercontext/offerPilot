"""Owner-operated grants, configuration, and a separate interactive review channel."""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Literal, NoReturn

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.config import resolve_data_dir
from offerpilot.external_agents.models import ExternalAgentRequest
from offerpilot.external_agents.server import open_sessions
from offerpilot.external_agents.service import (
    ExternalAgentError,
    ExternalAgentService,
    create_connection,
    transaction,
)

app = typer.Typer(help="Connect external Agents through scoped MCP; review writes separately")


def configuration(
    connection_id: str, data_dir: Path, target: str
) -> dict[str, Any] | list[dict[str, Any]]:
    config = {
        "command": sys.executable,
        "args": [
            "-m",
            "offerpilot.external_agents.server",
            "--data-dir",
            str(data_dir.resolve()),
            "--connection-id",
            connection_id,
        ],
    }
    if target == "dsh":
        return [
            {
                "insert": [
                    {
                        "id": "mcp-offerpilot",
                        "name": "@deepseek-ai/dsh-mcp-client",
                        "config": {
                            "serverName": "offerpilot",
                            "transport": "stdio",
                            **config,
                            "toolCallTimeoutMs": 15000,
                            "failOnStartupError": True,
                        },
                    }
                ]
            }
        ]
    if target == "mcp":
        return {"mcpServers": {"offerpilot": config}}
    raise ExternalAgentError("unknown_config_format")


def fail(exc: ExternalAgentError) -> NoReturn:
    typer.echo(f"OfferPilot MCP: {exc.code}", err=True)
    raise typer.Exit(2)


def owner_sessions(data_dir: Path | None) -> sessionmaker[Session]:
    try:
        return open_sessions(data_dir or resolve_data_dir())
    except ExternalAgentError as exc:
        fail(exc)


@app.command("connect")
def connect(
    name: str = typer.Argument(...),
    application_ids: list[int] = typer.Option([], "--application-id"),
    workspace: bool = typer.Option(False, "--workspace", help="Explicitly grant all applications"),
    allow_scheduling: bool = typer.Option(
        False, "--allow-scheduling", help="Allow pending scheduling proposals; never auto-approve"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Create a revocable local grant. Scope is required; the default is read-only."""
    sessions = owner_sessions(data_dir)
    try:
        typer.echo(
            json.dumps(
                create_connection(
                    sessions,
                    name,
                    application_ids,
                    workspace=workspace,
                    allow_scheduling=allow_scheduling,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    except ExternalAgentError as exc:
        fail(exc)
    finally:
        sessions.kw["bind"].dispose()


@app.command("config")
def config(
    connection_id: str = typer.Argument(...),
    target: str = typer.Option(
        "mcp", "--format", help="mcp JSON or dsh Cordis patch (JSON is valid YAML)"
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Create a new file; never overwrite a user configuration"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Generate a usable stdio configuration for the current installed interpreter."""
    resolved = (data_dir or resolve_data_dir()).resolve()
    sessions = owner_sessions(resolved)
    try:
        ExternalAgentService(sessions, connection_id).grant()
        rendered = (
            json.dumps(configuration(connection_id, resolved, target), ensure_ascii=False, indent=2)
            + "\n"
        )
        if output is None:
            typer.echo(rendered, nl=False)
        else:
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
            typer.echo(str(output.resolve()))
    except ExternalAgentError as exc:
        fail(exc)
    except OSError:
        typer.echo("配置文件无法创建（不会覆盖已有文件）。", err=True)
        raise typer.Exit(2) from None
    finally:
        sessions.kw["bind"].dispose()


@app.command("revoke")
def revoke(
    connection_id: str = typer.Argument(...),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Revoke a connection, including approval of its old pending requests."""
    sessions = owner_sessions(data_dir)
    try:
        ExternalAgentService(sessions, connection_id).revoke()
        typer.echo("连接已撤销；其待确认请求不会执行。")
    except ExternalAgentError as exc:
        fail(exc)
    finally:
        sessions.kw["bind"].dispose()


def review_in_terminal(service: ExternalAgentService, request_id: str) -> dict[str, Any]:
    """Operator terminal, outside MCP. No --yes, boolean or piped confirmation.

    TTY prevents accidental automation; it is not proof of a human identity.
    An Agent with arbitrary same-user OS execution is outside this boundary.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ExternalAgentError("interactive_operator_terminal_required")
    view = service.review(request_id)
    # JSON escapes terminal control characters in both saved and proposed text.
    rendered = json.dumps(view, ensure_ascii=False, indent=2)
    rendered = "".join(
        f"\\u{ord(character):04x}"
        if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} and character not in "\n\t"
        else character
        for character in rendered
    )
    typer.echo(rendered)
    if view["status"] != "pending":
        return {key: value for key, value in view.items() if key != "proposal"}
    typer.echo("请审阅以上投递、时间（UTC）、原安排和拟修改内容。批准将立即保存。")
    typer.echo(
        f"批准请输入 approve {request_id}；拒绝请输入 reject {request_id}；其他输入保留待确认。"
    )
    try:
        value = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        raise ExternalAgentError("review_interrupted") from None
    decision: Literal["approve", "reject"]
    if value == f"approve {request_id}":
        decision = "approve"
    elif value == f"reject {request_id}":
        decision = "reject"
    else:
        raise ExternalAgentError("review_not_confirmed")
    return service.decide(request_id, view["proposal_digest"], decision)


@app.command("review")
def review(
    request_id: str = typer.Argument(...),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Owner-operated review in an independent terminal, with exact-payload approval."""
    sessions = owner_sessions(data_dir)
    try:
        with transaction(sessions) as session:
            connection_id = session.scalar(
                select(ExternalAgentRequest.connection_id).where(
                    ExternalAgentRequest.id == request_id
                )
            )
        if connection_id is None:
            raise ExternalAgentError("request_not_found")
        result = review_in_terminal(ExternalAgentService(sessions, connection_id), request_id)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except ExternalAgentError as exc:
        fail(exc)
    finally:
        sessions.kw["bind"].dispose()
