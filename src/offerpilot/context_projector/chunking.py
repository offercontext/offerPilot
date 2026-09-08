from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from offerpilot.context_projector.contracts import ProjectionError, SourceChunk

DEFAULT_CHUNK_BYTE_CAP = 4_096
MAX_CHUNKS_PER_SOURCE = 32


def chunk_structured_source(
    value: Mapping[str, Any],
    *,
    byte_cap: int = DEFAULT_CHUNK_BYTE_CAP,
    max_chunks: int = MAX_CHUNKS_PER_SOURCE,
) -> tuple[SourceChunk, ...]:
    if byte_cap < 64 or max_chunks < 1 or max_chunks > MAX_CHUNKS_PER_SOURCE:
        raise ProjectionError("invalid_chunk_policy")
    leaves: list[tuple[str, str]] = []
    _collect_leaves(value, "$", leaves)
    pieces: list[tuple[str, str, int, int]] = []
    for path, text in leaves:
        raw_bytes = len(text.encode("utf-8"))
        codepoints = len(text)
        for piece in _split_utf8(text, byte_cap):
            pieces.append((path, piece, raw_bytes, codepoints))
    omitted = len(pieces) > max_chunks
    pieces = pieces[:max_chunks]
    total = len(pieces)
    return tuple(
        SourceChunk(
            path=path,
            ordinal=index,
            total=total,
            text=text,
            truncated=omitted and index == total,
            original_bytes=original_bytes,
            original_codepoints=original_codepoints,
        )
        for index, (path, text, original_bytes, original_codepoints) in enumerate(pieces, start=1)
    )


def _collect_leaves(value: Any, path: str, output: list[tuple[str, str]]) -> None:
    if type(value) is dict or isinstance(value, Mapping):
        for key in sorted(value):
            if type(key) is not str:
                raise ProjectionError("non_string_source_path")
            _collect_leaves(value[key], f"{path}.{key}", output)
        return
    if type(value) is list or type(value) is tuple:
        for index, item in enumerate(value):
            _collect_leaves(item, f"{path}[{index}]", output)
        return
    if value is None:
        output.append((path, "null"))
    elif type(value) is bool:
        output.append((path, "true" if value else "false"))
    elif type(value) in {str, int, float}:
        output.append((path, str(value)))
    else:
        raise ProjectionError("non_primitive_source_value")


def _split_utf8(value: str, byte_cap: int) -> tuple[str, ...]:
    if not value:
        return ("",)
    pieces: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        width = len(character.encode("utf-8"))
        if width > byte_cap:
            raise ProjectionError("invalid_utf8_chunk")
        if current and current_bytes + width > byte_cap:
            pieces.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += width
    if current:
        pieces.append("".join(current))
    return tuple(pieces)
