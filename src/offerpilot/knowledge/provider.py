from __future__ import annotations

from typing import Any

from litellm import completion


class LiteLLMKnowledgeBriefProviderClient:
    """The single raw Knowledge-provider boundary used by API and CLI."""

    def complete_once(self, **payload: Any) -> Any:
        return completion(**payload)


def build_knowledge_brief_provider_client() -> LiteLLMKnowledgeBriefProviderClient:
    return LiteLLMKnowledgeBriefProviderClient()

