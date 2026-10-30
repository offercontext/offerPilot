from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from offerpilot.db import init_database
from offerpilot.external_agents.catalog import CAPABILITIES
from offerpilot.external_agents.service import (
    ExternalAgentError,
    ExternalAgentService,
    create_connection,
)
from offerpilot.models import (
    KnowledgeCapturedSourceMetadata,
    KnowledgeSource,
    KnowledgeExtractionSnapshot,
    KnowledgeEvidence,
    KnowledgeNote,
    KnowledgeNoteVersion,
    KnowledgeNoteEvidence,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.application_events import (
    ApplicationEventCreate,
    ApplicationEventsRepository,
)
from offerpilot.repositories.notes import NoteCreate, NoteUpdate, NotesRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


@pytest.fixture
def business(tmp_path):
    sessions = init_database(tmp_path / "data.db")
    apps = ApplicationsRepository(sessions)
    first = apps.create(ApplicationCreate("云岚", "开发"))
    other = apps.create(ApplicationCreate("北辰", "测试"))
    grant = create_connection(
        sessions,
        "business",
        [],
        workspace=True,
        allow_scheduling=True,
        capabilities=list(CAPABILITIES),
    )
    service = ExternalAgentService(sessions, grant["connection_id"])
    yield sessions, service, first, other
    sessions.kw["bind"].dispose()


def propose(service, tool, **args):
    return service.call(tool, {"idempotency_key": str(uuid4()), **args})


def approve(service, request):
    view = service.review(request["request_id"])
    return service.decide(view["request_id"], view["proposal_digest"], "approve")


def test_application_maintenance_closes_and_soft_deletes_only_after_approval(business):
    sessions, service, _, _ = business
    pending = propose(
        service, "propose_application_create", company_name="新公司", position_name="工程师"
    )
    assert len(ApplicationsRepository(sessions).list()) == 2
    committed = approve(service, pending)
    created = committed["application"]
    assert committed["status"] == "committed" and created["company_name"] == "新公司"
    assert approve(service, pending) == committed
    pending = propose(
        service,
        "propose_application_update",
        application_id=created["id"],
        expected_revision=created["revision"],
        changes={"status": "closed", "closed_reason": "主动结束"},
    )
    assert ApplicationsRepository(sessions).get(created["id"]).status == "applied"
    closed = approve(service, pending)["application"]
    assert closed["company_name"] == "新公司" and closed["status"] == "closed"
    with pytest.raises(ExternalAgentError, match="closed_application_terminal"):
        propose(
            service,
            "propose_application_update",
            application_id=created["id"],
            expected_revision=closed["revision"],
            changes={"status": "applied"},
        )
    deleted = approve(
        service,
        propose(
            service,
            "propose_application_delete",
            application_id=closed["id"],
            expected_revision=closed["revision"],
        ),
    )
    assert deleted["status"] == "committed" and deleted["deleted"]
    assert ApplicationsRepository(sessions).get(closed["id"]) is None
    replay = service.call("get_request", {"request_id": committed["request_id"]})
    assert replay["status"] == "committed" and not replay["source_available"]
    assert "application" not in replay


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_resumes", {}),
        ("get_resume", {"resume_id": 1}),
        ("list_notes", {}),
        ("get_note", {"note_id": 1}),
        ("search_knowledge", {"query": "索引"}),
        (
            "propose_application_create",
            {"company_name": "公司", "position_name": "岗位", "idempotency_key": str(uuid4())},
        ),
    ],
)
def test_old_workspace_grants_do_not_gain_new_resources(business, tool, args):
    sessions, _, _, _ = business
    old = create_connection(sessions, "old", [], workspace=True, allow_scheduling=True)
    with pytest.raises(ExternalAgentError, match="capability_not_granted"):
        ExternalAgentService(sessions, old["connection_id"]).call(tool, args)


def test_create_scope_and_read_before_write_grants_are_explicit(business):
    sessions, _, first, _ = business
    with pytest.raises(ExternalAgentError, match="workspace_create_required"):
        create_connection(sessions, "wrong", [first.id], capabilities=["applications.create"])
    for capabilities in (["resumes.write"], ["notes.write"], ["notes.unbound"]):
        with pytest.raises(ExternalAgentError, match="read_grant_required"):
            create_connection(sessions, "wrong", [], workspace=True, capabilities=capabilities)
    scoped = create_connection(sessions, "scoped", [first.id], capabilities=["applications.write"])
    with pytest.raises(ExternalAgentError, match="capability_not_granted"):
        propose(
            ExternalAgentService(sessions, scoped["connection_id"]),
            "propose_application_create",
            company_name="公司",
            position_name="岗位",
        )


