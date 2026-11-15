"""Offline, annotated event projection for one frozen compiler-template pair.

Metadata is a trusted annotation supplied by the archive fixture generator, not
inferred from text and not independently authenticated by this pure function.
No model or fuzzy matching. Original strings and provenance remain in mappings.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import regex  # type: ignore[import-untyped]

SHORT = '用户在应用内为这次活动完成了选颜色这一步，选择内容是"蓝色"。只证明这一步，不证明共同制作或整项完成。'
LONG = '用户曾在应用内为这次活动中的纸小船完成选颜色，选择内容是"蓝色"。这只证明选择这一步，不证明共同制作或整项完成，也不是长期偏好。'
IDENTITY = ("source_message_id", "conversation_id", "turn", "activity_id", "step")
FACT = ("value", "status", "time")
FIELDS = frozenset((*IDENTITY, *FACT, "time_source"))


class ProjectionError(ValueError):
    """Constant diagnostic codes; never echo untrusted text."""


def _text(value: Any, maximum: int = 65536) -> bool:
    return (type(value) is str and 0 < len(value) <= maximum
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _event(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) - FIELDS:
        raise ProjectionError("event_schema")
    for key, item in value.items():
        if item is None:
            continue  # Explicitly unknown is not permission to merge.
        if key in IDENTITY:
            valid = ((type(item) is int and 0 <= item < 2**63)
                     or (_text(item, 128) and not item.isspace()))
        else:
            valid = _text(item, 512) and not item.isspace()
        if not valid:
            raise ProjectionError("event_type")
    return deepcopy(value)


def project(text: str, segments: list[dict[str, Any]], *, source_id: str = "reference") -> dict[str, Any]:
    """Only remove SHORT if its fully identified event matches the complete LONG.

    Unknown annotations remain unchanged. Conflicting known facts on the same
    complete event identity are rejected. Different event/time records are not
    collapsed. Maps cover every original code point, including removed spans.
    """
    if not _text(text) or type(source_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id):
        raise ProjectionError("source_invalid")
    if type(segments) is not list or len(segments) > 64:
        raise ProjectionError("segments_invalid")
    boundaries = {0, *(match.end() for match in regex.finditer(r"\X", text))}
    rows: list[dict[str, Any]] = []
    for item in segments:
        if type(item) is not dict or set(item) != {"start", "end", "text", "event"}:
            raise ProjectionError("segment_schema")
        start, end = item["start"], item["end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or type(item["text"]) is not str or text[start:end] != item["text"]):
            raise ProjectionError("span_invalid")
        if start not in boundaries or end not in boundaries:
            raise ProjectionError("span_splits_grapheme")
        rows.append({"start": start, "end": end, "text": item["text"], "event": _event(item["event"])})
    rows.sort(key=lambda row: row["start"])
    if any(left["end"] > right["start"] for left, right in zip(rows, rows[1:])):
        raise ProjectionError("spans_overlap")

    groups: dict[tuple[Any, ...], list[int]] = {}
    for index, row in enumerate(rows):
        event = row["event"]
        if all(event.get(key) is not None for key in IDENTITY):
            # Include types: integer identity 7 and string identity "7" are not conflated.
            identity = tuple((type(event[key]).__name__, event[key]) for key in IDENTITY)
            groups.setdefault(identity, []).append(index)
    removed: dict[int, int] = {}
    for indices in groups.values():
        for key in FACT:
            known = {rows[i]["event"][key] for i in indices if rows[i]["event"].get(key) is not None}
            if len(known) > 1:
                raise ProjectionError("event_fact_conflict")
        if len(indices) != 2:
            continue
        first, second = indices
        if {rows[first]["text"], rows[second]["text"]} != {SHORT, LONG}:
            continue
        if not all(all(rows[i]["event"].get(key) is not None for key in FIELDS) for i in indices):
            continue
        event = rows[first]["event"]
        if (event["step"], event["value"], event["status"]) != ("选颜色", "蓝色", "completed"):
            raise ProjectionError("template_fact_mismatch")
        short = first if rows[first]["text"] == SHORT else second
        long = second if short == first else first
        removed[short] = long

    pieces: list[str] = []
    mappings: list[dict[str, Any]] = []
    position, offset = 0, 0
    kept_views: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if position < row["start"]:
            literal = text[position:row["start"]]
            pieces.append(literal)
            mappings.append({"status": "passthrough", "original": {"source_id": source_id,
                "start": position, "end": row["start"], "text": literal, "event": None},
                "view": {"start": offset, "end": offset + len(literal), "text": literal}})
            offset += len(literal)
        original = {"source_id": source_id, **deepcopy(row)}
        if index not in removed:
            literal = row["text"]
            pieces.append(literal)
            kept_views[index] = {"start": offset, "end": offset + len(literal), "text": literal}
            mappings.append({"status": "kept", "original": original, "view": kept_views[index].copy()})
            offset += len(literal)
        else:
            mappings.append({"status": "merged", "original": original, "kept_index": removed[index]})
        position = row["end"]
    if position < len(text):
        literal = text[position:]
        pieces.append(literal)
        mappings.append({"status": "passthrough", "original": {"source_id": source_id,
            "start": position, "end": len(text), "text": literal, "event": None},
            "view": {"start": offset, "end": offset + len(literal), "text": literal}})
    for mapping in mappings:
        if mapping["status"] == "merged":
            mapping["view"] = kept_views[mapping.pop("kept_index")].copy()
    return {"view": "".join(pieces), "mappings": mappings, "merged_count": len(removed)}


def quote_origin(mapping: dict[str, Any], located: dict[str, Any]) -> dict[str, Any]:
    """Exact character mapping only through kept text, never the merged lineage alias."""
    if (
        located.get("source_id") != "reference"
        or type(located.get("start")) is not int
        or type(located.get("end")) is not int
        or type(located.get("quote")) is not str
        or not 0 <= located["start"] < located["end"]
        or located["end"] - located["start"] != len(located["quote"])
    ):
        raise ValueError("quote_origin_input")
    matches = [
        row
        for row in mapping["mappings"]
        if row["status"] in ("kept", "passthrough")
        and row["view"]["start"] <= located["start"] < located["end"] <= row["view"]["end"]
    ]
    if len(matches) != 1:
        raise ValueError("quote_crosses_projection_boundary")
    row = matches[0]
    offset = located["start"] - row["view"]["start"]
    start = row["original"]["start"] + offset
    if row["original"]["text"][offset : offset + len(located["quote"])] != located["quote"]:
        raise ValueError("quote_origin_mismatch")
    return {
        "source_id": row["original"]["source_id"],
        "start": start,
        "end": start + len(located["quote"]),
        "quote": located["quote"],
    }

