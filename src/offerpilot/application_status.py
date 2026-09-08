from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ApplicationStatus:
    value: str
    label: str
    color: str


APPLICATION_STATUSES: tuple[ApplicationStatus, ...] = (
    ApplicationStatus("pending", "待投递", "#64748b"),
    ApplicationStatus("applied", "已投递", "#0284c7"),
    ApplicationStatus("written_test", "笔试", "#ea580c"),
    ApplicationStatus("interview", "面试", "#059669"),
    ApplicationStatus("offer", "Offer", "#16a34a"),
    ApplicationStatus("closed", "结束", "#475569"),
)
APPLICATION_STATUS_IDS = tuple(status.value for status in APPLICATION_STATUSES)
FIRST_STATUS_TIMESTAMP_ATTR = {
    "pending": "first_pending_at",
    "applied": "first_applied_at",
    "written_test": "first_written_test_at",
    "interview": "first_interview_at",
    "offer": "first_offer_at",
    "closed": "closed_at",
}

_LEGACY_STATUS_ALIASES = {
    "assessment": "written_test",
    "eliminated": "closed",
    "rejected": "closed",
}


def normalize_application_status(raw: str | None, *, default: str = "applied") -> str:
    value = (raw or default).strip().lower()
    value = _LEGACY_STATUS_ALIASES.get(value, value)
    if value not in APPLICATION_STATUS_IDS:
        raise ValueError(f"invalid application status: {raw}")
    return value


def application_status_options() -> list[dict[str, str]]:
    return [
        {"value": status.value, "label": status.label, "color": status.color}
        for status in APPLICATION_STATUSES
    ]


def mark_first_status_timestamp(application: object, status: str, occurred_at: datetime) -> None:
    attr = FIRST_STATUS_TIMESTAMP_ATTR[status]
    if getattr(application, attr) is None:
        setattr(application, attr, occurred_at)
