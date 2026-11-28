from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from offerpilot.application_strategy.repository import (
    ApplicationStrategyRepository,
    ConfirmApplied,
    DecideChange,
    ProposeChange,
    StrategyArchiveLabel,
    StrategyConflict,
    StrategyState,
)
from offerpilot.models import ApplicationOutcome
from offerpilot.repositories.application_strategy_evidence import StrategyEvidenceUnavailable

_ROUTE = "/api/applications/{app_id}/outcomes/{outcome_id}/strategy"
_NO_CACHE = {"Cache-Control": "no-store"}
_EvidenceId = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]
_Revision = Annotated[StrictInt, Field(ge=0, le=2)]


class _WritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")


class _Proposal(_WritePayload):
    action: Literal["propose"]
    proposal: StrictStr = Field(min_length=1, max_length=2000)


class _Confirmation(_WritePayload):
    action: Literal["confirm_applied"]
    expected_revision: _Revision
    target_application_id: _EvidenceId
    target_snapshot_id: _EvidenceId
    applied_detail: StrictStr = Field(min_length=1, max_length=2000)


class _Decision(_WritePayload):
    action: Literal["decide"]
    expected_revision: _Revision
    target_outcome_id: _EvidenceId
    decision: Literal["keep", "revise", "drop"]


_COMMAND = TypeAdapter(Annotated[_Proposal | _Confirmation | _Decision, Field(discriminator="action")])


def register_application_strategy_routes(app: FastAPI, session_factory: sessionmaker[Session]) -> None:
    """UI-only API, behind the ordinary application auth middleware."""
    repository = ApplicationStrategyRepository(session_factory)

    @app.get(_ROUTE)
    def get_strategy(request: Request, app_id: int, outcome_id: int) -> JSONResponse:
        return _run(request, lambda: _response(_strategy(repository.get(app_id, outcome_id))))

    @app.get(_ROUTE + "/target-snapshots")
    def list_target_snapshots(request: Request, app_id: int, outcome_id: int, target_application_id: int) -> JSONResponse:
        return _run(request, lambda: _response([
            _archive(label) for label in repository.list_targets(app_id, outcome_id, target_application_id)
        ]))

    @app.post(_ROUTE)
    async def write_strategy(request: Request, app_id: int, outcome_id: int) -> JSONResponse:
        denied = _origin_denial(request)
        if denied is not None:
            return denied
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            return _error(422, "application_strategy_invalid")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 16_384:
                return _error(422, "application_strategy_invalid")
            raw.extend(chunk)
        try:
            payload = _COMMAND.validate_json(raw)
        except ValidationError:
            return _error(422, "application_strategy_invalid")

        if isinstance(payload, _Proposal):
            command = ProposeChange(payload.proposal)
        elif isinstance(payload, _Confirmation):
            command = ConfirmApplied(
                payload.expected_revision, payload.target_application_id, payload.target_snapshot_id, payload.applied_detail
            )
        else:
            command = DecideChange(payload.expected_revision, payload.target_outcome_id, payload.decision)

        def save() -> JSONResponse:
            result = repository.execute(app_id, outcome_id, request_key=payload.idempotency_key, command=command)
            return _response({**_strategy(result.state), "replayed": result.replayed})

        return await run_in_threadpool(_run, request, save)


def _run(request: Request, action: Callable[[], JSONResponse]) -> JSONResponse:
    denied = _origin_denial(request)
    if denied is not None:
        return denied
    try:
        return action()
    except StrategyEvidenceUnavailable:
        return _error(404, "application_strategy_evidence_unavailable")
    except StrategyConflict:
        return _error(409, "application_strategy_conflict")
    except ValueError:
        return _error(422, "application_strategy_invalid")


def _origin_denial(request: Request) -> JSONResponse | None:
    origins = request.headers.getlist("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origins and origins != [expected]:
        return _error(403, "application_strategy_origin_denied")
    return None


def _response(value: object, *, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers=_NO_CACHE)


def _error(status: int, code: str) -> JSONResponse:
    return _response({"error_code": code}, status=status)


def _utc_iso(value: datetime) -> str:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def _archive(label: StrategyArchiveLabel) -> dict[str, object]:
    return {**asdict(label), "submitted_at": _utc_iso(label.submitted_at)}


def _outcome(item: ApplicationOutcome) -> dict[str, object]:
    return {
        "id": item.id, "application_id": item.application_id, "submission_snapshot_id": item.submission_snapshot_id,
        "stage": item.stage, "result": item.result, "occurred_at": _utc_iso(item.occurred_at),
        "has_external_feedback": bool(item.feedback_text.strip()),
    }


def _strategy(state: StrategyState) -> dict[str, object]:
    change = state.change
    return {
        "origin": _outcome(state.origin),
        "change": {
            "proposal": change.proposal, "revision": change.revision,
            "applied_detail": change.applied_detail, "decision": change.decision,
            "decided_outcome_id": change.target_outcome_id,
        } if change is not None else None,
        "target": _archive(state.target) if state.target is not None else None,
        "comparable_outcomes": [_outcome(item) for item in state.comparable_outcomes],
    }
