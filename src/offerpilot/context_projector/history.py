from __future__ import annotations

from dataclasses import dataclass

from offerpilot.context_projector.budget import (
    OPTIONAL_HISTORY_MESSAGE_BYTE_CAP,
    canonical_messages,
)
from offerpilot.context_projector.contracts import (
    FrozenMessage,
    ProjectionError,
    canonical_json,
    sha256_hex,
)

RELEVANCE_VERSION = "bounded-bilingual-relevance-v1"


@dataclass(frozen=True)
class TurnGroup:
    group_id: str
    messages: tuple[FrozenMessage, ...]
    first_message_id: int
    last_message_id: int
    oversized: bool = False

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_messages(self.messages)


def _validate_tool_chain(messages: tuple[FrozenMessage, ...]) -> None:
    pending: dict[str, int] = {}
    resolved: set[str] = set()
    for message in messages:
        if message.role == "assistant":
            for call in message.tool_calls:
                if not call.id or call.id in pending or call.id in resolved:
                    raise ProjectionError("duplicate_tool_call")
                pending[call.id] = 0
        elif message.role == "tool":
            if not message.tool_call_id or message.tool_call_id not in pending:
                raise ProjectionError("orphan_tool_message")
            pending.pop(message.tool_call_id)
            resolved.add(message.tool_call_id)
    if pending:
        raise ProjectionError("dangling_tool_call")


def validate_message_integrity(messages: tuple[FrozenMessage, ...]) -> None:
    _validate_tool_chain(messages)


def group_history(
    messages: tuple[FrozenMessage, ...], *, legacy_orphan_compat: bool = False
) -> tuple[TurnGroup, ...]:
    groups: list[list[FrozenMessage]] = []
    current: list[FrozenMessage] = []
    for message in messages:
        if message.role == "user" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    result: list[TurnGroup] = []
    for ordinal, raw_group in enumerate(groups, start=1):
        frozen = tuple(raw_group)
        try:
            _validate_tool_chain(frozen)
        except ProjectionError:
            if legacy_orphan_compat:
                # Old persisted chains predate the typed contract. Omitting the
                # whole atomic group is the only projection that neither leaves
                # an orphan nor invents a successful result.
                continue
            raise
        if frozen[0].role != "user":
            if legacy_orphan_compat:
                continue
            raise ProjectionError("turn_group_missing_user")
        message_ids = [
            message.source_message_id for message in frozen if message.source_message_id > 0
        ]
        first = min(message_ids) if message_ids else ordinal
        last = max(message_ids) if message_ids else ordinal
        oversized = any(
            len(canonical_json(message.canonical_value()))
            > OPTIONAL_HISTORY_MESSAGE_BYTE_CAP
            for message in frozen
        )
        group_id = (
            sha256_hex(f"oversized:{first}:{last}".encode())[:24]
            if oversized
            else sha256_hex(canonical_messages(frozen))[:24]
        )
        result.append(TurnGroup(group_id, frozen, first, last, oversized))
    return tuple(result)


def relevance_score(group: TurnGroup, current_request: str) -> int:
    request_terms = _terms(current_request)
    if not request_terms:
        return 0
    group_text = " ".join(message.content for message in group.messages)[:65_536]
    group_terms = _terms(group_text)
    overlap = len(request_terms.intersection(group_terms))
    structural = 2 if any(message.tool_calls for message in group.messages) else 0
    return min(10_000, overlap * 10 + structural)


def _terms(value: str) -> frozenset[str]:
    normalized = value.casefold()
    latin = {piece for piece in _split_ascii(normalized) if 1 < len(piece) <= 48}
    chinese = {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
        if "\u4e00" <= normalized[index] <= "\u9fff"
        and "\u4e00" <= normalized[index + 1] <= "\u9fff"
    }
    return frozenset(tuple(sorted(latin.union(chinese)))[:512])


def _split_ascii(value: str) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    for character in value:
        if character.isascii() and character.isalnum():
            current.append(character)
        elif current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    return pieces


def select_history(
    groups: tuple[TurnGroup, ...],
    *,
    current_request: str,
    budget_bytes: int,
) -> tuple[TurnGroup, ...]:
    if budget_bytes < 0:
        raise ProjectionError("invalid_history_budget")
    ranked = rank_history(groups, current_request=current_request)
    selected: list[TurnGroup] = []
    used = 2  # canonical list brackets
    for group in ranked:
        cost = len(group.canonical_bytes) + (1 if selected else 0)
        if used + cost <= budget_bytes:
            selected.append(group)
            used += cost
    return tuple(
        sorted(selected, key=lambda group: (group.first_message_id, group.last_message_id))
    )


def rank_history(groups: tuple[TurnGroup, ...], *, current_request: str) -> tuple[TurnGroup, ...]:
    eligible = tuple(group for group in groups if not group.oversized)
    recent = list(reversed(eligible[-2:]))
    recent_ids = {group.group_id for group in recent}
    relevant = sorted(
        (group for group in eligible if group.group_id not in recent_ids),
        key=lambda group: (relevance_score(group, current_request), group.last_message_id),
        reverse=True,
    )
    return tuple((*recent, *relevant))