@pytest.mark.parametrize("terminal", ["reject", "cancel", "expire", "revoke"])
def test_all_new_writes_share_nonapproval_terminal_semantics(business, terminal):
    sessions, service, _, _ = business
    pending = propose(service, "propose_resume_create", title="待确认", text="资料")
    view = service.review(pending["request_id"])
    if terminal == "reject":
        result = service.decide(view["request_id"], view["proposal_digest"], "reject")
    elif terminal == "cancel":
        result = service.call("cancel_request", {"request_id": pending["request_id"]})
    elif terminal == "expire":
        service.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
        result = service.call("get_request", {"request_id": pending["request_id"]})
    else:
        service.revoke()
        with pytest.raises(ExternalAgentError, match="connection_unavailable"):
            service.decide(view["request_id"], view["proposal_digest"], "approve")
        result = {"status": "cancelled"}
    if terminal != "revoke":
        assert approve(service, pending)["status"] == result["status"]
    assert ResumesRepository(sessions).list() == []


def test_resume_copy_edit_and_master_transitions_are_atomic(business):
    sessions, service, _, _ = business
    first = approve(
        service, propose(service, "propose_resume_create", title="主简历", text="原始事实")
    )["resume"]
    assert first["is_master"] and first["text"] == "原始事实"
    with pytest.raises(ExternalAgentError, match="master_resume_required"):
        propose(
            service,
            "propose_resume_update",
            resume_id=first["id"],
            expected_revision=first["revision"],
            changes={"is_master": False},
        )
    with pytest.raises(ExternalAgentError, match="master_resume_cannot_delete"):
        propose(
            service,
            "propose_resume_delete",
            resume_id=first["id"],
            expected_revision=first["revision"],
        )
    copied = approve(
        service,
        propose(
            service,
            "propose_resume_copy",
            resume_id=first["id"],
            expected_revision=first["revision"],
            title="岗位副本",
        ),
    )["resume"]
    assert copied["parent_resume_id"] == first["id"] and not copied["is_master"]
    changed = approve(
        service,
        propose(
            service,
            "propose_resume_update",
            resume_id=copied["id"],
            expected_revision=copied["revision"],
            changes={"text": "确认的新事实", "is_master": True},
        ),
    )["resume"]
    assert (
        changed["text"] == "确认的新事实" and changed["content_json"]["raw_text"] == "确认的新事实"
    )
    assert len([item for item in ResumesRepository(sessions).list() if item.is_master]) == 1
    old = service.call("get_resume", {"resume_id": first["id"]})
    deleted = approve(
        service,
        propose(
            service,
            "propose_resume_delete",
            resume_id=first["id"],
            expected_revision=old["revision"],
        ),
    )
    assert deleted["deleted"] and ResumesRepository(sessions).get(first["id"]) is None


def test_resume_collection_change_prevents_unreviewed_master_effects(business):
    sessions, service, _, _ = business
    one = propose(service, "propose_resume_create", title="第一份")
    two = propose(service, "propose_resume_create", title="同时提交")
    assert approve(service, one)["status"] == "committed"
    assert approve(service, two)["status"] == "conflict"
    assert len(ResumesRepository(sessions).list()) == 1


def test_resume_title_patch_does_not_change_text_parse_status_or_content(business):
    sessions, service, _, _ = business
    repo = ResumesRepository(sessions)
    resource = repo.create(
        ResumeCreate(
            title="旧名",
            parsed_data="已存正文",
            parse_status="pending",
            content_json={"contact": {"name": "人名"}},
        )
    )
    before = service.call("get_resume", {"resume_id": resource.id})
    after = approve(
        service,
        propose(
            service,
            "propose_resume_update",
            resume_id=resource.id,
            expected_revision=before["revision"],
            changes={"title": "新名"},
        ),
    )["resume"]
    assert after["title"] == "新名" and after["parse_status"] == "pending"
    assert after["text"] == before["text"] and after["content_json"] == before["content_json"]


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"content_json": {"raw_text": "结构正文"}}, "结构正文"),
        ({"text": "显式正文", "content_json": {"raw_text": "旧正文"}}, "显式正文"),
        ({"text": "", "content_json": {"raw_text": "保留正文"}}, "保留正文"),
    ],
)
def test_resume_create_text_precedence_matches_http(business, fields, expected):
    _, service, _, _ = business
    created = approve(service, propose(service, "propose_resume_create", **fields))["resume"]
    assert created["text"] == expected
    assert created["content_json"]["raw_text"] == expected


