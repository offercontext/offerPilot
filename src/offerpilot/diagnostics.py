from __future__ import annotations

import json
import os
from pathlib import Path
from typing import BinaryIO, Iterator, NamedTuple, TypedDict


_LOG_READ_CHUNK_SIZE = 64 * 1024
_SNAPSHOT_ATTEMPTS = 2


class LogEntry(TypedDict):
    level: str
    message: str


class LogPage(TypedDict):
    entries: list[LogEntry]
    total: int
    limit: int
    offset: int
    has_more: bool


class _LogSnapshot(NamedTuple):
    device: int
    inode: int
    boundary: int


class _SnapshotChanged(Exception):
    pass


def append_log_entry(data_dir: Path, level: str, message: str) -> None:
    path = _log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"level": level.upper(), "message": message}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_recent_log_entries(data_dir: Path, limit: int = 100, level: str = "") -> list[LogEntry]:
    return read_recent_log_page(
        data_dir,
        limit=max(1, limit),
        offset=0,
        level=level,
    )["entries"]


def read_recent_log_page(data_dir: Path, *, limit: int, offset: int, level: str = "") -> LogPage:
    path = _log_path(data_dir)
    normalized_level = level.strip().upper()
    for _ in range(_SNAPSHOT_ATTEMPTS):
        try:
            with _open_log_file(path) as handle:
                snapshot = _capture_snapshot(handle)
                if normalized_level:
                    total = _count_valid_log_rows(handle, snapshot.boundary, normalized_level)
                else:
                    total = _count_valid_log_rows(handle, snapshot.boundary)
                if not _snapshot_is_usable(handle, snapshot):
                    continue
                if offset >= total:
                    return _empty_log_page(total, limit, offset)

                if normalized_level:
                    entries = _read_log_page_entries(handle, snapshot.boundary, limit, offset, normalized_level)
                else:
                    entries = _read_log_page_entries(handle, snapshot.boundary, limit, offset)
                if not _snapshot_is_usable(handle, snapshot):
                    continue
                return {
                    "entries": entries,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": total > offset + len(entries),
                }
        except FileNotFoundError:
            return _empty_log_page(0, limit, offset)
        except _SnapshotChanged:
            continue

    return _empty_log_page(0, limit, offset)


def _log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "offerpilot.log"


def _open_log_file(path: Path) -> BinaryIO:
    return path.open("rb")


def _capture_snapshot(handle: BinaryIO) -> _LogSnapshot:
    stat = os.fstat(handle.fileno())
    return _LogSnapshot(device=stat.st_dev, inode=stat.st_ino, boundary=stat.st_size)


def _snapshot_is_usable(handle: BinaryIO, snapshot: _LogSnapshot) -> bool:
    stat = os.fstat(handle.fileno())
    return (
        stat.st_dev == snapshot.device
        and stat.st_ino == snapshot.inode
        and stat.st_size >= snapshot.boundary
    )


def _empty_log_page(total: int, limit: int, offset: int) -> LogPage:
    return {
        "entries": [],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": False,
    }


def _count_valid_log_rows(handle: BinaryIO, boundary: int, level: str = "") -> int:
    return sum(
        entry is not None and _matches_level(entry, level)
        for raw in _iter_snapshot_lines_forward(handle, boundary)
        if (entry := _parse_log_entry(raw)) is not None
    )


def _read_log_page_entries(
    handle: BinaryIO,
    boundary: int,
    limit: int,
    offset: int,
    level: str = "",
) -> list[LogEntry]:
    if limit <= 0:
        return []

    entries_newest_first: list[LogEntry] = []
    skipped = 0
    for raw in _iter_snapshot_lines_reverse(handle, boundary):
        entry = _parse_log_entry(raw)
        if entry is None or not _matches_level(entry, level):
            continue
        if skipped < offset:
            skipped += 1
            continue
        _append_page_entry(entries_newest_first, entry, limit)
        if len(entries_newest_first) == limit:
            break
    return list(reversed(entries_newest_first))


def _append_page_entry(entries: list[LogEntry], entry: LogEntry, limit: int) -> None:
    if len(entries) < limit:
        entries.append(entry)


def _iter_snapshot_lines_forward(handle: BinaryIO, boundary: int) -> Iterator[bytes]:
    handle.seek(0)
    pending = b""
    remaining = boundary
    while remaining:
        read_size = min(_LOG_READ_CHUNK_SIZE, remaining)
        chunk = handle.read(read_size)
        if len(chunk) != read_size:
            raise _SnapshotChanged
        remaining -= read_size
        lines = (pending + chunk).split(b"\n")
        pending = lines.pop()
        yield from lines
    if pending:
        yield pending


def _iter_snapshot_lines_reverse(handle: BinaryIO, boundary: int) -> Iterator[bytes]:
    pending = b""
    position = boundary
    while position:
        read_size = min(_LOG_READ_CHUNK_SIZE, position)
        position -= read_size
        handle.seek(position)
        chunk = handle.read(read_size)
        if len(chunk) != read_size:
            raise _SnapshotChanged
        lines = (chunk + pending).split(b"\n")
        pending = lines[0]
        yield from reversed(lines[1:])
    if pending:
        yield pending


def _matches_level(entry: LogEntry, level: str) -> bool:
    return not level or entry["level"].upper() == level


def _parse_log_entry(raw: bytes) -> LogEntry | None:
    parsed = _parse_log_object(raw)
    if parsed is None:
        return None
    return {
        "level": str(parsed.get("level") or "INFO"),
        "message": str(parsed.get("message") or ""),
    }


def _parse_log_object(raw: bytes) -> dict[object, object] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None
