from __future__ import annotations

from typing import Any

def completion(*args: Any, **kwargs: Any) -> Any:
    """Defer SDK loading without changing the raw Knowledge-provider call boundary."""
    from litellm import completion as sdk_completion

    return sdk_completion(*args, **kwargs)


class LiteLLMKnowledgeBriefProviderClient:
    """The single raw Knowledge-provider boundary used by API and CLI."""

    def complete_once(self, **payload: Any) -> Any:
        return completion(**payload)


def build_knowledge_brief_provider_client() -> LiteLLMKnowledgeBriefProviderClient:
    return LiteLLMKnowledgeBriefProviderClient()

