"""Portable archive names; validate the complete index before extracting anything."""

from __future__ import annotations

import stat
import unicodedata
from zipfile import ZipInfo

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
}


def archive_parts(name: str, *, directory: bool = False) -> tuple[str, ...]:
    """Reject names that escape or alias paths on supported desktop platforms."""
    if directory and name.endswith("/"):
        name = name[:-1]
    parts = tuple(name.split("/"))
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.endswith((" ", "."))
            or any(ord(char) < 32 or ord(char) == 127 or char in '\\:*?"<>|' for char in part)
            or part.split(".", 1)[0].rstrip(" ").upper() in _RESERVED
        ):
            raise ValueError("unsafe_archive_path")
    return parts


def validate_archive_index(entries: list[ZipInfo], *, max_entries: int = 20_000) -> None:
    """Reject duplicates, file/directory conflicts, links and special entries."""
    if len(entries) > max_entries:
        raise ValueError("archive_entry_limit")
    paths: dict[tuple[str, ...], bool] = {}
    parents: set[tuple[str, ...]] = set()
    for entry in entries:
        # ZipInfo.filename silently truncates at NUL; inspect the original too.
        if entry.orig_filename != entry.filename or entry.flag_bits & 1:
            raise ValueError("unsafe_archive_entry")
        directory = entry.is_dir()
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFDIR if directory else stat.S_IFREG):
            raise ValueError("unsafe_archive_entry")
        key = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in archive_parts(entry.filename, directory=directory)
        )
        if key in paths or (not directory and key in parents):
            raise ValueError("conflicting_archive_path")
        for length in range(1, len(key)):
            parent = key[:length]
            if parent in paths and not paths[parent]:
                raise ValueError("conflicting_archive_path")
            parents.add(parent)
        paths[key] = directory
