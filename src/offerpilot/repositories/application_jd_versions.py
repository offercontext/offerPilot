from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session, sessionmaker
from offerpilot.repositories.session_binding import finish_repository_write, repository_session

from offerpilot.models import Application, ApplicationJDVersion


IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_JD_UTF8_BYTES = 60_000
MAX_SOURCE_URL_LENGTH = 2_048
MAX_HISTORY_LIMIT = 200


class JDVersionError(ValueError):
    def __init__(self, message: str, code: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class JDVersionValidationError(JDVersionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "application_jd_invalid_request", 422)


class JDVersionNotFoundError(JDVersionError):
    def __init__(self, message: str = "application or JD version not found") -> None:
        super().__init__(message, "application_jd_not_found", 404)


class JDVersionConflictError(JDVersionError):
    def __init__(self, message: str, code: str = "application_jd_source_conflict") -> None:
        super().__init__(message, code, 409)


@dataclass(frozen=True)
class ApplicationJDVersionSummary:
    id: int
    application_id: int
    version_number: int
    content_sha256: str
    source_url: str | None
    source_kind: Literal["ui", "pilot"]
    utf8_byte_length: int
    preview: str
    created_at: datetime


@dataclass(frozen=True)
class FrozenApplicationJD:
    jd_version_id: int
    application_id: int
    version_number: int
    jd_text: str
    content_sha256: str
    source_url: str | None
    source_kind: Literal["ui", "pilot"]


@dataclass(frozen=True)
class VersionCreateResult:
    version: ApplicationJDVersion
    replayed: bool


def _canonical_request_fingerprint(
    jd_text: str,
    source_url: str | None,
    source_kind: str,
) -> str:
    payload = {
        "jd_text_utf8_b64": base64.b64encode(jd_text.encode("utf-8")).decode("ascii"),
        "source_kind": source_kind,
        "source_url": source_url,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _content_sha256(jd_text: str) -> str:
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()


def _preview(jd_text: str) -> str:
    if len(jd_text) <= 240:
        return jd_text
    return jd_text[:240] + "…"


def _normalize_source_url(source_url: str | None) -> str | None:
    if source_url is None:
        return None
    if not isinstance(source_url, str):
        raise JDVersionValidationError("source_url must be a string or null")
    normalized = source_url.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_SOURCE_URL_LENGTH:
        raise JDVersionValidationError("source_url is too long")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
    except ValueError as exc:
        raise JDVersionValidationError("source_url is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise JDVersionValidationError("source_url must be an absolute http or https URL")
    return normalized


def _validate_idempotency_key(idempotency_key: str) -> None:
    if not isinstance(idempotency_key, str) or IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key) is None:
        raise JDVersionValidationError("idempotency_key is invalid")


def _validate_expected_current_version_id(expected: int | None) -> None:
    if expected is not None and (type(expected) is not int or expected <= 0):
        raise JDVersionValidationError("expected_current_version_id must be a positive integer or null")


def _validate_jd_text(jd_text: str) -> None:
    if not isinstance(jd_text, str) or not jd_text.strip():
        raise JDVersionValidationError("jd_text is required")
    if len(jd_text.encode("utf-8")) > MAX_JD_UTF8_BYTES:
        raise JDVersionValidationError("jd_text is too large")


class ApplicationJDService:
    def __init__(self, session_factory: sessionmaker[Session], session: Session | None = None):
        self._session_factory = session_factory
        self._session = session

    def bind(self, session: Session) -> "ApplicationJDService":
        return ApplicationJDService(self._session_factory, session)

    def get_current(self, application_id: int) -> ApplicationJDVersion | None:
        with repository_session(self._session_factory, self._session) as session:
            return session.scalar(
                select(ApplicationJDVersion)
                .where(ApplicationJDVersion.application_id == application_id)
                .order_by(desc(ApplicationJDVersion.version_number))
                .limit(1)
            )

    def require_current_version(
        self,
        application_id: int,
        requested_jd_version_id: int,
    ) -> ApplicationJDVersion:
        if type(requested_jd_version_id) is not int or requested_jd_version_id <= 0:
            raise JDVersionValidationError("requested JD version must be a positive integer")
        with self._session_factory() as session:
            version = session.scalar(
                select(ApplicationJDVersion).where(
                    ApplicationJDVersion.id == requested_jd_version_id,
                    ApplicationJDVersion.application_id == application_id,
                )
            )
            current = session.scalar(
                select(ApplicationJDVersion)
                .where(ApplicationJDVersion.application_id == application_id)
                .order_by(desc(ApplicationJDVersion.version_number))
                .limit(1)
            )
            if version is None or current is None or current.id != requested_jd_version_id:
                raise JDVersionConflictError("requested JD version is not current")
            return version

    def list_versions(
        self,
        application_id: int,
        offset: int,
        limit: int,
    ) -> list[ApplicationJDVersionSummary]:
        if type(offset) is not int or offset < 0:
            raise JDVersionValidationError("offset is invalid")
        if type(limit) is not int or limit <= 0 or limit > MAX_HISTORY_LIMIT:
            raise JDVersionValidationError("limit is invalid")
        with self._session_factory() as session:
            versions = list(
                session.scalars(
                    select(ApplicationJDVersion)
                    .where(ApplicationJDVersion.application_id == application_id)
                    .order_by(desc(ApplicationJDVersion.version_number))
                    .offset(offset)
                    .limit(limit)
                )
            )
            return [self._summary(version) for version in versions]

    def get_version(self, application_id: int, version_id: int) -> ApplicationJDVersion | None:
        if type(version_id) is not int or version_id <= 0:
            return None
        with self._session_factory() as session:
            return session.scalar(
                select(ApplicationJDVersion).where(
                    ApplicationJDVersion.application_id == application_id,
                    ApplicationJDVersion.id == version_id,
                )
            )

    def freeze(self, version: ApplicationJDVersion) -> FrozenApplicationJD:
        return FrozenApplicationJD(
            jd_version_id=version.id,
            application_id=version.application_id,
            version_number=version.version_number,
            jd_text=version.jd_text,
            content_sha256=version.content_sha256,
            source_url=version.source_url,
            source_kind=version.source_kind,  # type: ignore[arg-type]
        )

    def create_version(
        self,
        application_id: int,
        *,
        jd_text: str,
        source_url: str | None,
        source_kind: Literal["ui", "pilot"],
        expected_current_version_id: int | None,
        idempotency_key: str,
    ) -> VersionCreateResult:
        _validate_jd_text(jd_text)
        normalized_url = _normalize_source_url(source_url)
        if source_kind not in {"ui", "pilot"}:
            raise JDVersionValidationError("source_kind is invalid")
        _validate_expected_current_version_id(expected_current_version_id)
        _validate_idempotency_key(idempotency_key)
        request_fingerprint = _canonical_request_fingerprint(jd_text, normalized_url, source_kind)
        content_sha256 = _content_sha256(jd_text)

        with repository_session(self._session_factory, self._session) as session:
            if self._session is None:
                session.execute(text("BEGIN IMMEDIATE"))
            application = session.get(Application, application_id)
            if application is None or application.deleted_at is not None:
                raise JDVersionNotFoundError("application is not visible")

            existing = session.scalar(
                select(ApplicationJDVersion).where(
                    ApplicationJDVersion.application_id == application_id,
                    ApplicationJDVersion.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint_sha256 != request_fingerprint:
                    raise JDVersionConflictError(
                        "idempotency key was used with different input",
                        "application_jd_idempotency_conflict",
                    )
                finish_repository_write(session, self._session)
                return VersionCreateResult(existing, replayed=True)

            current = session.scalar(
                select(ApplicationJDVersion)
                .where(ApplicationJDVersion.application_id == application_id)
                .order_by(desc(ApplicationJDVersion.version_number))
                .limit(1)
            )
            current_id = current.id if current is not None else None
            if current_id != expected_current_version_id:
                raise JDVersionConflictError(
                    "current JD version changed",
                    "application_jd_stale_current_version",
                )

            version = ApplicationJDVersion(
                application_id=application_id,
                version_number=(current.version_number + 1) if current is not None else 1,
                jd_text=jd_text,
                content_sha256=content_sha256,
                source_url=normalized_url,
                source_kind=source_kind,
                idempotency_key=idempotency_key,
                request_fingerprint_sha256=request_fingerprint,
            )
            session.add(version)
            finish_repository_write(session, self._session)
            session.refresh(version)
            return VersionCreateResult(version, replayed=False)

    @staticmethod
    def _summary(version: ApplicationJDVersion) -> ApplicationJDVersionSummary:
        return ApplicationJDVersionSummary(
            id=version.id,
            application_id=version.application_id,
            version_number=version.version_number,
            content_sha256=version.content_sha256,
            source_url=version.source_url,
            source_kind=version.source_kind,  # type: ignore[arg-type]
            utf8_byte_length=len(version.jd_text.encode("utf-8")),
            preview=_preview(version.jd_text),
            created_at=version.created_at,
        )
