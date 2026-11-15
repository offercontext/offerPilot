"""Synthetic, process-level acceptance matrix for the bounded provider gate.

The caller owns an empty temporary directory and the stage capability.  This
module never reads a user configuration or a credential.  A stage must write
its single-provider loopback configuration before yielding and revoke its
capability on exit.  Closed-stage requests below must not reach upstream.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, cast

from scripts.simplification_ai_validation.broker import require_linux

import httpx
from sqlalchemy import select

# Prevent import-time model-price downloads before any production module loads.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from offerpilot.db import session_factory_for_data_dir
from offerpilot.models import (
    ApplicationEvent,
    JDAnalysis,
    MockInterviewAttempt,
    MockInterviewFeedbackProposal,
    MockInterviewReviewDraft,
    MockInterviewTurn,
    Question,
    Resume,
    ResumeMatch,
)
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.interview_practice_cases import InterviewPracticeCaseRepository
from offerpilot.repositories.notes import NoteCreate, NotesRepository
from offerpilot.repositories.questions import question_hash
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository

OPERATIONS = (
    "cli_analyze", "http_analyze", "cli_match", "http_match",
    "cli_questions", "http_questions", "quick_start", "quick_next",
    "quick_feedback", "event_start", "event_next", "event_feedback",
)
Stage = Callable[[str], AbstractContextManager[Any]]
ROOT = Path(__file__).resolve().parents[2]
PROCESS_TIMEOUT = 180
_GUARDED_ENTRY = "import tests._offline_network_guard; "
_FEEDBACK_FIELDS = ("strengths", "practice_points", "follow_up_questions", "next_practice_steps")


class MatrixFailure(RuntimeError):
    """A fixed-label failure; never expose a provider reply or process output."""

    def __init__(self, operation: str, check: str):
        self.operation = operation
        self.check = check
        super().__init__(f"{operation}: {check}")


def _require(condition: Any, operation: str, check: str) -> None:
    if not condition:
        raise MatrixFailure(operation, check)


def child_environment(data_dir: Path) -> dict[str, str]:
    """Construct, rather than filter, the child environment. No inherited keys."""
    home = data_dir / "process-home"
    temporary = data_dir / "process-tmp"
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "TMPDIR": str(temporary),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OFFERPILOT_DATA": str(data_dir),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DO_NOT_TRACK": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
    }


def _cli(data_dir: Path, operation: str, arguments: list[str], *, error: str = "") -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _GUARDED_ENTRY + "from offerpilot.cli import main; main()",
             *arguments],
            cwd=ROOT, env=child_environment(data_dir), capture_output=True,
            timeout=PROCESS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MatrixFailure(operation, "cli_process_failed") from None
    if error:
        _require(result.returncode != 0, operation, "cli_negative_case_succeeded")
        try:
            body = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise MatrixFailure(operation, "cli_error_contract") from None
        _require(body.get("error_code", body.get("code")) == error,
                 operation, "cli_error_contract")
    else:
        _require(result.returncode == 0, operation, "cli_process_nonzero")


def _capture(stream: BinaryIO, buffer: bytearray) -> None:
    # Drain without disk logs; retain at most 64 KiB until process cleanup.
    while chunk := stream.read(8192):
        buffer.extend(chunk[:max(0, 65536 - len(buffer))])


@contextmanager
def _api(data_dir: Path, operation: str) -> Iterator[httpx.Client]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = (
        _GUARDED_ENTRY + "import uvicorn; "
        "uvicorn.run('offerpilot.api:create_app', factory=True, host='127.0.0.1', "
        f"port={port}, log_level='critical', access_log=False)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", command], cwd=ROOT, env=child_environment(data_dir),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    buffers = [bytearray(), bytearray()]
    threads = []
    for stream, buffer in zip((process.stdout, process.stderr), buffers):
        if stream is not None:
            thread = threading.Thread(target=_capture, args=(stream, buffer), daemon=True)
            thread.start()
            threads.append(thread)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                          timeout=PROCESS_TIMEOUT, follow_redirects=False) as client:
            deadline = time.monotonic() + 60
            while True:
                _require(process.poll() is None, operation, "api_startup_failed")
                try:
                    response = client.get("/api/health", timeout=0.5)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                _require(time.monotonic() < deadline, operation, "api_startup_timeout")
                time.sleep(0.05)
            yield client
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        for buffer in buffers:
            buffer.clear()


def _request(client: httpx.Client, operation: str, method: str, path: str,
             payload: dict[str, Any] | None = None, *, status: int = 200,
             error: str = "") -> Any:
    try:
        response = client.request(method, path, json=payload)
    except httpx.HTTPError:
        raise MatrixFailure(operation, "http_transport_failed") from None
    _require(response.status_code == status, operation, f"http_status_{response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise MatrixFailure(operation, "http_json_contract") from None
    if error:
        _require(body.get("error_code", body.get("code")) == error,
                 operation, "http_error_contract")
    return body


@dataclass(frozen=True)
class Sources:
    application_ids: tuple[int, int]
    version_ids: tuple[int, int]
    old_version_ids: tuple[int, int]
    jd_texts: tuple[str, str]
    resume_id: int
    quick_resume_id: int
    event_id: int
    case_id: int


def _seed(factory: Any) -> Sources:
    applications = ApplicationsRepository(factory)
    versions = ApplicationJDService(factory)
    resumes = ResumesRepository(factory)
    resume = resumes.create(ResumeCreate(
        name="Synthetic event resume", parsed_data="Built a Python queue with idempotent jobs.",
        content_json={"raw_text": "Built a Python queue with idempotent jobs."},
    ))
    quick_resume = resumes.create(ResumeCreate(
        name="Synthetic quick resume", parsed_data="Built SQL reporting and tested query plans.",
        content_json={"raw_text": "Built SQL reporting and tested query plans."},
    ))
    app_ids, version_ids, old_ids, texts = [], [], [], []
    for index, subject in enumerate(("Python queue reliability", "SQL query plan optimization")):
        application = applications.create(ApplicationCreate(
            company_name=f"Synthetic Company {index}", position_name=f"Synthetic {subject}",
        ))
        old = versions.create_version(
            application.id, jd_text="Superseded synthetic JD", source_url=None,
            source_kind="ui", expected_current_version_id=None,
            idempotency_key=f"matrix-jd-old-{index:04d}",
        ).version
        text = f"  Synthetic role: {subject}. Explain design trade-offs and testing.\n"
        version = versions.create_version(
            application.id, jd_text=text, source_url=None, source_kind="ui",
            expected_current_version_id=old.id, idempotency_key=f"matrix-jd-current-{index}",
        ).version
        NotesRepository(factory).create(NoteCreate(
            company=application.company_name, application_id=application.id,
            questions=f"Explain {subject}, give a measurable example and a validation plan.",
        ))
        app_ids.append(application.id)
        version_ids.append(version.id)
        old_ids.append(old.id)
        texts.append(text)
    with factory() as session:
        event = ApplicationEvent(
            application_id=app_ids[0], event_type="interview", status="todo",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1), duration_minutes=30,
        )
        session.add(event)
        session.commit()
        event_id = event.id
    case, _ = InterviewPracticeCaseRepository(factory).create_or_replay(
        idempotency_key="matrix-quick-case", position_name="Synthetic SQL analyst",
        jd_text="Synthetic quick-only SQL role: query plans, indexes and reporting validation.",
        resume_id=quick_resume.id,
    )
    return Sources((app_ids[0], app_ids[1]), (version_ids[0], version_ids[1]),
                   (old_ids[0], old_ids[1]), (texts[0], texts[1]),
                   resume.id, quick_resume.id, event_id, case.id)


def _workflow_command(kind: str, sources: Sources, index: int, version_id: int) -> list[str]:
    command = ["analyze"] if kind == "analyze" else ["resume", "match", "--resume",
                                                              str(sources.resume_id)]
    return command + ["--app", str(sources.application_ids[index]), "--jd-version", str(version_id)]


def _workflows(data_dir: Path, stage: Stage, factory: Any, sources: Sources) -> None:
    for kind in ("analyze", "match", "questions"):
        for index, adapter in enumerate(("cli", "http")):
            operation = f"{adapter}_{kind}"
            app_id, version_id = sources.application_ids[index], sources.version_ids[index]
            payload: dict[str, Any] = {"application_id": app_id, "jd_version_id": version_id}
            route = "/api/jd/analyze" if kind == "analyze" else (
                f"/api/resumes/{sources.resume_id}/match"
            )
            command = _workflow_command(kind, sources, index, version_id)
            row_type: type[Any] = JDAnalysis if kind == "analyze" else ResumeMatch
            if kind == "questions":
                payload = {"application_id": app_id, "source": "notes", "count": 1,
                           "topic": f"synthetic-{adapter}"}
                route = "/api/questions/generate"
                command = ["question", "generate", "--app", str(app_id), "--source", "notes",
                           "--count", "1", "--topic", f"synthetic-{adapter}"]
                row_type = Question
            with ExitStack() as stack:
                with stage(operation):
                    if adapter == "cli":
                        _cli(data_dir, operation, command)
                    else:
                        client = stack.enter_context(_api(data_dir, operation))
                        _request(client, operation, "POST", route, payload, status=201)
                with factory() as session:
                    rows = list(session.scalars(select(row_type).where(
                        row_type.application_id == app_id,
                    )))
                    _require(rows, operation, "persisted_result_missing")
                    if kind == "questions":
                        _require(len(rows) == 1 and all(
                            row.source_type == "ai_notes" and row.question.strip()
                            and row.difficulty in {"easy", "medium", "hard"}
                            and row.status == "new" and row.topic == f"synthetic-{adapter}"
                            and row.question == row.question.strip()
                            and row.category == row.category.strip()
                            and row.reference_answer == row.reference_answer.strip()
                            and all(isinstance(tag, str) for tag in row.tags)
                            and row.question_hash == question_hash(row.question)
                            for row in rows), operation, "notes_count_and_source_identity")
                    else:
                        _require(len(rows) == 1 and rows[0].jd_version_id == version_id
                                 and rows[0].jd_text == sources.jd_texts[index],
                                 operation, "jd_version_identity")
                        _require(isinstance(json.loads(rows[0].result), dict)
                                 and bool(json.loads(rows[0].result)),
                                 operation, "analysis_result_empty")
                # All negative checks run after capability revocation, not while
                # another operation's ticket can accidentally pay for them.
                if kind != "questions":
                    for invalid_version in (sources.old_version_ids[index],
                                            sources.version_ids[1 - index]):
                        if adapter == "cli":
                            _cli(data_dir, operation,
                                 _workflow_command(kind, sources, index, invalid_version),
                                 error="application_jd_source_conflict")
                        else:
                            _request(client, operation, "POST", route,
                                     {"application_id": app_id, "jd_version_id": invalid_version},
                                     status=409, error="application_jd_source_conflict")
                if adapter == "http":
                    read_path = "/api/questions" if kind == "questions" else (
                        "/api/jd/analyses" if kind == "analyze"
                        else f"/api/resumes/{sources.resume_id}/matches"
                    )
                    _require(_request(client, operation, "GET", read_path),
                             operation, "http_readback_empty")


def _stored_attempt(factory: Any, attempt_id: int, operation: str) -> MockInterviewAttempt:
    with factory() as session:
        attempt = session.get(MockInterviewAttempt, attempt_id)
        _require(attempt is not None, operation, "attempt_missing")
        session.expunge(attempt)
        return cast(MockInterviewAttempt, attempt)


def _feedback(body: dict[str, Any], operation: str, answers: dict[int, str]) -> list[dict[str, Any]]:
    proposal = body["proposal"]
    _require(body["proposal_status"] == proposal["proposal_status"] == "normal",
             operation, "normal_feedback_required")
    substantive = [item for field in ("strengths", "practice_points", "next_practice_steps")
                   for item in proposal[field]]
    _require(substantive, operation, "nonempty_feedback_required")
    for item in substantive:
        refs = item.get("evidence_refs", [])
        _require(item.get("text", "").strip() and any(
            ref.get("source") == "turn"
            and any(ref.get("path") == f"/turns/{number:03d}/answer"
                    and ref.get("excerpt") and ref["excerpt"] in answer
                    for number, answer in answers.items())
            for ref in refs
        ), operation, "feedback_turn_evidence_required")
    return [item for field in _FEEDBACK_FIELDS for item in proposal[field]]


def _mock(data_dir: Path, stage: Stage, factory: Any, sources: Sources, kind: str) -> None:
    quick = kind == "quick"
    base = (f"/api/interview-practice-cases/{sources.case_id}/mock-interview/attempts" if quick
            else f"/api/applications/{sources.application_ids[0]}/events/"
                 f"{sources.event_id}/mock-interview/attempts")
    start: dict[str, Any] = {"attempt_idempotency_key": f"matrix-{kind}-attempt",
                             "initial_question_idempotency_key": f"matrix-{kind}-question-1"}
    if not quick:
        start.update({"resume_id": sources.resume_id, "jd_version_id": sources.version_ids[0]})
    answers = {
        1: ("I compared baseline and indexed SQL plans, measured p95 on 10000 synthetic rows, "
            "and verified identical result counts." if quick else
            "I assigned idempotency keys to queue jobs, tested duplicate delivery, measured p95 "
            "latency, and verified only one committed result per job."),
        2: ("I would repeat the benchmark with skewed data and compare p95 and result hashes."
            if quick else "I would inject worker crashes and verify retries never commit twice."),
    }
    attempt_id = 0
    frozen = ""
    fingerprint = ""
    first_question = ""
    for step in ("start", "next", "feedback"):
        operation = f"{kind}_{step}"
        path = base if step == "start" else f"{base}/{attempt_id}/" + (
            "turns/2/question" if step == "next" else "finish"
        )
        payload = start if step == "start" else (
            {"question_idempotency_key": f"matrix-{kind}-question-2"} if step == "next"
            else {"feedback_idempotency_key": f"matrix-{kind}-feedback"}
        )
        with ExitStack() as stack:
            with stage(operation):
                client = stack.enter_context(_api(data_dir, operation))
                body = _request(client, operation, "POST", path, payload, status=201)
            replay = _request(client, operation, "POST", path, payload)
            if step == "start":
                attempt_id = body["attempt_id"]
                _require(attempt_id == replay["attempt_id"], operation, "attempt_replay_identity")
                attempt = _stored_attempt(factory, attempt_id, operation)
                frozen, fingerprint = attempt.input_snapshot_json, attempt.source_fingerprint
                snapshot = json.loads(frozen)
                _require(attempt.context_kind == ("quick_practice" if quick else "application_event"),
                         operation, "mock_context_kind")
                if quick:
                    _require(attempt.application_id is None and attempt.event_id is None
                             and attempt.jd_version_id is None
                             and attempt.practice_case_id == sources.case_id
                             and "application" not in snapshot and "event" not in snapshot
                             and "quick-only SQL" in snapshot["jd"]["text"],
                             operation, "quick_sources_not_independent")
                    InterviewPracticeCaseRepository(factory).archive(sources.case_id)
                    with factory() as session:
                        resume = session.get(Resume, sources.quick_resume_id)
                        resume.content_json = '{"raw_text":"Changed synthetic live resume"}'
                        session.commit()
                else:
                    _require(attempt.application_id == sources.application_ids[0]
                             and attempt.event_id == sources.event_id and attempt.practice_case_id is None
                             and attempt.jd_version_id == sources.version_ids[0]
                             and snapshot["jd"]["text"] == sources.jd_texts[0],
                             operation, "event_source_identity")
                    ApplicationJDService(factory).create_version(
                        sources.application_ids[0], jd_text="New synthetic current JD after start",
                        source_url=None, source_kind="ui",
                        expected_current_version_id=sources.version_ids[0],
                        idempotency_key="matrix-event-jd-evolved",
                    )
                rejected = {**start, "attempt_idempotency_key": f"matrix-{kind}-new-rejected"}
                _request(client, operation, "POST", base, rejected,
                         status=404 if quick else 409,
                         error="interview_practice_case_not_found" if quick
                         else "mock_interview_source_conflict")
            attempt = _stored_attempt(factory, attempt_id, operation)
            _require(attempt.input_snapshot_json == frozen and attempt.source_fingerprint == fingerprint,
                     operation, "frozen_source_changed")
            if step != "feedback":
                turn = body["turn"]
                _require(turn == replay["turn"] and turn["question"].strip(),
                         operation, "question_replay_identity")
                if step == "start":
                    first_question = turn["question"]
                else:
                    _require(turn["question"] != first_question and turn["question_kind"] == "follow_up"
                             and turn["parent_turn_no"] == 1,
                             operation, "next_question_not_followup")
                    with factory() as session:
                        stored = session.scalar(select(MockInterviewTurn).where(
                            MockInterviewTurn.attempt_id == attempt_id, MockInterviewTurn.turn_no == 2,
                        ))
                        refs = json.loads(stored.question_source_snapshot_json)["selected_evidence_refs"]
                    _require(any(ref["source"] == "turn" and ref["path"] == "/turns/001/answer"
                                 and ref["excerpt"] and ref["excerpt"] in answers[1] for ref in refs),
                             operation, "next_missing_selected_latest_answer")
                number = 1 if step == "start" else 2
                _request(client, operation, "POST", f"{base}/{attempt_id}/turns",
                         {"turn_no": number, "answer_text": answers[number],
                          "turn_idempotency_key": f"matrix-{kind}-answer-{number}"})
                if quick:
                    saved = _request(client, operation, "GET", f"{base}/{attempt_id}")
                    _require(saved["turns"][-1]["answer"] == answers[number],
                             operation, "answer_readback")
            else:
                _require(replay["proposal_id"] == body["proposal_id"]
                         and replay["proposal_hash"] == body["proposal_hash"],
                         operation, "feedback_replay_identity")
                blocks = _feedback(body, operation, answers)
                confirmation = {"proposal_id": body["proposal_id"],
                                "confirmation_idempotency_key": f"matrix-{kind}-confirm",
                                "selected_blocks": blocks}
                if quick:
                    _request(client, operation, "POST", f"{base}/{attempt_id}/review-drafts",
                             confirmation, status=422, error="quick_practice_review_not_available")
                else:
                    confirmed = _request(client, operation, "POST",
                                         f"{base}/{attempt_id}/review-drafts", confirmation, status=201)
                    confirmed_replay = _request(client, operation, "POST",
                                                f"{base}/{attempt_id}/review-drafts", confirmation)
                    _require(confirmed == confirmed_replay
                             and confirmed["application_id"] == sources.application_ids[0]
                             and confirmed["event_id"] == sources.event_id,
                             operation, "isolated_confirmation_identity")
                history = _request(client, operation, "GET", base)["items"]
                _require(len(history) == 1 and history[0]["attempt_id"] == attempt_id
                         and history[0]["proposal_id"] == body["proposal_id"]
                         and [turn["answer"] for turn in history[0]["turns"]] == list(answers.values()),
                         operation, "feedback_history_readback")


def run_matrix(data_dir: Path, stage: Stage) -> dict[str, Any]:
    """Run exactly twelve AI entrypoints; fail closed, with no automatic reruns.

    ``data_dir`` must be a new empty directory owned by this run.  The caller
    deletes it afterwards; only this function's bounded summary is an artifact.
    """
    require_linux()
    _require(not data_dir.is_symlink(), "matrix", "data_dir_symlink")
    data_dir = data_dir.resolve()
    _require(data_dir.is_dir() and not any(data_dir.iterdir()), "matrix", "data_dir_not_empty")
    factory = session_factory_for_data_dir(data_dir)
    try:
        sources = _seed(factory)
        _workflows(data_dir, stage, factory, sources)
        _mock(data_dir, stage, factory, sources, "quick")
        _mock(data_dir, stage, factory, sources, "event")
        with factory() as session:
            questions = list(session.scalars(select(Question)))
            _require(len({row.question_hash for row in questions}) == len(questions) == 2,
                     "matrix", "question_hashes_must_be_unique")
            counts = {
                "analyses": len(list(session.scalars(select(JDAnalysis)))),
                "matches": len(list(session.scalars(select(ResumeMatch)))),
                "questions": len(list(session.scalars(select(Question)))),
                "attempts": len(list(session.scalars(select(MockInterviewAttempt)))),
                "turns": len(list(session.scalars(select(MockInterviewTurn)))),
                "feedback": len(list(session.scalars(select(MockInterviewFeedbackProposal)))),
                "confirmed_event_drafts": len(list(session.scalars(select(MockInterviewReviewDraft)))),
            }
            _require(counts == {"analyses": 2, "matches": 2, "questions": 2,
                                "attempts": 2, "turns": 4,
                                "feedback": 2, "confirmed_event_drafts": 1},
                     "matrix", "unexpected_persisted_counts")
        return {"passed": True, "operations": list(OPERATIONS), "counts": counts,
                "closed_gate_replay": True, "stale_and_foreign_versions_rejected": True,
                "frozen_sources_verified": True, "normal_feedback_with_turn_evidence": True}
    finally:
        factory.kw["bind"].dispose()