def test_resume_structured_patch_preserves_existing_body_and_parse_status(business):
    sessions, service, _, _ = business
    repo = ResumesRepository(sessions)
    resource = repo.create(
        ResumeCreate(title="原稿", parsed_data="原始正文", parse_status="pending")
    )
    before = service.call("get_resume", {"resume_id": resource.id})
    assert before["content_json"] == {"raw_text": "原始正文"}
    after = approve(
        service,
        propose(
            service,
            "propose_resume_update",
            resume_id=resource.id,
            expected_revision=before["revision"],
            changes={"content_json": {"contact": {"name": "新名"}}},
        ),
    )["resume"]
    assert after["text"] == "原始正文" and after["parse_status"] == "pending"
    assert after["content_json"] == {"contact": {"name": "新名"}}


@pytest.mark.parametrize("child", ["event", "note"])
def test_application_delete_rechecks_reviewed_dependencies(business, child):
    sessions, service, first, _ = business
    before = service.call("get_application", {"application_id": first.id})
    pending = propose(
        service,
        "propose_application_delete",
        application_id=first.id,
        expected_revision=before["revision"],
    )
    if child == "event":
        ApplicationEventsRepository(sessions).create(
            ApplicationEventCreate(first.id, "interview", datetime(2026, 11, 1), 60)
        )
    else:
        NotesRepository(sessions).create(NoteCreate(company="云岚", application_id=first.id))
    assert approve(service, pending)["status"] == "conflict"
    assert ApplicationsRepository(sessions).get(first.id) is not None


def test_empty_master_deletion_previews_and_receipts_replacement(business):
    sessions, service, _, _ = business
    repo = ResumesRepository(sessions)
    empty = repo.create(ResumeCreate(title="空主稿", is_master=True))
    replacement = repo.create(ResumeCreate(title="接任稿", is_master=False))
    before = service.call("get_resume", {"resume_id": empty.id})
    pending = propose(
        service, "propose_resume_delete", resume_id=empty.id, expected_revision=before["revision"]
    )
    view = service.review(pending["request_id"])
    expected = {"id": replacement.id, "title": "接任稿", "is_master": True}
    assert view["proposal"]["after"]["replacement_master"] == expected
    committed = approve(service, pending)
    assert committed["replacement_master"] == expected
    assert repo.get(replacement.id).is_master
    # A retained deletion receipt does not reveal a successor hidden later.
    repo.delete(replacement.id)
    replay = service.call("get_request", {"request_id": pending["request_id"]})
    assert replay["status"] == "committed" and "replacement_master" not in replay


