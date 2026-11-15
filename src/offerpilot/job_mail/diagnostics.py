"""Bounded, content-free observations stored only in the existing local run record.

None means not observed, not zero. These observations cannot establish why a
past run was empty. No folder names, message identifiers, headers, bodies,
protocol text, or arbitrary exception strings belong in this schema.
"""
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Literal

Stage = Literal["read", "connect", "select", "search", "metadata", "body", "parse", "complete"]
STAGES = {"read", "connect", "select", "search", "metadata", "body", "parse", "complete"}
ERROR_CODES = {
    "mail_connection_failed", "mail_cancelled", "invalid_folder", "folder_unavailable",
    "folder_identity_unavailable", "folder_search_failed", "message_metadata_failed",
    "message_date_missing", "message_too_large", "message_fetch_failed", "mail_read_failed",
    "secure_connection_unavailable",
}


def number(value: object) -> int | None:
    # IMAP UIDs are unsigned 32-bit. Also bound counters; reject bool and text.
    return value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None


def uid_number(value: object) -> int | None:
    if type(value) is str and value.isascii() and value.isdigit() and len(value) <= 10:
        return number(int(value))
    return number(value)


def utc(value: object) -> str | None:
    if type(value) is not datetime:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def error_code(value: object) -> str:
    return value if type(value) is str and value in ERROR_CODES else "mail_read_failed"


@dataclass
class ReadDiagnostics:
    stage: Stage = "read"
    error_code: str | None = None
    server_uidvalidity: int | None = None
    # Derived from the existing SELECT high-water mark, not an extra response read.
    server_uidnext: int | None = None
    effective_after_uid: int | None = None
    uidvalidity_reset: bool | None = None
    search_returned: int | None = None
    uid_filtered: int | None = None
    eligible_uids: int | None = None
    batch_limit_deferred: int | None = None
    metadata_checked: int | None = None
    before_since: int | None = None
    after_until_observed: int | None = None
    body_fetched: int | None = None
    messages_returned: int | None = None
    last_uid: int | None = None
    has_more: bool | None = None

    def safe_dict(self) -> dict[str, object]:
        # Explicit fields only, even if a plugin adds arbitrary attributes.
        result: dict[str, object] = {}
        for field in fields(ReadDiagnostics):
            value = getattr(self, field.name, None)
            if field.name == "stage":
                result[field.name] = value if type(value) is str and value in STAGES else "read"
            elif field.name == "error_code":
                result[field.name] = None if value is None else error_code(value)
            elif field.name in {"has_more", "uidvalidity_reset"}:
                result[field.name] = value if type(value) is bool else None
            else:
                result[field.name] = number(value)
        return result
