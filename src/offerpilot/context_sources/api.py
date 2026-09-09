from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .contracts import ContextPolicies
from .models import ContextContributorSettings


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    policies: ContextPolicies


def register_context_policy_routes(app: FastAPI, sessions: sessionmaker[Session]) -> None:
    @app.get("/api/context-policies")
    def read_policies() -> dict[str, object]:
        with sessions() as session:
            row = session.get(ContextContributorSettings, 1)
            return {"revision": row.revision if row else 0, "policies": (
                ContextPolicies.model_validate_json(row.settings_json) if row else ContextPolicies()).model_dump()}

    @app.put("/api/context-policies")
    def update_policies(command: PolicyUpdate) -> JSONResponse:
        with sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(ContextContributorSettings, 1)
            if command.expected_revision != (row.revision if row else 0):
                return JSONResponse({"error": "设置已变化，请刷新后重试"}, status_code=409)
            if row is None:
                row = ContextContributorSettings(id=1, revision=0, settings_json="{}")
                session.add(row)
            row.revision += 1
            row.settings_json = command.policies.model_dump_json()
            result = {"revision": row.revision, "policies": command.policies.model_dump()}
            session.commit()
            return JSONResponse(result)
