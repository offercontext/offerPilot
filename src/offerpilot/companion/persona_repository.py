"""Transactional persona configuration. No life, memory, authority or budget writes."""
from __future__ import annotations

from hashlib import sha256
import json
import time
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from .persona_contracts import (
    PERSONA_POLICY_VERSION, PersonaMutation, PersonaRestoreCommand, PersonaSpec, persona_presets,
)
from .persona_models import PersonaCommandReceipt, PersonaProfile, PersonaRevision


class PersonaConflict(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def persona_content_hash(spec: PersonaSpec) -> str:
    return sha256(_json(spec.model_dump()).encode("utf-8")).hexdigest()


def _profile(session: Session) -> PersonaProfile:
    row = session.get(PersonaProfile, 1)
    if row is None:
        # The no-op conflict insert serializes concurrent initializers inside
        # their caller's transaction; admission must not commit independently.
        session.execute(insert(PersonaProfile).values(
            id=1, profile_id=str(uuid4()), revision=0,
        ).on_conflict_do_nothing(index_elements=["id"]))
        row = session.get(PersonaProfile, 1)
        assert row is not None
        spec = PersonaSpec()
        session.execute(insert(PersonaRevision).values(
            profile_id=row.profile_id, revision=0, content_hash=persona_content_hash(spec),
            spec_json=_json(spec.model_dump()), created_at=time.time(), operation="initial",
            restored_from_revision=None,
        ).on_conflict_do_nothing(index_elements=["profile_id", "revision"]))
    return row


def read_active_persona_spec(session: Session) -> PersonaSpec:
    """Pure read for future activity selection; never initializes a profile."""
    profile = session.get(PersonaProfile, 1)
    if profile is None:
        return PersonaSpec()
    row = session.get(PersonaRevision, (profile.profile_id, profile.revision))
    if row is None:
        raise PersonaConflict("persona_revision_missing")
    return load_persona_spec(session, {"profile_id": row.profile_id,
        "revision": row.revision, "content_hash": row.content_hash})


def capture_persona_binding(session: Session) -> dict[str, Any]:
    profile = _profile(session)
    row = session.get(PersonaRevision, (profile.profile_id, profile.revision))
    if row is None:
        raise PersonaConflict("persona_revision_missing")
    binding = {"profile_id": row.profile_id, "revision": row.revision,
               "content_hash": row.content_hash}
    load_persona_spec(session, binding)
    return binding


def load_persona_spec(session: Session, binding: Mapping[str, Any]) -> PersonaSpec:
    """Resolve exactly the admitted revision, never the current profile pointer."""
    if set(binding) != {"profile_id", "revision", "content_hash"}:
        raise PersonaConflict("persona_binding_invalid")
    if (not isinstance(binding["profile_id"], str)
            or type(binding["revision"]) is not int
            or not isinstance(binding["content_hash"], str)):
        raise PersonaConflict("persona_binding_invalid")
    row = session.get(PersonaRevision, (binding["profile_id"], binding["revision"]))
    if row is None:
        raise PersonaConflict("persona_revision_missing")
    spec = PersonaSpec.model_validate_json(row.spec_json)
    if row.content_hash != binding["content_hash"] or persona_content_hash(spec) != row.content_hash:
        raise PersonaConflict("persona_revision_hash_mismatch")
    return spec


def _revision_view(row: PersonaRevision) -> dict[str, Any]:
    return {"profile_id": row.profile_id, "revision": row.revision,
            "content_hash": row.content_hash, "spec": json.loads(row.spec_json),
            "created_at": row.created_at, "operation": row.operation,
            "restored_from_revision": row.restored_from_revision}


def _view(session: Session, binding: Mapping[str, Any]) -> dict[str, Any]:
    spec = load_persona_spec(session, binding)
    return {**binding, "spec": spec.model_dump(), "policy_version": PERSONA_POLICY_VERSION,
            "presets": persona_presets()}


class PersonaRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def read(self) -> dict[str, Any]:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _view(session, capture_persona_binding(session))
            session.commit()
            return result

    def revisions(self) -> dict[str, Any]:
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            profile = _profile(session)
            rows = session.scalars(select(PersonaRevision).where(
                PersonaRevision.profile_id == profile.profile_id,
            ).order_by(PersonaRevision.revision.desc())).all()
            result = {"revisions": [_revision_view(row) for row in rows]}
            session.commit()
            return result

    def mutate(self, command: PersonaMutation) -> dict[str, Any]:
        # Revalidate even direct Python callers; model_copy/construct must not
        # turn this repository into an unconfirmed write path.
        command = type(command).model_validate(command.model_dump(mode="json"))
        digest = sha256(_json(command.model_dump(mode="json")).encode("utf-8")).hexdigest()
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            receipt = session.get(PersonaCommandReceipt, str(command.command_id))
            if receipt is not None:
                if receipt.request_digest != digest:
                    raise PersonaConflict("persona_command_id_conflict")
                return dict(json.loads(receipt.response_json))
            profile = _profile(session)
            if profile.profile_id != str(command.profile_id):
                raise PersonaConflict("persona_profile_conflict")
            if profile.revision != command.expected_revision:
                raise PersonaConflict("persona_revision_conflict")
            restored_from = None
            if isinstance(command, PersonaRestoreCommand):
                old = session.get(PersonaRevision, (profile.profile_id, command.target_revision))
                if old is None:
                    raise PersonaConflict("persona_restore_revision_missing")
                spec = load_persona_spec(session, {"profile_id": old.profile_id,
                    "revision": old.revision, "content_hash": old.content_hash})
                restored_from = command.target_revision
            else:
                spec = command.spec
            revision = profile.revision + 1
            binding = {"profile_id": profile.profile_id, "revision": revision,
                       "content_hash": persona_content_hash(spec)}
            session.add(PersonaRevision(**binding, spec_json=_json(spec.model_dump()),
                created_at=time.time(), operation=command.operation,
                restored_from_revision=restored_from))
            profile.revision = revision
            session.flush()
            result = _view(session, binding)
            session.add(PersonaCommandReceipt(command_id=str(command.command_id),
                profile_id=profile.profile_id, request_digest=digest, response_json=_json(result)))
            session.commit()
            return result
