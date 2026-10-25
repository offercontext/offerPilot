"""Local immutable image storage; admission and collection share SQLite fencing."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import warnings
import logging
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.image_contracts import (
    IMAGE_ID_PATTERN, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_IMAGE_SIDE,
    ImageInputError, ImageReference, image_ids, image_references,
)
from offerpilot.models import ChatAttachment, ChatMessageAttachment, Conversation
from offerpilot.repositories.chat import ConversationScopeMutationSnapshot, _require_active_application


def normalize_image(content: bytes) -> tuple[bytes, str, int, int]:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ImageInputError("image_size_exceeded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ImageInputError("image_format_unsupported")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageInputError("image_animation_unsupported")
                if image.width * image.height > MAX_IMAGE_PIXELS or max(image.size) > MAX_IMAGE_SIDE:
                    raise ImageInputError("image_dimensions_exceeded")
                is_jpeg = image.format == "JPEG"
                image.load()
                oriented = ImageOps.exif_transpose(image)
                # Copy pixels into a fresh image to drop EXIF, comments, ICC and
                # any metadata inherited by Pillow's conversion helpers.
                mode = "RGB" if is_jpeg else "RGBA"
                converted = oriented.convert(mode)
                clean = Image.new(mode, converted.size)
                clean.paste(converted)
                output = io.BytesIO()
                clean.save(output, format="JPEG" if is_jpeg else "PNG", **({"quality": 95} if is_jpeg else {}))
                normalized = output.getvalue()
                if len(normalized) > MAX_IMAGE_BYTES:
                    raise ImageInputError("image_normalized_size_exceeded")
                return normalized, "image/jpeg" if is_jpeg else "image/png", clean.width, clean.height
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageInputError("image_decode_failed") from exc


def reference(row: ChatAttachment) -> ImageReference:
    return ImageReference(row.id, row.sha256, row.mime_type, row.size_bytes,
                          row.width, row.height, row.normalization_version)


class ChatImageStore:
    def __init__(self, sessions: sessionmaker[Session], data_dir: Path):
        self.sessions = sessions
        self.root = data_dir.resolve() / "chat-images"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.resolve().parent != data_dir.resolve():
            raise ImageInputError("image_storage_invalid")

    def path(self, attachment_id: str) -> Path:
        if IMAGE_ID_PATTERN.fullmatch(attachment_id) is None:
            raise ImageInputError("invalid_image_reference")
        path = self.root / (attachment_id + ".image")
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ImageInputError("image_storage_invalid")
        return path

    @staticmethod
    def require_scope(session: Session, context_type: str, context_ref: str,
                      conversation_id: int | None = None, *, allow_archived: bool = False) -> tuple[str, str]:
        if conversation_id:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or (conversation.archived_at is not None and not allow_archived):
                raise ImageInputError("image_conversation_unavailable")
            context_type, context_ref = conversation.context_type, conversation.context_ref
        scope = ConversationScopeMutationSnapshot(context_type=context_type, context_ref=context_ref)
        _require_active_application(session, scope)
        return str(scope.context_type or "workspace"), str(scope.context_ref or "")

    def upload(self, content: bytes, *, context_type: str, context_ref: str,
               conversation_id: int | None = None) -> ImageReference:
        normalized, mime, width, height = normalize_image(content)
        ref = ImageReference("img_" + uuid4().hex, hashlib.sha256(normalized).hexdigest(),
                             mime, len(normalized), width, height)
        path = self.path(ref.id)
        # No row becomes visible before the complete file is durable. A crash
        # here can only leave an orphan file, never an accepted dangling image.
        with path.open("xb") as file:
            file.write(normalized)
            file.flush()
            os.fsync(file.fileno())
        try:
            with self.sessions() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                kind, scope_ref = self.require_scope(session, context_type, context_ref, conversation_id)
                session.add(ChatAttachment(**ref.as_dict(), context_type=kind, context_ref=scope_ref,
                                           conversation_id=conversation_id, bound=bool(conversation_id),
                                           created_at=int(time.time())))
                session.commit()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return ref

    def resolve_in_session(self, session: Session, ids: tuple[str, ...],
                           conversation: Conversation) -> tuple[ImageReference, ...]:
        validated_ids = image_ids(ids)
        # Text-only admission keeps the main runtime's scope validation and
        # recovery semantics; image-specific checks must not intercept it.
        if not validated_ids:
            return ()
        refs = []
        kind, scope_ref = self.require_scope(session, conversation.context_type,
                                             conversation.context_ref, conversation.id)
        for attachment_id in validated_ids:
            row = session.get(ChatAttachment, attachment_id)
            if row is None or row.state != "ready":
                raise ImageInputError("image_unavailable")
            if row.bound and row.conversation_id is None:
                raise ImageInputError("image_unavailable")
            # Once bound, ownership follows the conversation, including an
            # explicit scope change. Creation scope constrains unbound drafts.
            if (row.conversation_id is not None and row.conversation_id != conversation.id) or (
                row.conversation_id is None and (row.context_type, row.context_ref) != (kind, scope_ref)
            ):
                raise ImageInputError("image_scope_mismatch")
            ref = reference(row)
            self.read(ref)
            refs.append(ref)
        return image_references(refs)

    def bind_in_session(self, session: Session, refs: tuple[ImageReference, ...],
                        conversation_id: int, message_id: int) -> None:
        for ordinal, ref in enumerate(refs):
            row = session.get(ChatAttachment, ref.id)
            if row is None or row.state != "ready" or reference(row) != ref:
                raise ImageInputError("image_unavailable")
            if row.conversation_id not in (None, conversation_id):
                raise ImageInputError("image_scope_mismatch")
            row.conversation_id = conversation_id
            row.bound = True
            session.add(ChatMessageAttachment(message_id=message_id, attachment_id=ref.id,
                                              ordinal=ordinal))
        from offerpilot.models import ChatMessage
        message = session.get(ChatMessage, message_id)
        if message is None:
            raise ImageInputError("image_message_unavailable")
        message.image_refs_json = json.dumps([ref.as_dict() for ref in refs], separators=(",", ":"))

    def read(self, ref: ImageReference) -> bytes:
        try:
            with self.path(ref.id).open("rb") as file:
                content = file.read(MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise ImageInputError("image_unavailable") from exc
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ImageInputError("image_content_changed")
        return content

    def read_authorized(self, attachment_id: str) -> tuple[ImageReference, bytes]:
        with self.sessions() as session:
            row = session.get(ChatAttachment, attachment_id)
            if row is None or row.state != "ready":
                raise ImageInputError("image_unavailable")
            self.require_scope(session, row.context_type, row.context_ref, row.conversation_id, allow_archived=True)
            # A bound image with a deleted conversation is no longer visible.
            if row.bound and row.conversation_id is None:
                raise ImageInputError("image_unavailable")
            ref = reference(row)
            return ref, self.read(ref)

    def read_for_model(self, ref: ImageReference) -> bytes:
        stored, content = self.read_authorized(ref.id)
        if stored != ref:
            raise ImageInputError("image_content_changed")
        return content

    def collect(self, *, now: int | None = None) -> int:
        """Collect only expired drafts / detached files. Same writer lock as admission."""
        cutoff = (int(time.time()) if now is None else now) - 86400
        removed = 0
        with self.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            rows = list(session.scalars(select(ChatAttachment).where(
                or_(ChatAttachment.created_at < cutoff,
                    and_(ChatAttachment.bound.is_(True), ChatAttachment.conversation_id.is_(None))),
                ~exists().where(ChatMessageAttachment.attachment_id == ChatAttachment.id),
            )))
            for row in rows:
                self.path(row.id).unlink(missing_ok=True)
                session.delete(row)
                removed += 1
            # Crash between file fsync and row insertion leaves only a private
            # orphan. Collect it after the same grace period, never follow links.
            for path in self.root.glob("img_*.image"):
                attachment_id = path.stem
                if (IMAGE_ID_PATTERN.fullmatch(attachment_id) is not None
                        and not path.is_symlink() and path.stat().st_mtime < cutoff
                        and session.get(ChatAttachment, attachment_id) is None):
                    self.path(attachment_id).unlink(missing_ok=True)
                    removed += 1
            session.commit()
        return removed


class ChatImageCollector:
    """Sweep expired drafts even when no new uploads arrive; stop before DB close."""

    def __init__(self, store: ChatImageStore, *, interval_seconds: float = 3600):
        self.store = store
        self.interval_seconds = interval_seconds
        self._stopped = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = Thread(target=self._run, name="chat-image-collector", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                self.store.collect()
            except (OSError, SQLAlchemyError, ImageInputError):
                logging.getLogger(__name__).warning("Image collection deferred")
            if self._stopped.wait(self.interval_seconds):
                break

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
