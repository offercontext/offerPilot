"""User-confirmed revisions and lifecycle for existing Knowledge Notes."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import Integer, String, delete, func, select, text, update
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from offerpilot.ai.interview_knowledge_capture import (
    MAX_BLOCKS,
    MAX_BLOCK_TEXT_CHARS,
    MAX_EVIDENCE_REFS_PER_BLOCK,
    MAX_TITLE_CHARS,
)
from offerpilot.knowledge.interview_capture import MAX_FRAGMENT_UTF8_BYTES
from offerpilot.models import (
    Base,
    InterviewKnowledgeCaptureAttempt,
    KnowledgeEvidence,
    KnowledgeExtractionSnapshot,
    KnowledgeNote,
    KnowledgeNoteEvidence,
    KnowledgeNoteVersion,
    KnowledgeSource,
)

# Include repeated evidence quotations and worst-case JSON escaping. Valid
# capture output can exceed 64 KiB even though each fragment is bounded.
MAX_NOTE_JSON_BYTES = 1024 + 6 * (MAX_TITLE_CHARS + MAX_BLOCKS * (
    MAX_BLOCK_TEXT_CHARS + 256 + MAX_EVIDENCE_REFS_PER_BLOCK * (MAX_FRAGMENT_UTF8_BYTES + 128)
))


class KnowledgeNoteMutation(Base):
    __tablename__ = "knowledge_note_mutations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_version_id: Mapped[int] = mapped_column(Integer, nullable=False)


class NoteBlockEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=MAX_BLOCK_TEXT_CHARS)


class NoteMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mutation_id: UUID
    expected_version_id: StrictInt = Field(gt=0)
    expected_archived: StrictBool = False
    confirmed: StrictBool
    action: Literal["revise", "archive", "unarchive", "delete"]
    title: str = Field(default="", max_length=MAX_TITLE_CHARS)
    blocks: list[NoteBlockEdit] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_action(self) -> NoteMutation:
        if not self.confirmed:
            raise ValueError("explicit_confirmation_required")
        if self.action == "revise":
            if not self.title.strip() or not self.blocks or any(not block.text.strip() for block in self.blocks) or len({block.block_id for block in self.blocks}) != len(self.blocks):
                raise ValueError("note_revision_invalid")
            if sum(len(block.text.encode()) for block in self.blocks) > 32768:
                raise ValueError("note_revision_too_large")
        elif self.blocks or self.title:
            raise ValueError("lifecycle_action_has_content")
        return self


class KnowledgeNoteConflict(ValueError):
    pass


class KnowledgeNoteGone(ValueError):
    pass


def _invalid_current_version(message: str) -> KnowledgeNoteConflict:
    return KnowledgeNoteConflict(message)


def _validate_note_version(
    session: Session,
    version: KnowledgeNoteVersion,
    *,
    source: KnowledgeSource | None = None,
) -> tuple[dict[str, Any], list[KnowledgeNoteEvidence]]:
    """Validate a stored version before it is copied or exposed.

    A Note Version is only useful when its stored content, block/reference
    shape, and Evidence chain still agree with the active Source Snapshot.
    Keep these checks at the model boundary so mutation and public reads use
    the same fail-closed contract.
    """
    if not isinstance(version.content_json, str):
        raise _invalid_current_version("knowledge_note_integrity_failed")
    try:
        content_bytes = version.content_json.encode("utf-8")
        content_hash = sha256(content_bytes).hexdigest()
    except UnicodeError as exc:
        raise _invalid_current_version("knowledge_note_integrity_failed") from exc
    if len(content_bytes) > MAX_NOTE_JSON_BYTES or content_hash != version.content_hash:
        raise _invalid_current_version("knowledge_note_integrity_failed")
    try:
        content = json.loads(version.content_json)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _invalid_current_version("knowledge_note_integrity_failed") from exc
    if (
        not isinstance(content, dict)
        or set(content) != {"title", "blocks"}
        or not isinstance(content.get("title"), str)
        or not isinstance(content.get("blocks"), list)
        or not content["blocks"]
        or len(content["title"]) > MAX_TITLE_CHARS
        or len(content["blocks"]) > MAX_BLOCKS
    ):
        raise _invalid_current_version("knowledge_note_structure_invalid")

    block_ids: set[str] = set()
    for block in content["blocks"]:
        if not isinstance(block, dict) or set(block) != {"block_id", "text", "evidence_refs"}:
            raise _invalid_current_version("knowledge_note_structure_invalid")
        block_id = block.get("block_id")
        text_value = block.get("text")
        refs = block.get("evidence_refs")
        if (
            not isinstance(block_id, str)
            or not block_id
            or block_id in block_ids
            or not isinstance(text_value, str)
            or not text_value
            or len(text_value) > MAX_BLOCK_TEXT_CHARS
            or not isinstance(refs, list)
            or not refs
            or len(refs) > MAX_EVIDENCE_REFS_PER_BLOCK
        ):
            raise _invalid_current_version("knowledge_note_structure_invalid")
        block_ids.add(block_id)
        for ref in refs:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"fragment_id", "excerpt"}
                or not isinstance(ref.get("fragment_id"), str)
                or not ref["fragment_id"]
                or not isinstance(ref.get("excerpt"), str)
                or not ref["excerpt"]
            ):
                raise _invalid_current_version("knowledge_note_structure_invalid")
            try:
                excerpt_size = len(ref["excerpt"].encode("utf-8"))
            except UnicodeError as exc:
                raise _invalid_current_version("knowledge_note_structure_invalid") from exc
            if excerpt_size > MAX_FRAGMENT_UTF8_BYTES:
                raise _invalid_current_version("knowledge_note_structure_invalid")

    if source is None:
        source = session.get(KnowledgeSource, version.source_id)
    if (
        source is None
        or source.id != version.source_id
        or source.deleted_at is not None
        or source.archived_at is not None
        or source.lifecycle != "active"
    ):
        raise KnowledgeNoteGone("knowledge_source_unavailable")
    if source.active_snapshot_id is None:
        raise _invalid_current_version("knowledge_note_snapshot_unavailable")
    active_snapshot_row = session.execute(
        select(
            KnowledgeExtractionSnapshot.id,
            KnowledgeExtractionSnapshot.source_id,
            func.length(KnowledgeExtractionSnapshot.canonical_text),
        ).where(KnowledgeExtractionSnapshot.id == source.active_snapshot_id)
    ).one_or_none()
    if active_snapshot_row is None or active_snapshot_row[1] != source.id:
        raise _invalid_current_version("knowledge_note_snapshot_unavailable")
    active_snapshot_id = active_snapshot_row[0]
    snapshot_char_count = active_snapshot_row[2]
    if type(snapshot_char_count) is not int:
        raise _invalid_current_version("knowledge_note_snapshot_unavailable")

    links = list(
        session.scalars(
            select(KnowledgeNoteEvidence).where(
                KnowledgeNoteEvidence.note_version_id == version.id
            )
        )
    )
    evidence_by_block: dict[str, list[KnowledgeEvidence]] = {}
    for link in links:
        if link.block_id not in block_ids:
            raise _invalid_current_version("knowledge_note_reference_invalid")
        evidence = session.get(KnowledgeEvidence, link.evidence_id)
        if (
            evidence is None
            or evidence.source_id != source.id
            or evidence.snapshot_id != active_snapshot_id
            or not isinstance(evidence.canonical_excerpt, str)
            or not evidence.canonical_excerpt
            or type(evidence.char_start) is not int
            or type(evidence.char_end) is not int
            or evidence.char_start < 0
            or evidence.char_end <= evidence.char_start
            or evidence.char_end > snapshot_char_count
        ):
            raise _invalid_current_version("knowledge_note_evidence_invalid")
        try:
            heading_path = json.loads(evidence.heading_path_json or "[]")
            excerpt_hash = sha256(evidence.canonical_excerpt.encode("utf-8")).hexdigest()
        except (TypeError, ValueError, UnicodeError) as exc:
            raise _invalid_current_version("knowledge_note_evidence_invalid") from exc
        if (
            not isinstance(heading_path, list)
            or any(not isinstance(item, str) for item in heading_path)
            or session.scalar(
                select(
                    func.substr(
                        KnowledgeExtractionSnapshot.canonical_text,
                        evidence.char_start + 1,
                        evidence.char_end - evidence.char_start,
                    )
                ).where(KnowledgeExtractionSnapshot.id == active_snapshot_id)
            )
            != evidence.canonical_excerpt
            or excerpt_hash != evidence.content_hash
        ):
            raise _invalid_current_version("knowledge_note_evidence_invalid")
        evidence_by_block.setdefault(link.block_id, []).append(evidence)

    if set(evidence_by_block) != block_ids:
        raise _invalid_current_version("knowledge_note_evidence_missing")
    for block in content["blocks"]:
        block_id = block["block_id"]
        linked_excerpts = sorted(
            evidence.canonical_excerpt for evidence in evidence_by_block[block_id]
        )
        referenced_excerpts = sorted(ref["excerpt"] for ref in block["evidence_refs"])
        if linked_excerpts != referenced_excerpts:
            raise _invalid_current_version("knowledge_note_reference_invalid")
    return content, links


class KnowledgeNoteLifecycle:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def mutate(self, note_id: int, command: NoteMutation) -> dict[str, object]:
        fingerprint = sha256(json.dumps({"note_id": note_id, **command.model_dump(mode="json")},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(KnowledgeNote, note_id)
            previous = session.get(KnowledgeNoteMutation, str(command.mutation_id))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise KnowledgeNoteConflict("mutation_id_conflict")
                if command.action == "delete":
                    return {"id": note_id, "state": "deleted"}
                if row is None or row.current_version_id is None:
                    raise KnowledgeNoteGone("knowledge_note_gone")
                return self._view(row)
            if row is None or row.current_version_id is None:
                raise KnowledgeNoteGone("knowledge_note_gone")
            if row.current_version_id != command.expected_version_id or bool(row.archived_at) != command.expected_archived:
                raise KnowledgeNoteConflict("knowledge_note_version_changed")
            version = session.get(KnowledgeNoteVersion, row.current_version_id)
            if version is None or version.note_id != note_id:
                raise KnowledgeNoteGone("knowledge_note_version_unavailable")
            now = datetime.now(timezone.utc)
            result_version_id = version.id
            if command.action == "revise":
                source = session.get(KnowledgeSource, version.source_id)
                if source is None or source.deleted_at or source.archived_at or source.lifecycle != "active" or row.archived_at:
                    raise KnowledgeNoteGone("knowledge_source_unavailable")
                content, links = _validate_note_version(session, version, source=source)
                old_blocks = {block["block_id"]: block for block in content["blocks"]}
                if set(old_blocks) != {block.block_id for block in command.blocks}:
                    raise KnowledgeNoteConflict("knowledge_note_blocks_changed")
                content["title"] = command.title.strip()
                content["blocks"] = [{**old_blocks[block.block_id], "text": block.text.strip()} for block in command.blocks]
                raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                new_version = KnowledgeNoteVersion(note_id=row.id, version_number=version.version_number + 1,
                    content_json=raw, content_hash=sha256(raw.encode()).hexdigest(), content_origin="user_confirmed_revision",
                    capture_attempt_key=version.capture_attempt_key, source_id=version.source_id)
                session.add(new_version)
                session.flush()
                for link in links:
                    session.add(KnowledgeNoteEvidence(note_version_id=new_version.id, block_id=link.block_id, evidence_id=link.evidence_id))
                row.current_version_id = new_version.id
                row.title = command.title.strip()
                result_version_id = new_version.id
            elif command.action == "archive":
                row.archived_at = now
            elif command.action == "unarchive":
                source = session.get(KnowledgeSource, version.source_id)
                if source is None or source.deleted_at or source.archived_at or source.lifecycle != "active":
                    raise KnowledgeNoteGone("knowledge_source_unavailable")
                row.archived_at = None
            else:
                row.current_version_id = None
                row.archived_at = now
                row.title = ""
                version_ids = select(KnowledgeNoteVersion.id).where(KnowledgeNoteVersion.note_id == note_id)
                session.execute(update(InterviewKnowledgeCaptureAttempt).where(
                    InterviewKnowledgeCaptureAttempt.confirmed_note_version_id.in_(version_ids)).values(preview_json="{}"))
                session.execute(delete(KnowledgeNoteEvidence).where(KnowledgeNoteEvidence.note_version_id.in_(version_ids)))
                session.execute(update(KnowledgeNoteVersion).where(KnowledgeNoteVersion.note_id == note_id).values(
                    content_json="{}", content_hash=sha256(b"{}").hexdigest()))
            row.updated_at = now
            session.add(KnowledgeNoteMutation(id=str(command.mutation_id), note_id=note_id,
                fingerprint=fingerprint, result_version_id=result_version_id))
            result = self._view(row)
            session.commit()
            return result

    @staticmethod
    def _view(row: KnowledgeNote) -> dict[str, object]:
        return {"id": row.id, "current_version_id": row.current_version_id,
                "state": "deleted" if row.current_version_id is None else "archived" if row.archived_at else "active"}
