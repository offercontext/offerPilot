from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class ContributorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool = False
    max_units: StrictInt = Field(default=4096, ge=256, le=8192)
    version: Literal["v1"] = "v1"


class ContextPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    confirmed_readiness: ContributorPolicy = ContributorPolicy()
    confirmed_memory: ContributorPolicy = ContributorPolicy(enabled=True)
    knowledge_context: ContributorPolicy = ContributorPolicy()
    older_conversation_summary: ContributorPolicy = ContributorPolicy()
