"""User-facing resume deletion policy shared by HTTP and external proposals."""

from typing import Any

from offerpilot.schemas import normalize_resume_content


def content_has_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(content_has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(content_has_value(item) for item in value)
    return bool(str(value or "").strip())


def is_empty_resume_draft(resume: Any) -> bool:
    return not str(resume.parsed_data or "").strip() and not content_has_value(
        normalize_resume_content(resume.content_json)
    )
