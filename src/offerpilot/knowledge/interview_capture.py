from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CAPTURE_SCHEMA_VERSION = "interview-note-capture-v1"
MAX_FRAGMENTS = 20
MAX_FRAGMENT_UTF8_BYTES = 4096
MAX_TOTAL_UTF8_BYTES = 32768
ALLOWED_FRAGMENT_PATHS = (
    "/questions",
    "/self_reflection",
    "/difficulty_points",
    "/mood",
)


class FragmentValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SelectedFragment:
    fragment_id: str
    path: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class CanonicalFragment:
    fragment_id: str
    path: str
    start: int
    end: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "path": self.path,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


@dataclass(frozen=True)
class SnapshotFragmentRange:
    fragment_id: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    excerpt: str


def slice_utf16(value: str, start: int, end: int) -> str:
    if not isinstance(value, str) or not isinstance(start, int) or not isinstance(end, int):
        raise FragmentValidationError("utf16 range must use integer offsets")
    if start < 0 or end <= start:
        raise FragmentValidationError("invalid utf16 range")
    encoded = value.encode("utf-16-le")
    total_units = len(encoded) // 2
    if end > total_units:
        raise FragmentValidationError("utf16 range is out of bounds")
    start_bytes = start * 2
    end_bytes = end * 2
    try:
        return encoded[start_bytes:end_bytes].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise FragmentValidationError("utf16 range splits a surrogate pair") from exc


def _as_selected_fragment(raw: SelectedFragment | Mapping[str, Any]) -> SelectedFragment:
    if isinstance(raw, SelectedFragment):
        return raw
    if not isinstance(raw, Mapping):
        raise FragmentValidationError("fragment must be an object")
    try:
        return SelectedFragment(
            fragment_id=raw["fragment_id"],
            path=raw["path"],
            start=raw["start"],
            end=raw["end"],
            text=raw["text"],
        )
    except (KeyError, TypeError) as exc:
        raise FragmentValidationError("fragment fields are required") from exc


def canonicalize_fragments(
    raw: Sequence[SelectedFragment | Mapping[str, Any]],
    source_fields: Mapping[str, str],
) -> list[CanonicalFragment]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) == 0:
        raise FragmentValidationError("fragment_count must be between 1 and 20")
    if len(raw) > MAX_FRAGMENTS:
        raise FragmentValidationError("fragment_count exceeds limit")
    fragments = [_as_selected_fragment(item) for item in raw]
    seen_ids: set[str] = set()
    total_bytes = 0
    validated: list[SelectedFragment] = []
    for fragment in fragments:
        if (
            not isinstance(fragment.fragment_id, str)
            or not fragment.fragment_id.strip()
            or fragment.fragment_id in seen_ids
        ):
            raise FragmentValidationError("fragment_id must be unique")
        seen_ids.add(fragment.fragment_id)
        if fragment.path not in ALLOWED_FRAGMENT_PATHS:
            raise FragmentValidationError("fragment path is not allowed")
        if fragment.path not in source_fields or not isinstance(source_fields[fragment.path], str):
            raise FragmentValidationError("fragment source field is unavailable")
        if not isinstance(fragment.text, str) or not fragment.text:
            raise FragmentValidationError("fragment text must be non-empty")
        expected = slice_utf16(source_fields[fragment.path], fragment.start, fragment.end)
        if expected != fragment.text:
            raise FragmentValidationError("fragment text does not match source")
        byte_count = len(fragment.text.encode("utf-8"))
        if byte_count > MAX_FRAGMENT_UTF8_BYTES:
            raise FragmentValidationError("fragment utf8_bytes exceeds limit")
        total_bytes += byte_count
        validated.append(fragment)
    if total_bytes > MAX_TOTAL_UTF8_BYTES:
        raise FragmentValidationError("total_utf8_bytes exceeds limit")

    path_order = {path: index for index, path in enumerate(ALLOWED_FRAGMENT_PATHS)}
    validated.sort(key=lambda item: (path_order[item.path], item.start, item.end))
    previous_by_path: dict[str, SelectedFragment] = {}
    for fragment in validated:
        previous = previous_by_path.get(fragment.path)
        if previous is not None and fragment.start < previous.end:
            raise FragmentValidationError("fragment ranges overlap")
        previous_by_path[fragment.path] = fragment
    return [
        CanonicalFragment(
            fragment_id=f"fragment_{index:03d}",
            path=fragment.path,
            start=fragment.start,
            end=fragment.end,
            text=fragment.text,
        )
        for index, fragment in enumerate(validated, start=1)
    ]


