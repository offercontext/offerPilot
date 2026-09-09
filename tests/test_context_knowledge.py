from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from offerpilot.db import init_database
from offerpilot.models import KnowledgeEvidence, KnowledgeExtractionSnapshot, KnowledgeNote, KnowledgeNoteEvidence, KnowledgeNoteVersion, KnowledgeSource
from offerpilot.knowledge.note_lifecycle import KnowledgeNoteConflict, KnowledgeNoteGone, KnowledgeNoteLifecycle, NoteMutation
from offerpilot.context_projector.loader import ContextSourceLoader
from offerpilot.context_projector.contracts import ProjectionError
from offerpilot.context_sources.knowledge import recall_knowledge
from offerpilot.context_sources.loader import _contributor
from offerpilot.context_sources.contracts import ContributorPolicy
from offerpilot.repositories.interview_knowledge_capture import InterviewKnowledgeCaptureRepository


@pytest.fixture
def knowledge(tmp_path):
    path = tmp_path / "knowledge.db"
    sessions = init_database(path)
    with sessions() as session:
        source = KnowledgeSource(source_hash="source", display_title="数据库", main_filename="a.md",
            main_relative_path="a.md", total_bytes=20, extraction_status="extracted")
        session.add(source)
        session.flush()
        snapshot = KnowledgeExtractionSnapshot(source_id=source.id, extractor_version="test-v1", canonical_text="索引可以优化查询\n事务保证一致性", digest="snapshot", char_count=20)
        session.add(snapshot)
        session.flush()
        source.active_snapshot_id = snapshot.id
        for index, excerpt in enumerate(["索引可以优化查询", "事务保证一致性"]):
            start = 0 if index == 0 else 9
            session.add(KnowledgeEvidence(id=f"e{index}", source_id=source.id, snapshot_id=snapshot.id,
                kind="text", block_kind="paragraph", ordinal=index, char_start=start, char_end=start+len(excerpt),
                line_start=index+1, line_end=index+1, canonical_excerpt=excerpt, search_text=excerpt,
                content_hash=sha256(excerpt.encode()).hexdigest()))
        note = KnowledgeNote(title="索引与事务", origin_kind="confirmed_interview_capture")
        session.add(note)
        session.flush()
        raw = json.dumps({"title": "索引与事务", "blocks": [{"block_id": "b1", "text": "索引用于查询",
            "evidence_refs": [{"fragment_id": "f1", "excerpt": "索引可以优化查询"}]}]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = KnowledgeNoteVersion(note_id=note.id, version_number=1, content_json=raw,
            content_hash=sha256(raw.encode()).hexdigest(), source_id=source.id, content_origin="user_edited_preview", capture_attempt_key="capture")
        session.add(version)
        session.flush()
        note.current_version_id = version.id
        session.add(KnowledgeNoteEvidence(note_version_id=version.id, block_id="b1", evidence_id="e0"))
        session.commit()
        ids = note.id, version.id, source.id
    loader = ContextSourceLoader(path)
    yield sessions, loader, ids
    loader._pool.close()


def read(loader, query="索引 事务"):
    return loader.load(lambda connection: recall_knowledge(connection, query), lambda value: value)


def mutation(version, action, **fields):
    return NoteMutation(mutation_id=uuid4(), expected_version_id=version, action=action, confirmed=True, **fields)


def test_note_and_unnoted_evidence_recalled_with_valid_references(knowledge):
    _, loader, _ = knowledge
    items = read(loader)
    assert items[0]["kind"] == "confirmed_note"
    assert any(item.get("evidence_id") == "e1" for item in items)
    assert any(item.get("evidence_id") == "e0" for item in items)
    contributor, source = _contributor("knowledge_context", ContributorPolicy(enabled=True, max_units=8192), items)
    assert contributor.status == "ready"
    assert source is not None
    selected = json.loads(source.canonical_content)
    assert not any(item.get("evidence_id") == "e0" for item in selected)
    assert any(item.get("evidence_id") == "e1" for item in selected)


def test_revision_cas_archive_delete_and_replay_tombstone(knowledge):
    sessions, loader, (note_id, version_id, _) = knowledge
    repository = KnowledgeNoteLifecycle(sessions)
    edit = mutation(version_id, "revise", title="索引修订", blocks=[{"block_id": "b1", "text": "新的索引说明"}])
    result = repository.mutate(note_id, edit)
    assert result["current_version_id"] != version_id
    assert repository.mutate(note_id, edit) == result
    with pytest.raises(KnowledgeNoteConflict):
        repository.mutate(note_id, mutation(version_id, "archive"))
    assert "新的索引说明" in str(read(loader))
    repository.mutate(note_id, mutation(result["current_version_id"], "archive"))
    assert all(item["kind"] != "confirmed_note" for item in read(loader))
    reader = InterviewKnowledgeCaptureRepository(sessions)
    assert reader.list_knowledge_notes() == []
    assert reader.list_knowledge_notes(include_archived=True)[0]["archived"]
    deletion = mutation(result["current_version_id"], "delete", expected_archived=True)
    assert repository.mutate(note_id, deletion)["state"] == "deleted"
    assert repository.mutate(note_id, deletion)["state"] == "deleted"
    with pytest.raises(KnowledgeNoteGone):
        repository.mutate(note_id, edit)
    assert reader.list_knowledge_notes(include_archived=True) == []
    with sessions() as session:
        assert all(raw == "{}" for raw in session.scalars(select(KnowledgeNoteVersion.content_json)))
    assert any(item.get("evidence_id") == "e0" for item in read(loader))


def test_revision_rejects_tampered_current_version_without_creating_version(knowledge):
    sessions, _, (note_id, version_id, _) = knowledge
    repository = KnowledgeNoteLifecycle(sessions)
    with sessions() as session:
        version = session.get(KnowledgeNoteVersion, version_id)
        assert version is not None
        version.content_json = "not-json"
        session.commit()

    command = mutation(
        version_id,
        "revise",
        title="索引修订",
        blocks=[{"block_id": "b1", "text": "新的索引说明"}],
    )
    with pytest.raises(KnowledgeNoteConflict, match="integrity"):
        repository.mutate(note_id, command)
    with sessions() as session:
        assert session.get(KnowledgeNote, note_id).current_version_id == version_id
        assert len(list(session.scalars(select(KnowledgeNoteVersion)))) == 1


def test_revision_rejects_evidence_outside_active_snapshot(knowledge):
    sessions, _, (note_id, version_id, source_id) = knowledge
    repository = KnowledgeNoteLifecycle(sessions)
    with sessions() as session:
        source = session.get(KnowledgeSource, source_id)
        assert source is not None
        source.active_snapshot_id = None
        session.commit()

    command = mutation(
        version_id,
        "revise",
        title="索引修订",
        blocks=[{"block_id": "b1", "text": "新的索引说明"}],
    )
    with pytest.raises(KnowledgeNoteConflict, match="evidence|snapshot"):
        repository.mutate(note_id, command)
    with sessions() as session:
        assert session.get(KnowledgeNote, note_id).current_version_id == version_id


def test_valid_repeated_quotations_above_64k_remain_readable_and_editable(knowledge):
    from sqlalchemy import delete
    sessions, _, (note_id, version_id, source_id) = knowledge
    excerpt = "e" * 4000
    blocks = [{"block_id": f"b{i}", "text": "summary", "evidence_refs": [
        {"fragment_id": "f1", "excerpt": excerpt}]} for i in range(20)]
    with sessions() as session:
        source = session.get(KnowledgeSource, source_id)
        snapshot = session.get(KnowledgeExtractionSnapshot, source.active_snapshot_id)
        snapshot.canonical_text = excerpt
        snapshot.char_count = len(excerpt)
        snapshot.digest = sha256(excerpt.encode()).hexdigest()
        evidence = session.get(KnowledgeEvidence, "e0")
        evidence.canonical_excerpt = excerpt
        evidence.char_start, evidence.char_end = 0, len(excerpt)
        evidence.content_hash = snapshot.digest
        raw = json.dumps({"title": "valid quotations", "blocks": blocks})
        assert len(raw.encode()) > 65536
        version = session.get(KnowledgeNoteVersion, version_id)
        version.content_json, version.content_hash = raw, sha256(raw.encode()).hexdigest()
        session.execute(delete(KnowledgeNoteEvidence).where(KnowledgeNoteEvidence.note_version_id == version_id))
        for block in blocks:
            session.add(KnowledgeNoteEvidence(note_version_id=version_id, block_id=block["block_id"], evidence_id="e0"))
        session.commit()
    assert InterviewKnowledgeCaptureRepository(sessions).get_knowledge_note(note_id) is not None
    result = KnowledgeNoteLifecycle(sessions).mutate(note_id, mutation(
        version_id, "revise", title="edited", blocks=[
            {"block_id": block["block_id"], "text": "edited"} for block in blocks]))
    assert result["current_version_id"] != version_id


def test_public_knowledge_read_skips_corrupt_current_version(knowledge):
    sessions, _, (note_id, version_id, _) = knowledge
    reader = InterviewKnowledgeCaptureRepository(sessions)
    with sessions() as session:
        version = session.get(KnowledgeNoteVersion, version_id)
        assert version is not None
        version.content_json = "not-json"
        session.commit()

    assert reader.list_knowledge_notes(include_archived=True) == []
    assert reader.get_knowledge_note(note_id) is None


def test_source_archive_invalidates_both_lanes_and_public_read(knowledge):
    from datetime import datetime, timezone
    sessions, loader, (note_id, _, source_id) = knowledge
    with sessions() as session:
        session.get(KnowledgeSource, source_id).archived_at = datetime.now(timezone.utc)
        session.commit()
    assert read(loader) == []
    assert InterviewKnowledgeCaptureRepository(sessions).get_knowledge_note(note_id) is None


def test_evidence_tamper_fails_closed(knowledge):
    sessions, loader, _ = knowledge
    with sessions() as session:
        session.get(KnowledgeEvidence, "e0").canonical_excerpt = "伪造内容"
        session.commit()
    with pytest.raises(ProjectionError):
        read(loader)


def test_active_snapshot_change_invalidates_notes_and_evidence(knowledge):
    sessions, loader, (_, _, source_id) = knowledge
    with sessions() as session:
        session.get(KnowledgeSource, source_id).active_snapshot_id = None
        session.commit()
    assert read(loader) == []


def test_captured_interview_note_is_read_and_edit_invalidates_both_lanes(knowledge):
    from offerpilot.models import InterviewNote, KnowledgeCapturedSourceMetadata
    from offerpilot.knowledge.interview_capture import note_fingerprint
    sessions, loader, (_, _, source_id) = knowledge
    with sessions() as session:
        note = InterviewNote(company="公司", position="职位", questions="索引与事务")
        session.add(note)
        session.flush()
        note_id = note.id
        session.add(KnowledgeCapturedSourceMetadata(source_id=source_id, origin_note_id=note.id,
            note_fingerprint=note_fingerprint(note), selected_fragments_json="[]", capture_schema_version="v1"))
        session.commit()
    assert len(read(loader)) >= 2
    with sessions() as session:
        session.get(InterviewNote, note_id).questions = "来源已编辑"
        session.commit()
    assert read(loader) == []
