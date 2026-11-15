"""The same business matrix must hold through the real HTTP and CLI adapters."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from sqlalchemy import delete

from offerpilot.models import (
    Application,
    ApplicationJDVersion,
    InterviewNote,
    JDAnalysis,
    Question,
    Resume,
    ResumeMatch,
)

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app
from offerpilot.cli import app as cli_app
from offerpilot.db import session_factory_for_data_dir
from offerpilot.repositories.application_jd_versions import ApplicationJDService
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
from offerpilot.repositories.jd import JDAnalysesRepository
from offerpilot.repositories.notes import NoteCreate, NotesRepository
from offerpilot.repositories.questions import QuestionsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


class StubModel:
    def __init__(self):
        self.reply = '{"summary":"ok","match_score":80}'
        self.messages = []
        self.on_complete = None
        self.error = None

    def complete(self, messages, tools):
        self.messages.append(messages)
        if self.on_complete is not None:
            self.on_complete()
        if self.error is not None:
            raise self.error
        return Assistant(content=self.reply)


@pytest.fixture(scope="module")
def analysis_environment(tmp_path_factory):
    # Startup migrations and the unrelated app composition are covered elsewhere.
    # This contract matrix exercises real routes, CLI parsing and repositories
    # against one disposable database, resetting only its test-owned rows per case.
    data_dir = tmp_path_factory.mktemp("analysis-contract")
    factory = session_factory_for_data_dir(data_dir)
    model = StubModel()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("offerpilot.api.session_factory_for_data_dir", lambda _: factory)
        client = TestClient(create_app(data_dir=data_dir, chat_model=model))
    yield data_dir, factory, model, client
    client.close()
    factory.kw["bind"].dispose()


@pytest.fixture(params=["api", "cli"])
def entrypoint(request, analysis_environment, monkeypatch):
    data_dir, factory, model, client = analysis_environment
    model.__init__()
    with factory() as session:
        for row_type in (
            Question,
            JDAnalysis,
            ResumeMatch,
            InterviewNote,
            ApplicationJDVersion,
            Resume,
            Application,
        ):
            session.execute(delete(row_type))
        session.commit()
    monkeypatch.setattr("offerpilot.cli.session_factory_for_data_dir", lambda _: factory)
    application = ApplicationsRepository(factory).create(
        ApplicationCreate(company_name="Acme", position_name="Backend")
    )
    versions = ApplicationJDService(factory)
    version = versions.create_version(
        application.id,
        jd_text="  Python backend\n原始岗位文本🦀  ",
        source_url=None,
        source_kind="ui",
        expected_current_version_id=None,
        idempotency_key="analysis-contract-01",
    ).version
    resumes = ResumesRepository(factory)
    resume = resumes.create(ResumeCreate(name="Resume", parsed_data="Python résumé"))
    monkeypatch.setenv("OFFERPILOT_DATA", str(data_dir))
    monkeypatch.setattr("offerpilot.cli._build_ai_model", lambda: model)

    class EntryPoint:
        kind = request.param

        def invoke(self, operation, payload):
            if self.kind == "api":
                route = {
                    "analyze": "/api/jd/analyze",
                    "match": f"/api/resumes/{resume.id}/match",
                    "questions": "/api/questions/generate",
                }[operation]
                response = client.post(route, json=payload)
                return response.status_code, response.json()
            command = {
                "analyze": ["analyze"],
                "match": ["resume", "match", "--resume", str(resume.id)],
                "questions": ["question", "generate"],
            }[operation]
            flags = {
                "application_id": "--app",
                "jd_version_id": "--jd-version",
                "jd_text": "--jd",
                "jd_url": "--jd-url",
                "count": "--count",
                "source": "--source",
                "topic": "--topic",
            }
            for key, value in payload.items():
                command.extend([flags[key], str(value)])
            result = CliRunner().invoke(cli_app, command)
            if result.exit_code == 0:
                return 201, {"output": result.output}
            # The CLI reports the same stable business error code as HTTP.
            return 400, json.loads(result.output)

        def rows(self, operation):
            return (
                JDAnalysesRepository(factory).list()
                if operation == "analyze"
                else resumes.list_matches(resume.id)
            )

        def advance(self):
            return versions.create_version(
                application.id,
                jd_text="Changed JD",
                source_url=None,
                source_kind="ui",
                expected_current_version_id=version.id,
                idempotency_key="analysis-contract-02",
            )

    endpoint = EntryPoint()
    endpoint.application = application
    endpoint.version = version
    endpoint.resume = resume
    endpoint.model = model
    endpoint.factory = factory
    return endpoint


def assert_error(endpoint, response, code, status):
    actual_status, body = response
    assert actual_status == (status if endpoint.kind == "api" else 400)
    assert body.get("error_code", body.get("code")) == code


@pytest.mark.parametrize("operation", ["analyze", "match"])
def test_linked_text_is_rejected_before_provider(entrypoint, operation):
    result = entrypoint.invoke(
        operation,
        {
            "application_id": entrypoint.application.id,
            "jd_text": "arbitrary different JD",
        },
    )
    assert_error(entrypoint, result, "application_jd_version_required", 422)
    assert entrypoint.model.messages == []
    assert entrypoint.rows(operation) == []


@pytest.mark.parametrize("operation", ["analyze", "match"])
def test_linked_version_preserves_source_identity(entrypoint, operation):
    result = entrypoint.invoke(
        operation,
        {
            "application_id": entrypoint.application.id,
            "jd_version_id": entrypoint.version.id,
        },
    )
    assert result[0] == 201
    (row,) = entrypoint.rows(operation)
    assert row.application_id == entrypoint.application.id
    assert row.jd_version_id == entrypoint.version.id
    assert row.jd_text == entrypoint.version.jd_text
    assert entrypoint.version.jd_text in entrypoint.model.messages[0][1].content


@pytest.mark.parametrize("operation", ["analyze", "match"])
@pytest.mark.parametrize("case", ["missing", "stale", "foreign", "text_and_version"])
def test_invalid_linked_input_never_consumes_provider(entrypoint, operation, case):
    payload = {"application_id": entrypoint.application.id}
    if case != "missing":
        payload["jd_version_id"] = entrypoint.version.id
    if case == "stale":
        entrypoint.advance()
    if case == "foreign":
        payload["application_id"] += 1000
    if case == "text_and_version":
        payload["jd_text"] = "Do not silently replace this text"
    conflict = case in {"stale", "foreign"}
    assert_error(
        entrypoint,
        entrypoint.invoke(operation, payload),
        "application_jd_source_conflict" if conflict else "application_jd_version_required",
        409 if conflict else 422,
    )
    assert entrypoint.model.messages == []
    assert entrypoint.rows(operation) == []


@pytest.mark.parametrize("operation", ["analyze", "match"])
def test_source_change_during_provider_cannot_publish(entrypoint, operation):
    entrypoint.model.on_complete = entrypoint.advance
    result = entrypoint.invoke(
        operation,
        {
            "application_id": entrypoint.application.id,
            "jd_version_id": entrypoint.version.id,
        },
    )
    assert_error(entrypoint, result, "application_jd_source_conflict", 409)
    assert len(entrypoint.model.messages) == 1
    assert entrypoint.rows(operation) == []


@pytest.mark.parametrize("operation", ["analyze", "match"])
def test_independent_text_preserves_original_without_inventing_link(entrypoint, operation):
    text = "  independent JD\n不关联岗位🦀  "
    assert entrypoint.invoke(operation, {"jd_text": text})[0] == 201
    (row,) = entrypoint.rows(operation)
    assert row.jd_text == text
    assert row.application_id is None
    assert row.jd_version_id is None


@pytest.mark.parametrize("operation", ["analyze", "match", "questions"])
@pytest.mark.parametrize(
    "reply",
    [
        "not JSON secret-source-text",
        "[]",
        '{"summary":"a","summary":"secret-key"}',
        '{"summary":NaN}',
        pytest.param('{"summary":"' + "x" * (128 * 1024) + '"}', id="oversized-json"),
    ],
)
def test_invalid_model_output_is_bounded_safe_and_not_persisted(entrypoint, operation, reply):
    entrypoint.model.reply = reply
    payload = {"jd_text": "independent JD"}
    if operation == "questions":
        NotesRepository(entrypoint.factory).create(
            NoteCreate(company="Acme", questions="Explain Python")
        )
        payload = {"source": "notes"}
    result = entrypoint.invoke(operation, payload)
    assert_error(entrypoint, result, "ai_invalid_response", 502)
    assert "secret-source-text" not in str(result[1])
    assert "secret-key" not in str(result[1])
    rows = (
        QuestionsRepository(entrypoint.factory).list()
        if operation == "questions"
        else entrypoint.rows(operation)
    )
    assert rows == []


@pytest.mark.parametrize("operation", ["analyze", "match", "questions"])
def test_provider_failure_does_not_echo_raw_error(entrypoint, operation):
    entrypoint.model.error = RuntimeError("Bearer sk-private-token; private résumé text")
    payload = {"jd_text": "independent JD"}
    if operation == "questions":
        NotesRepository(entrypoint.factory).create(
            NoteCreate(company="Acme", questions="Explain Python")
        )
        payload = {"source": "notes"}
    result = entrypoint.invoke(operation, payload)
    assert_error(entrypoint, result, "ai_provider_error", 502)
    assert "sk-private-token" not in str(result[1])
    assert "résumé" not in str(result[1])


def test_questions_share_count_deduplication_normalization_and_source(entrypoint):
    NotesRepository(entrypoint.factory).create(
        NoteCreate(
            company="Acme",
            application_id=entrypoint.application.id,
            questions="Explain Python",
        )
    )
    item = {"question": "Explain Python", "difficulty": "困难", "tags": ["python"]}
    entrypoint.model.reply = json.dumps({"questions": [item, item, {"question": ""}]})
    payload = {
        "source": "notes",
        "count": 99,
        "topic": "backend",
        "application_id": entrypoint.application.id,
    }
    assert entrypoint.invoke("questions", payload)[0] == 201
    assert "设计 20 道面试题" in entrypoint.model.messages[0][1].content
    (row,) = QuestionsRepository(entrypoint.factory).list()
    assert row.source_type == "ai_notes"
    assert row.application_id == entrypoint.application.id
    assert row.topic == "backend"
    assert row.difficulty == "hard"
    repeated = entrypoint.invoke("questions", payload)
    assert repeated[0] == 201
    assert len(QuestionsRepository(entrypoint.factory).list()) == 1
    if entrypoint.kind == "api":
        assert repeated[1]["skipped"] == 2
    else:
        assert "skipped duplicates: 2" in repeated[1]["output"]


@pytest.mark.parametrize("operation", ["analyze", "match"])
def test_supplied_empty_text_is_not_replaced_with_saved_version(entrypoint, operation):
    result = entrypoint.invoke(
        operation,
        {
            "application_id": entrypoint.application.id,
            "jd_version_id": entrypoint.version.id,
            "jd_text": "",
        },
    )
    assert_error(entrypoint, result, "application_jd_version_required", 422)
    assert entrypoint.model.messages == []
    if entrypoint.kind == "cli":
        assert "--jd-version" in result[1]["error"]
        assert "without --jd" in result[1]["error"]


def test_legacy_fenced_json_still_works_on_both_ports(entrypoint):
    entrypoint.model.reply = '```json\n{"summary":"ok"}\n```'
    assert entrypoint.invoke("analyze", {"jd_text": "Independent JD"})[0] == 201


def test_api_question_nulls_retain_optional_defaults(entrypoint):
    if entrypoint.kind != "api":
        return
    NotesRepository(entrypoint.factory).create(
        NoteCreate(company="Acme", questions="Explain Python")
    )
    entrypoint.model.reply = '{"questions":[]}'
    result = entrypoint.invoke("questions", {"application_id": None, "count": None})
    assert result[0] == 201
    assert "设计 8 道面试题" in entrypoint.model.messages[0][1].content


@pytest.mark.parametrize("operation", ["analyze", "match", "questions"])
def test_numeric_overflow_is_not_published_as_non_finite_json(entrypoint, operation):
    entrypoint.model.reply = '{"summary":1e999}'
    payload = {"jd_text": "Independent JD"}
    if operation == "questions":
        NotesRepository(entrypoint.factory).create(NoteCreate(company="Acme", questions="Python"))
        payload = {"source": "notes"}
    assert_error(entrypoint, entrypoint.invoke(operation, payload), "ai_invalid_response", 502)
    rows = (
        QuestionsRepository(entrypoint.factory).list()
        if operation == "questions"
        else entrypoint.rows(operation)
    )
    assert rows == []


@pytest.mark.parametrize("operation", ["analyze", "match"])
@pytest.mark.parametrize("field", ["application_id", "jd_version_id"])
def test_out_of_range_identity_is_rejected_without_database_overflow(entrypoint, operation, field):
    payload = {"application_id": entrypoint.application.id, "jd_version_id": entrypoint.version.id}
    payload[field] = 2**63
    expected = (
        "application_id_invalid" if field == "application_id" else "application_jd_version_required"
    )
    assert_error(entrypoint, entrypoint.invoke(operation, payload), expected, 422)
    assert entrypoint.model.messages == []