def serialize_capture_snapshot_with_ranges(
    fragments: list[CanonicalFragment],
) -> tuple[bytes, dict[str, SnapshotFragmentRange]]:
    if not fragments:
        raise FragmentValidationError("snapshot requires fragments")
    payload = bytearray(f"{CAPTURE_SCHEMA_VERSION}\n".encode("ascii"))
    ranges: dict[str, SnapshotFragmentRange] = {}
    for ordinal, fragment in enumerate(fragments, start=1):
        text_bytes = fragment.text.encode("utf-8")
        payload.extend(f"fragment={ordinal}\n".encode("ascii"))
        payload.extend(f"path={fragment.path}\n".encode("ascii"))
        payload.extend(f"start={fragment.start}\n".encode("ascii"))
        payload.extend(f"end={fragment.end}\n".encode("ascii"))
        payload.extend(f"bytes={len(text_bytes)}\n".encode("ascii"))
        payload.extend(b"text-bytes=")
        prefix_text = bytes(payload).decode("utf-8")
        char_start = len(prefix_text)
        line_start = prefix_text.count("\n") + 1
        payload.extend(text_bytes)
        char_end = char_start + len(fragment.text)
        line_end = line_start + fragment.text.count("\n")
        ranges[fragment.fragment_id] = SnapshotFragmentRange(
            fragment_id=fragment.fragment_id,
            char_start=char_start,
            char_end=char_end,
            line_start=line_start,
            line_end=line_end,
            excerpt=fragment.text,
        )
        payload.extend(b"\nseparator=single LF\n")
    snapshot_text = bytes(payload).decode("utf-8")
    for item in ranges.values():
        if snapshot_text[item.char_start : item.char_end] != item.excerpt:
            raise FragmentValidationError("snapshot range does not match excerpt")
    return bytes(payload), ranges


def serialize_capture_snapshot(fragments: list[CanonicalFragment]) -> bytes:
    return serialize_capture_snapshot_with_ranges(fragments)[0]


def _read_ascii_line(payload: bytes, offset: int) -> tuple[str, int]:
    end = payload.find(b"\n", offset)
    if end < 0:
        raise FragmentValidationError("snapshot line is incomplete")
    try:
        line = payload[offset:end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise FragmentValidationError("snapshot metadata must be ASCII") from exc
    return line, end + 1


def parse_capture_snapshot(payload: bytes) -> list[CanonicalFragment]:
    if not isinstance(payload, bytes):
        raise FragmentValidationError("snapshot must be bytes")
    header, offset = _read_ascii_line(payload, 0)
    if header != CAPTURE_SCHEMA_VERSION:
        raise FragmentValidationError("snapshot version is invalid")
    result: list[CanonicalFragment] = []
    while offset < len(payload):
        fragment_line, offset = _read_ascii_line(payload, offset)
        if not fragment_line.startswith("fragment="):
            raise FragmentValidationError("snapshot field order is invalid")
        ordinal = fragment_line.removeprefix("fragment=")
        path_line, offset = _read_ascii_line(payload, offset)
        start_line, offset = _read_ascii_line(payload, offset)
        end_line, offset = _read_ascii_line(payload, offset)
        bytes_line, offset = _read_ascii_line(payload, offset)
        if not path_line.startswith("path=") or not start_line.startswith("start="):
            raise FragmentValidationError("snapshot field order is invalid")
        if not end_line.startswith("end=") or not bytes_line.startswith("bytes="):
            raise FragmentValidationError("snapshot field order is invalid")
        try:
            start = int(start_line.removeprefix("start="))
            end = int(end_line.removeprefix("end="))
            byte_count = int(bytes_line.removeprefix("bytes="))
            expected_ordinal = int(ordinal)
        except ValueError as exc:
            raise FragmentValidationError("snapshot numeric field is invalid") from exc
        marker = b"text-bytes="
        if not payload.startswith(marker, offset):
            raise FragmentValidationError("snapshot text-bytes field is missing")
        offset += len(marker)
        end_text = offset + byte_count
        if byte_count < 1 or end_text > len(payload):
            raise FragmentValidationError("snapshot text byte length is invalid")
        raw_text = payload[offset:end_text]
        offset = end_text
        if offset >= len(payload) or payload[offset : offset + 1] != b"\n":
            raise FragmentValidationError("snapshot text separator is missing")
        offset += 1
        separator_line, offset = _read_ascii_line(payload, offset)
        if separator_line != "separator=single LF":
            raise FragmentValidationError("snapshot separator is invalid")
        try:
            text_value = raw_text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FragmentValidationError("snapshot text is not valid UTF-8") from exc
        if expected_ordinal != len(result) + 1:
            raise FragmentValidationError("snapshot ordinal is invalid")
        result.append(
            CanonicalFragment(
                fragment_id=f"fragment_{expected_ordinal:03d}",
                path=path_line.removeprefix("path="),
                start=start,
                end=end,
                text=text_value,
            )
        )
    if not result:
        raise FragmentValidationError("snapshot requires fragments")
    return result


def fragments_json(fragments: list[CanonicalFragment]) -> str:
    return json.dumps([item.as_dict() for item in fragments], ensure_ascii=False, separators=(",", ":"))


def note_fingerprint(note: Any) -> str:
    values = {
        "note_id": note.id,
        "application_id": note.application_id,
        "application_event_id": note.application_event_id,
        "company": note.company,
        "position": note.position,
        "round": note.round,
        "date": note.date,
        "questions": note.questions,
        "self_reflection": note.self_reflection,
        "difficulty_points": note.difficulty_points,
        "mood": note.mood,
    }
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_fields_for_note(note: Any) -> dict[str, str]:
    return {
        "/questions": note.questions,
        "/self_reflection": note.self_reflection,
        "/difficulty_points": note.difficulty_points,
        "/mood": note.mood,
    }
