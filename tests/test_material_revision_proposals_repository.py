from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from offerpilot.ai.types import Assistant
from offerpilot.db import init_database
from offerpilot.models import (
    Application,
    ApplicationEvent,
    ApplicationJDVersion,
    ApplicationMaterialKit,
    MaterialRevisionProposal,
    Resume,
)
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.material_kits import MaterialKitCreate, MaterialKitsRepository
from offerpilot.repositories import material_revision_proposals as material_revision_proposals_module
from offerpilot.repositories.material_revision_proposals import (
    MaterialProposalConflictError,
    MaterialProposalNotFound,
    MaterialProposalValidationError,
    MaterialRevisionProposalsRepository,
)
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


class _TrackedSession:
    def __init__(self, session, tracker):  # type: ignore[no-untyped-def]
        self._session = session
        self._tracker = tracker

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._session.__enter__()
        return self._session

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        try:
            return self._session.__exit__(*args)
        finally:
            self._tracker.open_sessions -= 1


class _TrackedSessionFactory:
    def __init__(self, factory):  # type: ignore[no-untyped-def]
        self._factory = factory
        self.open_sessions = 0

    def __call__(self):  # type: ignore[no-untyped-def]
        self.open_sessions += 1
        return _TrackedSession(self._factory(), self)


class ProposalModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(
            content=json.dumps(
                {
                    "summary": "Tailor the backend experience.",
                    "changes": [
                        {
                            "id": "change-fastapi",
                            "path": "/experience/0/highlights/0",
                            "before": "Built APIs",
                            "after": "Built FastAPI APIs for internal workflow automation",
                            "rationale": "Make the existing API experience specific.",
                            "evidence_refs": [
                                {
                                    "source": "resume",
                                    "path": "/experience/0/highlights/0",
                                    "excerpt": "Built APIs",
                                }
                            ],
                        },
                        {
                            "id": "change-assertion",
                            "path": "/raw_text",
                            "before": "Built APIs",
                            "after": "Built APIs; I led the migration.",
                            "rationale": "Include the user's explicit assertion.",
                            "evidence_refs": [
                                {
                                    "source": "user_assertion",
                                    "path": "/user_assertions/0/text",
                                    "excerpt": "I led the migration.",
                                }
                            ],
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )


def _ready(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    applications = ApplicationsRepository(session_factory)
    resumes = ResumesRepository(session_factory)
    kits = MaterialKitsRepository(session_factory)
    app = applications.create(ApplicationCreate(company_name="Acme", position_name="Backend"))
    with session_factory() as session:
        jd_version = ApplicationJDVersion(
            application_id=app.id,
            version_number=1,
            jd_text="FastAPI backend",
            content_sha256="material-revision-jd",
            source_kind="ui",
            idempotency_key="material-revision-jd-01",
            request_fingerprint_sha256="material-revision-request",
        )
        session.add(jd_version)
        session.commit()
        jd_version_id = jd_version.id
    resume = resumes.create(
        ResumeCreate(
            title="Backend Resume",
            parsed_data="Built APIs",
            content_json={
                "experience": [{"highlights": ["Built APIs"]}],
                "raw_text": "Built APIs",
            },
        )
    )
    kit = kits.create(
        MaterialKitCreate(
            application_id=app.id,
            resume_id=resume.id,
            jd_snapshot="FastAPI backend",
            jd_version_id=jd_version_id,
            content_json=json.dumps({"requirements": ["FastAPI"]}),
        )
    )
    return session_factory, app, resume, kit


def test_repository_generates_and_accepts_one_child_resume_transactionally(tmp_path) -> None:
    session_factory, app, resume, kit = _ready(tmp_path)
    repository = MaterialRevisionProposalsRepository(session_factory)

    proposal = repository.create_generated(
        app.id,
        "Highlight backend API experience",
        ["I led the migration."],
        ProposalModel(),
    )
    assert proposal.status == "draft"
    assert json.loads(proposal.source_snapshot_json)["resume"]["id"] == resume.id

    accepted, child, created = repository.accept(
        app.id,
        proposal.id,
        proposal.proposal_sha256,
        ["change-fastapi"],
    )

    assert created is True
    assert accepted.status == "accepted"
    assert child.parent_resume_id == resume.id
    assert child.is_master is False
    assert child.title == "Backend Resume · Acme Backend"
    assert json.loads(child.content_json)["experience"][0]["highlights"] == [
        "Built FastAPI APIs for internal workflow automation"
    ]
    with session_factory() as session:
        kit_row = session.get(ApplicationMaterialKit, kit.id)
        source_row = session.get(Resume, resume.id)
        events = list(session.scalars(select(ApplicationEvent)))
    assert kit_row is not None and kit_row.resume_id == child.id
    assert source_row is not None and json.loads(source_row.content_json)["raw_text"] == "Built APIs"
    assert [(event.subtype, event.tags) for event in events] == [
        ("material_proposal_accepted", ["material_proposal", f"proposal:{proposal.id}", f"resume:{child.id}"])
    ]


def test_repository_closes_snapshot_session_before_model_generation(tmp_path) -> None:
    base_factory, app, _resume, _kit = _ready(tmp_path)
    tracked_factory = _TrackedSessionFactory(base_factory)

    class NoSessionModel(ProposalModel):
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            assert tracked_factory.open_sessions == 0
            return super().complete(messages, tools)

    repository = MaterialRevisionProposalsRepository(tracked_factory)  # type: ignore[arg-type]

    proposal = repository.create_generated(app.id, "", ["I led the migration."], NoSessionModel())

    assert proposal.status == "draft"
    assert tracked_factory.open_sessions == 0


def test_repository_does_not_create_draft_when_application_is_deleted_during_generation(tmp_path) -> None:
    session_factory, app, _resume, _kit = _ready(tmp_path)
    repository = MaterialRevisionProposalsRepository(session_factory)

    class DeletesApplicationModel(ProposalModel):
        def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            ApplicationsRepository(session_factory).delete(app.id)
            return super().complete(messages, tools)

    with pytest.raises(MaterialProposalNotFound):
        repository.create_generated(app.id, "", ["I led the migration."], DeletesApplicationModel())

    with session_factory() as session:
        assert list(session.scalars(select(MaterialRevisionProposal))) == []


def test_repository_serializes_soft_delete_after_visibility_check(tmp_path, monkeypatch) -> None:
    base_factory, app, _resume, _kit = _ready(tmp_path)
    delete_engine = create_engine(
        f"sqlite:///{tmp_path / 'data.db'}",
        connect_args={"check_same_thread": False},
    )
    delete_factory = sessionmaker(bind=delete_engine, expire_on_commit=False)
    delete_ready = threading.Event()
    allow_delete_commit = threading.Event()
    delete_finished = threading.Event()
    order: list[str] = []

    def record(value: str) -> None:
        order.append(value)

    def delete_application() -> None:
        with delete_factory() as session:
            application = session.get(Application, app.id)
            assert application is not None
            application.deleted_at = datetime.now(timezone.utc)
            delete_ready.set()
            assert allow_delete_commit.wait(5)
            session.commit()
            record("delete_commit")
            delete_finished.set()

    class CoordinatedSession(Session):
        def commit(self) -> None:  # type: ignore[override]
            if self.info.get("material_proposal_write"):
                allow_delete_commit.set()
                delete_finished.wait(1)
                record("proposal_commit")
            super().commit()

    coordinated_factory = sessionmaker(
        bind=base_factory.kw["bind"],
        class_=CoordinatedSession,
        expire_on_commit=False,
    )
    repository = MaterialRevisionProposalsRepository(coordinated_factory)
    original_visible = material_revision_proposals_module._visible_application
    visible_calls = 0
    delete_thread: threading.Thread | None = None

    def visible_application(session: Session, application_id: int):
        nonlocal delete_thread, visible_calls
        result = original_visible(session, application_id)
        visible_calls += 1
        if visible_calls == 2:
            session.info["material_proposal_write"] = True
            delete_thread = threading.Thread(target=delete_application)
            delete_thread.start()
            assert delete_ready.wait(5)
        return result

    monkeypatch.setattr(
        material_revision_proposals_module,
        "_visible_application",
        visible_application,
    )

    proposal = repository.create_generated(
        app.id,
        "",
        ["I led the migration."],
        ProposalModel(),
    )
    assert delete_thread is not None
    delete_thread.join(timeout=5)
    assert not delete_thread.is_alive()
    assert order == ["proposal_commit", "delete_commit"]
    assert proposal.status == "draft"


def test_repository_accepts_empty_selection_only_as_validation_error_and_replays_accept(tmp_path) -> None:
    session_factory, app, _resume, _kit = _ready(tmp_path)
    repository = MaterialRevisionProposalsRepository(session_factory)
    proposal = repository.create_generated(app.id, "", ["I led the migration."], ProposalModel())

    with pytest.raises(MaterialProposalValidationError):
        repository.accept(app.id, proposal.id, proposal.proposal_sha256, [])

    accepted, child, created = repository.accept(
        app.id, proposal.id, proposal.proposal_sha256, ["change-fastapi"]
    )
    replayed, same_child, replay_created = repository.accept(
        app.id, proposal.id, "stale", ["change-assertion"]
    )

    assert created is True
    assert replay_created is False
    assert replayed.id == accepted.id
    assert same_child.id == child.id


def test_repository_source_drift_has_no_partial_writes(tmp_path) -> None:
    session_factory, app, resume, _kit = _ready(tmp_path)
    repository = MaterialRevisionProposalsRepository(session_factory)
    proposal = repository.create_generated(app.id, "", ["I led the migration."], ProposalModel())
    ResumesRepository(session_factory).update(resume.id, {"title": "Changed"})

    with pytest.raises(MaterialProposalConflictError):
        repository.accept(app.id, proposal.id, proposal.proposal_sha256, ["change-fastapi"])

    with session_factory() as session:
        assert session.scalar(select(Resume).where(Resume.parent_resume_id == resume.id)) is None
        assert list(session.scalars(select(ApplicationEvent))) == []


def test_repository_reject_is_idempotent_but_accept_after_reject_conflicts(tmp_path) -> None:
    session_factory, app, _resume, _kit = _ready(tmp_path)
    repository = MaterialRevisionProposalsRepository(session_factory)
    proposal = repository.create_generated(app.id, "", ["I led the migration."], ProposalModel())

    rejected = repository.reject(app.id, proposal.id)
    replayed = repository.reject(app.id, proposal.id)

    assert rejected.id == replayed.id
    assert rejected.status == "rejected"
    with pytest.raises(MaterialProposalConflictError):
        repository.accept(app.id, proposal.id, proposal.proposal_sha256, ["change-fastapi"])
