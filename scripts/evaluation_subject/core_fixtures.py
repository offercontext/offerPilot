"""Synthetic fixture construction through target repositories and ingestion."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def prepare(data: Path, root: Path, item: str, execution_item: str | None = None) -> dict[str, Any]:
    from offerpilot.db import init_database
    from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
    from offerpilot.repositories.application_events import (
        ApplicationEventCreate,
        ApplicationEventsRepository,
    )

    sessions = init_database(data / "data.db")
    aliases: dict[str, Any] = {}
    fixture = json.loads(
        (root / "evals/fixtures/synthetic-interviews-v1.json").read_text(encoding="utf-8")
    )
    for row in fixture["applications"]:
        app = ApplicationsRepository(sessions).create(
            ApplicationCreate(company_name=row["company_name"], position_name=row["position_name"])
        )
        aliases[row["alias"]] = app.id
    for row in fixture["events"]:
        event = ApplicationEventsRepository(sessions).create(
            ApplicationEventCreate(
                application_id=aliases[row["application"]],
                event_type=row["event_type"],
                subtype=row["subtype"],
                scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
                duration_minutes=row["duration_minutes"],
            )
        )
        aliases[row["alias"]] = event.id
    core = json.loads((root / "evals/fixtures/synthetic-core-v1.json").read_text(encoding="utf-8"))
    if item.startswith(("E09.", "E10.")):
        from offerpilot.context_sources.models import ContextContributorSettings
        from offerpilot.context_sources.contracts import ContextPolicies, ContributorPolicy
        from offerpilot.confirmed_memory.repository import ConfirmedMemoryRepository, MemoryMutation

        memory = ConfirmedMemoryRepository(sessions).mutate(
            None,
            MemoryMutation(
                mutation_id=uuid4(),
                action="confirm",
                expected_version=0,
                confirmed=True,
                content=core["memory"],
            ),
        )
        aliases["memory_id"], aliases["memory_version"] = memory["id"], memory["current_version"]
        if execution_item == "E11.memory_withdraw_scope":
            extension = json.loads(
                (root / "evals/fixtures/synthetic-core-extension-v1.json").read_bytes()
            )
            untouched = ConfirmedMemoryRepository(sessions).mutate(
                None,
                MemoryMutation(
                    mutation_id=uuid4(),
                    action="confirm",
                    expected_version=0,
                    confirmed=True,
                    content=extension["unrelated_memory"],
                ),
            )
            aliases["untouched_memory_id"] = untouched["id"]
            aliases["untouched_memory_version"] = untouched["current_version"]
        with sessions() as s:
            s.add(
                ContextContributorSettings(
                    id=1,
                    revision=1,
                    settings_json=ContextPolicies(
                        knowledge_context=ContributorPolicy(enabled=True, max_units=8192)
                    ).model_dump_json(),
                )
            )
            s.commit()
    if item.startswith("E10."):
        from offerpilot.repositories.notes import NotesRepository, NoteCreate
        from offerpilot.repositories.interview_knowledge_capture import (
            InterviewKnowledgeCaptureRepository,
        )
        from offerpilot.knowledge.service import KnowledgeIngestService, IngestRequest
        from offerpilot.knowledge.repository import KnowledgeRepository
        from offerpilot.knowledge.worker import ExtractionWorker, KnowledgeJobRunner

        note = NotesRepository(sessions).create(
            NoteCreate(company="合成知识公司", position="QA", questions=core["note_fact"])
        )
        captures = InterviewKnowledgeCaptureRepository(sessions)
        preview = captures.prepare_preview(
            note.id,
            "core-fixture",
            "direct",
            [
                {
                    "fragment_id": "f1",
                    "path": "/questions",
                    "start": 0,
                    "end": len(core["note_fact"]),
                    "text": core["note_fact"],
                }
            ],
        )
        saved = captures.confirm(
            note.id,
            preview.attempt_key,
            preview.note_fingerprint,
            preview.preview["title"],
            preview.preview["blocks"],
        )
        aliases["knowledge_note_id"], aliases["knowledge_note_version"] = (
            saved.note_id,
            saved.version_id,
        )
        aliases["note_evidence"] = [r["id"] for r in saved.evidence]
        raw = core["evidence_fact"] + (
            "\n" + core["injection"] if item.endswith("injection") else ""
        )
        repo = KnowledgeRepository(sessions)
        source = KnowledgeIngestService(repo, data, sessions).ingest(
            IngestRequest(
                filename="core-evidence.md", content_bytes=raw.encode(), title_hint="恢复协议紫鹭"
            )
        )
        results = KnowledgeJobRunner(repo, ExtractionWorker(repo, data, sessions)).tick_extraction()
        extracted = repo.get_source(source.source.id)
        if (
            any(not r.accepted for r in results)
            or extracted is None
            or extracted.extraction_status != "extracted"
        ):
            raise ValueError("synthetic source extraction did not finish")
        aliases["new_source_id"] = source.source.id
    sessions.kw["bind"].dispose()
    (data / "fixture-aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
    return aliases
