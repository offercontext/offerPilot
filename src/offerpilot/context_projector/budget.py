from __future__ import annotations

from dataclasses import dataclass

from offerpilot.context_projector.contracts import FrozenMessage, ProjectionError, canonical_json

BUDGET_POLICY_VERSION = "model-surface-budget-v1"
COMPAT_CONTEXT_WINDOW = 32_768
DEFAULT_OUTPUT_RESERVE = 4_096
PROVIDER_FRAMING_RESERVE = 1_024
PRODUCT_INPUT_CAP = 65_536
CANONICAL_MESSAGES_BYTE_CAP = 256 * 1024
PROVIDER_TOOLS_BYTE_CAP = 64 * 1024
COMBINED_SURFACE_BYTE_CAP = 320 * 1024
ADAPTER_REQUEST_BODY_BYTE_CAP = 512 * 1024
OPTIONAL_HISTORY_MESSAGE_BYTE_CAP = 1024 * 1024


@dataclass(frozen=True)
class ProviderBudget:
    context_window: int = COMPAT_CONTEXT_WINDOW
    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    framing_reserve: int = PROVIDER_FRAMING_RESERVE
    estimator_version: str = "utf8-upper-bound-v1"

    def __post_init__(self) -> None:
        if (
            self.context_window <= 0
            or self.output_reserve < 0
            or self.framing_reserve < 0
            or not self.estimator_version
        ):
            raise ProjectionError("invalid_provider_budget")

    @property
    def input_limit(self) -> int:
        result = self.context_window - self.output_reserve - self.framing_reserve
        if result < 0:
            raise ProjectionError("invalid_provider_budget")
        return result


def conservative_units(raw: bytes) -> int:
    # Every UTF-8 byte is charged as one estimator unit. This deliberately
    # overestimates common model tokenizers and is stable without provider code.
    return len(raw)


def canonical_messages(messages: tuple[FrozenMessage, ...]) -> bytes:
    return canonical_json([message.canonical_value() for message in messages])


def surface_input_limit(providers: tuple[ProviderBudget, ...]) -> int:
    if not providers:
        raise ProjectionError("provider_chain_empty")
    return min(PRODUCT_INPUT_CAP, *(provider.input_limit for provider in providers))


def optional_shares(remainder: int) -> tuple[dict[str, int], int]:
    if remainder < 0:
        raise ProjectionError("mandatory_surface_over_budget")
    shares = {
        "scope": remainder * 25 // 100,
        "attachments": remainder * 35 // 100,
        "history": remainder * 40 // 100,
    }
    return shares, remainder - sum(shares.values())