def test_bound_resume_copy_rolls_back_with_its_request_on_failure(business, monkeypatch):
    sessions, service, _, _ = business
    resource = approve(
        service, propose(service, "propose_resume_create", title="主稿", text="事实")
    )["resume"]
    pending = propose(
        service,
        "propose_resume_copy",
        resume_id=resource["id"],
        expected_revision=resource["revision"],
        title="副本",
    )
    original = ResumesRepository.copy

    def fail_after_copy(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OperationalError("copy", {}, RuntimeError("after flush"))

    with monkeypatch.context() as patch:
        patch.setattr(ResumesRepository, "copy", fail_after_copy)
        with pytest.raises(ExternalAgentError, match="service_unavailable"):
            approve(service, pending)
    assert len(ResumesRepository(sessions).list()) == 1
    assert service.call("get_request", {"request_id": pending["request_id"]})["status"] == "pending"
    assert approve(service, pending)["status"] == "committed"
    assert len(ResumesRepository(sessions).list()) == 2


def test_notes_binding_scope_revision_and_no_automatic_knowledge_capture(business):
    sessions, service, first, second = business
    event = ApplicationEventsRepository(sessions).create(
        ApplicationEventCreate(first.id, "interview", datetime(2026, 11, 1), 60)
    )
    pending = propose(
        service,
        "propose_note_create",
        application_id=first.id,
        application_event_id=event.id,
        questions="原始问题",
    )
    assert NotesRepository(sessions).list() == []
    note = approve(service, pending)["note"]
    assert note["company"] == first.company_name and note["application_id"] == first.id
    assert note["application_event_id"] == event.id
    with pytest.raises(ExternalAgentError, match="note_binding_conflict"):
        propose(
            service,
            "propose_note_create",
            application_id=first.id,
            application_event_id=event.id,
            questions="重复",
        )
    with pytest.raises(ExternalAgentError, match="note_binding_conflict"):
        propose(
            service, "propose_note_create", application_id=second.id, application_event_id=event.id
        )
    pending = propose(
        service,
        "propose_note_update",
        note_id=note["id"],
        expected_revision=note["revision"],
        changes={"self_reflection": "自我复盘"},
    )
    NotesRepository(sessions).update(
        note["id"], NoteUpdate(company=first.company_name, questions="并发修改")
    )
    assert approve(service, pending)["status"] == "conflict"
    current = service.call("get_note", {"note_id": note["id"]})
    unbound = approve(
        service,
        propose(
            service,
            "propose_note_update",
            note_id=note["id"],
            expected_revision=current["revision"],
            changes={"application_event_id": None},
        ),
    )["note"]
    assert unbound["application_event_id"] is None and unbound["questions"] == "并发修改"
    with sessions() as session:
        assert session.scalar(select(KnowledgeSource)) is None


def test_application_scoped_notes_cannot_read_or_create_unbound_or_foreign_notes(business):
    sessions, _, first, second = business
    notes = NotesRepository(sessions)
    allowed = notes.create(NoteCreate(company="本投递", application_id=first.id))
    foreign = notes.create(NoteCreate(company="其他投递", application_id=second.id))
    unbound = notes.create(NoteCreate(company="独立笔记"))
    grant = create_connection(
        sessions, "scope", [first.id], capabilities=["notes.read", "notes.write"]
    )
    scoped = ExternalAgentService(sessions, grant["connection_id"])
    assert [item["id"] for item in scoped.call("list_notes", {})["items"]] == [allowed.id]
    for note_id in (foreign.id, unbound.id, 99999):
        with pytest.raises(ExternalAgentError, match="note_not_found"):
            scoped.call("get_note", {"note_id": note_id})
    with pytest.raises(ExternalAgentError, match="unbound_notes_not_granted"):
        propose(scoped, "propose_note_create", company="不应独立保存")


def test_concurrent_business_approvals_create_one_record_and_recover_receipt(business):
    sessions, service, _, _ = business
    payload = {"idempotency_key": str(uuid4()), "company_name": "新公司", "position_name": "岗位"}
    pending = service.call("propose_application_create", payload)
    assert service.call("propose_application_create", payload) == pending
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(service, pending), range(2)))
    assert results[0] == results[1] and results[0]["status"] == "committed"
    resumed = ExternalAgentService(sessions, service.connection_id)
    assert resumed.call("propose_application_create", payload) == results[0]
    assert len(ApplicationsRepository(sessions).list()) == 3
    with pytest.raises(ExternalAgentError, match="idempotency_conflict"):
        service.call("propose_application_create", {**payload, "company_name": "改过"})


