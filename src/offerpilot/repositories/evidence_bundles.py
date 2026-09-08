from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.application_status import mark_first_status_timestamp
from offerpilot.models import (
    Application,
    ApplicationEvidenceBundle,
    ApplicationEvent,
    ApplicationMaterialKit,
    Resume,
)
from offerpilot.repositories.json_contract import (
    JsonContractError,
    canonical_json as _canonical_json,
    parse_json_object as _parse_json_object,
    sha256_text,
)


_SEQUENCE_CONFLICT_ATTEMPTS = 3


class EvidenceBundleNotFound(Exception):
    pass


class EvidenceBundleValidationError(ValueError):
    pass


class EvidenceBundleConflictError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return _canonical_json(value)
    except JsonContractError as exc:
        raise EvidenceBundleValidationError(str(exc)) from exc


def parse_json_object(name: str, value: str) -> dict[str, Any]:
    try:
        return _parse_json_object(name, value)
    except JsonContractError as exc:
        raise EvidenceBundleValidationError(str(exc)) from exc


@dataclass(frozen=True)
class EvidenceBundlePreview:
    application_id: int
    ready: bool
    issues: list[str]
    bundle_sha256: str | None
    snapshot: dict[str, Any] | None


class EvidenceBundlesRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def preview(self, application_id: int) -> EvidenceBundlePreview:
        with self._session_factory() as session:
            return _build_preview(session, application_id)

    def confirm(
        self,
        application_id: int,
        submitted_at: datetime,
        idempotency_key: str,
        expected_bundle_sha256: str,
    ) -> tuple[ApplicationEvidenceBundle, bool]:
        submitted_at = _normalize_submitted_at(submitted_at)
        for attempt in range(_SEQUENCE_CONFLICT_ATTEMPTS):
            try:
                return self._confirm_once(
                    application_id,
                    submitted_at,
                    idempotency_key,
                    expected_bundle_sha256,
                )
            except IntegrityError as exc:
                existing = self._find_by_idempotency_key(application_id, idempotency_key)
                if existing is not None:
                    return existing, False
                if not _is_sequence_conflict(exc) or attempt == _SEQUENCE_CONFLICT_ATTEMPTS - 1:
                    raise

        raise AssertionError("unreachable")

    def _confirm_once(
        self,
        application_id: int,
        submitted_at: datetime,
        idempotency_key: str,
        expected_bundle_sha256: str,
    ) -> tuple[ApplicationEvidenceBundle, bool]:
        submitted_at = _normalize_submitted_at(submitted_at)
        with self._session_factory() as session:
            app = _get_visible_application(session, application_id)
            if app is None:
                raise EvidenceBundleNotFound()

            existing = session.scalar(
                select(ApplicationEvidenceBundle).where(
                    ApplicationEvidenceBundle.application_id == application_id,
                    ApplicationEvidenceBundle.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _normalize_bundle_timestamps(existing), False

            preview = _build_preview(session, application_id)
            if not preview.ready:
                raise EvidenceBundleValidationError(f"提交材料未就绪：{'；'.join(preview.issues)}")
            if preview.bundle_sha256 != expected_bundle_sha256:
                raise EvidenceBundleConflictError("提交材料已变化，请重新核对")

            try:
                max_sequence = session.scalar(
                    select(func.max(ApplicationEvidenceBundle.sequence)).where(
                        ApplicationEvidenceBundle.application_id == application_id
                    )
                )
                bundle = ApplicationEvidenceBundle(
                    application_id=application_id,
                    sequence=int(max_sequence or 0) + 1,
                    submitted_at=submitted_at,
                    confirmed_at=datetime.now(timezone.utc),
                    confirmation_kind="user_asserted",
                    idempotency_key=idempotency_key,
                    snapshot_json=canonical_json(preview.snapshot),
                    bundle_sha256=preview.bundle_sha256,
                )
                session.add(bundle)
                session.flush()

                if app.status == "pending":
                    app.status = "applied"
                    mark_first_status_timestamp(app, "applied", submitted_at)
                    app.updated_at = datetime.now(timezone.utc)

                event = ApplicationEvent(
                    application_id=application_id,
                    event_type="custom",
                    subtype="submission_confirmed",
                    scheduled_at=submitted_at,
                    duration_minutes=0,
                    status="done",
                    notes="用户确认已提交材料",
                )
                event.tags = ["submission_evidence", f"bundle:{bundle.id}"]
                session.add(event)
                session.commit()
                session.refresh(bundle)
                return _normalize_bundle_timestamps(bundle), True
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(ApplicationEvidenceBundle).where(
                        ApplicationEvidenceBundle.application_id == application_id,
                        ApplicationEvidenceBundle.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return _normalize_bundle_timestamps(existing), False
                raise

    def _find_by_idempotency_key(
        self, application_id: int, idempotency_key: str
    ) -> ApplicationEvidenceBundle | None:
        with self._session_factory() as session:
            bundle = session.scalar(
                select(ApplicationEvidenceBundle).where(
                    ApplicationEvidenceBundle.application_id == application_id,
                    ApplicationEvidenceBundle.idempotency_key == idempotency_key,
                )
            )
            return _normalize_bundle_timestamps(bundle) if bundle is not None else None

    def list(self, application_id: int) -> list[ApplicationEvidenceBundle]:
        statement = (
            select(ApplicationEvidenceBundle)
            .join(Application, Application.id == ApplicationEvidenceBundle.application_id)
            .where(ApplicationEvidenceBundle.application_id == application_id)
            .where(Application.deleted_at.is_(None))
            .order_by(ApplicationEvidenceBundle.sequence.desc())
        )
        with self._session_factory() as session:
            return [_normalize_bundle_timestamps(bundle) for bundle in session.scalars(statement)]

    def get(self, application_id: int, bundle_id: int) -> ApplicationEvidenceBundle | None:
        statement = (
            select(ApplicationEvidenceBundle)
            .join(Application, Application.id == ApplicationEvidenceBundle.application_id)
            .where(ApplicationEvidenceBundle.application_id == application_id)
            .where(ApplicationEvidenceBundle.id == bundle_id)
            .where(Application.deleted_at.is_(None))
        )
        with self._session_factory() as session:
            bundle = session.scalar(statement)
            return _normalize_bundle_timestamps(bundle) if bundle is not None else None


def _normalize_submitted_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceBundleValidationError("submitted_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _is_sequence_conflict(exc: IntegrityError) -> bool:
    return (
        "application_evidence_bundles.application_id, application_evidence_bundles.sequence"
        in str(exc.orig)
    )


def _normalize_bundle_timestamps(bundle: ApplicationEvidenceBundle) -> ApplicationEvidenceBundle:
    for attr in ("submitted_at", "confirmed_at", "created_at"):
        value = getattr(bundle, attr)
        if value.tzinfo is None or value.utcoffset() is None:
            setattr(bundle, attr, value.replace(tzinfo=timezone.utc))
        else:
            setattr(bundle, attr, value.astimezone(timezone.utc))
    return bundle


def _get_visible_application(session: Session, application_id: int) -> Application | None:
    return session.scalar(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.deleted_at.is_(None))
    )


def _build_preview(session: Session, application_id: int) -> EvidenceBundlePreview:
    app = _get_visible_application(session, application_id)
    if app is None:
        raise EvidenceBundleNotFound()

    issues: list[str] = []
    kits = list(
        session.scalars(
            select(ApplicationMaterialKit).where(
                ApplicationMaterialKit.application_id == application_id
            )
        )
    )
    if not kits:
        return EvidenceBundlePreview(application_id, False, ["缺少投递材料包"], None, None)
    if len(kits) != 1:
        return EvidenceBundlePreview(application_id, False, ["投递材料包不唯一"], None, None)

    kit = kits[0]
    if not kit.jd_snapshot.strip():
        issues.append("缺少职位描述")

    resume: Resume | None = None
    if kit.resume_id is None:
        issues.append("缺少关联简历")
    else:
        resume = session.scalar(
            select(Resume)
            .where(Resume.id == kit.resume_id)
            .where(Resume.deleted_at.is_(None))
        )
        if resume is None:
            issues.append("关联简历不存在或已删除")

    resume_content: dict[str, Any] | None = None
    if resume is not None:
        try:
            resume_content = parse_json_object("resume", resume.content_json)
        except EvidenceBundleValidationError:
            issues.append("简历内容不是 JSON 对象")

    material_kit_content: dict[str, Any] | None = None
    try:
        material_kit_content = parse_json_object("material_kit", kit.content_json)
    except EvidenceBundleValidationError:
        issues.append("材料包内容不是 JSON 对象")

    if issues:
        return EvidenceBundlePreview(application_id, False, issues, None, None)

    assert resume is not None
    assert resume_content is not None
    assert material_kit_content is not None
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "application": {
            "id": app.id,
            "company_name": app.company_name,
            "position_name": app.position_name,
            "job_url": app.job_url,
            "source": app.source,
        },
        "jd": {
            "text": kit.jd_snapshot,
            "sha256": sha256_text(kit.jd_snapshot),
            "jd_analysis_id": kit.jd_analysis_id,
        },
        "resume": {
            "resume_id": resume.id,
            "title": resume.title or resume.name,
            "content_json": resume_content,
            "sha256": sha256_text(canonical_json(resume_content)),
        },
        "material_kit": {
            "material_kit_id": kit.id,
            "content_json": material_kit_content,
            "sha256": sha256_text(canonical_json(material_kit_content)),
        },
    }
    return EvidenceBundlePreview(
        application_id=application_id,
        ready=True,
        issues=[],
        bundle_sha256=sha256_text(canonical_json(snapshot)),
        snapshot=snapshot,
    )
