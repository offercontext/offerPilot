"""Offline-only exact quote locator; never a semantic or paid admission gate."""
from __future__ import annotations

import re
from typing import Any

# Existing locked dependency has no bundled typing stubs; no lockfile change.
import regex  # type: ignore[import-untyped]

MAX_SOURCES = 8
MAX_SOURCE_CODEPOINTS = 4096
MAX_QUOTE_CODEPOINTS = 128
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class LocatorError(ValueError):
    """Constant error codes only; no untrusted text in exceptions."""


def _text(value: Any, limit: int) -> bool:
    return (type(value) is str and 0 < len(value) <= limit
            and not value.isspace()
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def locate_quote(sources: list[dict[str, str]], source_id: str, quote: str) -> dict[str, Any]:
    """Locate exactly once, including overlapping matches, within one named source.

    Additional grapheme-boundary rejection prevents matching a fragment of an
    emoji or combining sequence. Returned offsets remain Python code points,
    not grapheme ordinals. No Unicode normalization or repair is performed.
    """
    if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
        raise LocatorError("sources_invalid")
    texts: dict[str, str] = {}
    for source in sources:
        if type(source) is not dict or set(source) != {"id", "text"}:
            raise LocatorError("source_invalid")
        key, value = source["id"], source["text"]
        if (type(key) is not str or not _ID.fullmatch(key) or key in texts
                or not _text(value, MAX_SOURCE_CODEPOINTS)):
            raise LocatorError("source_invalid")
        texts[key] = value
    if type(source_id) is not str or source_id not in texts:
        raise LocatorError("source_id_invalid")
    if not _text(quote, MAX_QUOTE_CODEPOINTS):
        raise LocatorError("quote_invalid")
    text = texts[source_id]
    start = text.find(quote)
    if start < 0:
        raise LocatorError("quote_not_found")
    if text.find(quote, start + 1) >= 0:
        raise LocatorError("quote_not_unique")
    end = start + len(quote)
    boundaries = {0}
    for cluster in regex.finditer(r"\X", text):
        boundaries.add(cluster.end())
    if start not in boundaries or end not in boundaries:
        raise LocatorError("quote_splits_grapheme")
    return {"source_id": source_id, "start": start, "end": end, "quote": quote}