def seed_knowledge(sessions):
    with sessions() as session:
        source = KnowledgeSource(
            source_hash="source",
            display_title="数据库",
            main_filename="a.md",
            main_relative_path="a.md",
            total_bytes=20,
            extraction_status="extracted",
        )
        session.add(source)
        session.flush()
        excerpt = "索引可以优化查询"
        snapshot = KnowledgeExtractionSnapshot(
            source_id=source.id,
            extractor_version="fixture",
            canonical_text=excerpt,
            digest=sha256(excerpt.encode()).hexdigest(),
            char_count=len(excerpt),
        )
        session.add(snapshot)
        session.flush()
        source.active_snapshot_id = snapshot.id
        session.add(
            KnowledgeEvidence(
                id="e0",
                source_id=source.id,
                snapshot_id=snapshot.id,
                kind="text",
                block_kind="paragraph",
                ordinal=0,
                char_start=0,
                char_end=len(excerpt),
                line_start=1,
                line_end=1,
                canonical_excerpt=excerpt,
                search_text=excerpt,
                content_hash=sha256(excerpt.encode()).hexdigest(),
            )
        )
        note = KnowledgeNote(title="索引", origin_kind="confirmed_interview_capture")
        session.add(note)
        session.flush()
        content = json.dumps(
            {
                "title": "索引",
                "blocks": [
                    {
                        "block_id": "b1",
                        "text": "索引用于查询",
                        "evidence_refs": [{"fragment_id": "f1", "excerpt": excerpt}],
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        version = KnowledgeNoteVersion(
            note_id=note.id,
            version_number=1,
            content_json=content,
            content_hash=sha256(content.encode()).hexdigest(),
            source_id=source.id,
            content_origin="user_edited_preview",
            capture_attempt_key="capture",
        )
        session.add(version)
        session.flush()
        note.current_version_id = version.id
        session.add(
            KnowledgeNoteEvidence(note_version_id=version.id, block_id="b1", evidence_id="e0")
        )
        session.commit()
        return source.id


def test_knowledge_reuses_current_dual_lane_provenance_and_fail_closed_integrity(business):
    sessions, service, _, _ = business
    source_id = seed_knowledge(sessions)
    result = service.call("search_knowledge", {"query": "索引"})
    assert {item["kind"] for item in result["items"]} == {"confirmed_note", "evidence"}
    assert all(item["source_id"] == source_id for item in result["items"])
    assert result["omitted_count"] == 0
    limited = service.call("search_knowledge", {"query": "索引", "limit": 1})
    assert len(limited["items"]) == 1 and limited["omitted_count"] == 1
    with sessions() as session:
        session.execute(text("UPDATE knowledge_evidence SET content_hash='bad' WHERE id='e0'"))
        session.commit()
    with pytest.raises(ExternalAgentError, match="knowledge_integrity_failed"):
        service.call("search_knowledge", {"query": "索引"})
    with sessions() as session:
        session.execute(
            text("UPDATE knowledge_sources SET archived_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": source_id},
        )
        session.commit()
    assert service.call("search_knowledge", {"query": "索引"})["items"] == []


@pytest.mark.parametrize("action", ["update_note", "delete_note", "delete_application"])
def test_review_warns_and_captured_knowledge_stops_when_original_note_changes(business, action):
    from offerpilot.knowledge.interview_capture import note_fingerprint

    sessions, service, first, _ = business
    source_id = seed_knowledge(sessions)
    note = NotesRepository(sessions).create(NoteCreate(company="云岚", application_id=first.id))
    with sessions() as session:
        session.add(
            KnowledgeCapturedSourceMetadata(
                source_id=source_id,
                origin_note_id=note.id,
                note_fingerprint=note_fingerprint(note),
                selected_fragments_json="[]",
                capture_schema_version="v1",
            )
        )
        session.commit()
    assert len(service.call("search_knowledge", {"query": "索引"})["items"]) == 2
    kind = "application" if action == "delete_application" else "note"
    resource_id = first.id if kind == "application" else note.id
    before = service.call(f"get_{kind}", {f"{kind}_id": resource_id})
    fields = {f"{kind}_id": resource_id, "expected_revision": before["revision"]}
    if action == "update_note":
        fields["changes"] = {"questions": "新的复盘"}
    pending = propose(service, f"propose_{action.split('_')[1]}_{action.split('_')[0]}", **fields)
    assert "Knowledge" in service.review(pending["request_id"])["proposal"]["effects"]
    assert approve(service, pending)["status"] == "committed"
    assert service.call("search_knowledge", {"query": "索引"})["items"] == []


def test_cli_business_grants_are_explicit_and_global_axes_are_separate(business, tmp_path):
    from typer.testing import CliRunner
    from offerpilot.cli import app

    _, _, first, _ = business
    result = CliRunner().invoke(
        app,
        [
            "mcp",
            "connect",
            "selected",
            "--application-id",
            str(first.id),
            "--data-dir",
            str(tmp_path),
            "--allow-application-writes",
            "--allow-resumes",
            "--allow-resume-writes",
            "--allow-notes",
            "--allow-note-writes",
            "--allow-knowledge",
        ],
    )
    assert result.exit_code == 0, result.output
    grant = json.loads(result.output)
    assert not grant["workspace"] and grant["application_ids"] == [first.id]
    assert set(grant["capabilities"]) == {
        "applications.write",
        "resumes.read",
        "resumes.write",
        "notes.read",
        "notes.write",
        "knowledge.read",
    }
    assert not grant["allow_scheduling"]
