import json

from typer.testing import CliRunner

from offerpilot.ai.types import Assistant
from offerpilot.cli import app
from offerpilot.config import AIProviderProfile, Config, load_config, save_config
from offerpilot.db import session_factory_for_data_dir
from offerpilot.repositories.jd import JDAnalysesRepository
from offerpilot.repositories.notes import NoteCreate, NotesRepository
from offerpilot.repositories.questions import QuestionsRepository
from offerpilot.repositories.resumes import ResumeCreate, ResumesRepository


class JSONModel:
    def __init__(self, payloads: list[str]):
        self.payloads = list(payloads)

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(content=self.payloads.pop(0))


def _write_ai_config(data_dir):
    save_config(data_dir, Config(api_key="sk-test"))


def test_config_auto_approve_help_declares_deprecation(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    result = CliRunner().invoke(app, ["config", "--help"])

    assert result.exit_code == 0
    assert "已弃用" in result.output
    assert "without confirmation" not in result.output


def test_add_and_list_application(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    add_result = runner.invoke(app, ["add", "--company", "ByteDance", "--position", "Backend"])
    list_result = runner.invoke(app, ["list"])

    assert add_result.exit_code == 0
    assert "Added: ByteDance" in add_result.output
    assert "Status: applied" in add_result.output
    assert list_result.exit_code == 0
    assert "ByteDance" in list_result.output
    assert "Backend" in list_result.output


def test_list_empty_applications(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "No applications found" in result.output


def test_list_rejects_invalid_application_status(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["list", "--status", "onsite"])

    assert result.exit_code != 0
    assert "invalid application status: onsite" in result.output


def test_config_masks_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["config", "--api-key", "sk-abcdef"])

    assert result.exit_code == 0
    assert "sk-a****ef" in result.output
    assert "ai_auto_approve: false" in result.output


def test_config_updates_active_provider_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    save_config(
        tmp_path,
        Config(
            active_provider_id="default",
            providers=[
                AIProviderProfile(
                    id="default",
                    api_key="sk-old",
                    base_url="https://api.openai.com/v1",
                    model="gpt-4o",
                )
            ],
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["config", "--model", "gpt-4o-mini"])

    assert result.exit_code == 0
    assert load_config(tmp_path).active_provider().model == "gpt-4o-mini"


def test_config_updates_runtime_and_log_options(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["config", "--runtime-mode", "server", "--auth", "--log-level", "debug"],
    )

    cfg = load_config(tmp_path)
    assert result.exit_code == 0
    assert cfg.runtime_mode == "server"
    assert cfg.auth_enabled is True
    assert cfg.log_level == "DEBUG"


def test_config_sets_auth_token(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["config", "--auth-token", "local-secret"])

    assert result.exit_code == 0
    assert "auth_token: loca****et" in result.output
    assert load_config(tmp_path).auth_token == "local-secret"


def test_skill_cli_registers_trusts_and_enables_package(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "skill",
            "add",
            "--id",
            "resume-coach",
            "--label",
            "Resume Coach",
            "--source",
            "file:///skills/resume-coach",
        ],
    )
    enable_before_trust = runner.invoke(app, ["skill", "enable", "resume-coach"])
    trust_result = runner.invoke(app, ["skill", "trust", "resume-coach"])
    enable_result = runner.invoke(app, ["skill", "enable", "resume-coach"])
    list_result = runner.invoke(app, ["skill", "list"])

    assert add_result.exit_code == 0
    assert "registered" in add_result.output
    assert enable_before_trust.exit_code != 0
    assert "trusted before enabling" in enable_before_trust.output
    assert trust_result.exit_code == 0
    assert enable_result.exit_code == 0
    assert "loaded" in list_result.output

    cfg = load_config(tmp_path)
    assert cfg.skills[0].id == "resume-coach"
    assert cfg.skills[0].trusted is True
    assert cfg.skills[0].enabled is True


def test_skill_cli_registers_manifest_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "data"))
    manifest = tmp_path / "skill.json"
    manifest.write_text(
        (
            '{"id":"resume-coach","label":"Resume Coach","version":"0.1.0",'
            '"description":"Resume review assistant","entrypoint":"SKILL.md"}'
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["skill", "add", "--manifest", str(manifest), "--source", "file:///skills/resume-coach"],
    )

    cfg = load_config(tmp_path / "data")
    assert result.exit_code == 0
    assert cfg.skills[0].id == "resume-coach"
    assert cfg.skills[0].description == "Resume review assistant"
    assert cfg.skills[0].entrypoint == "SKILL.md"
    assert len(cfg.skills[0].manifest_digest) == 64


def test_wakeup_cli_add_list_and_dispatch_due(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "wakeup",
            "add",
            "--kind",
            "follow_up",
            "--due-at",
            "2026-07-08T09:30:00Z",
            "--payload-json",
            '{"application_id":7}',
        ],
    )
    list_result = runner.invoke(app, ["wakeup", "list"])
    dispatch_result = runner.invoke(
        app,
        ["wakeup", "dispatch-due", "--now", "2026-07-08T10:00:00Z"],
    )
    repeated = runner.invoke(
        app,
        ["wakeup", "dispatch-due", "--now", "2026-07-08T10:00:00Z"],
    )

    assert add_result.exit_code == 0
    assert "Wakeup scheduled" in add_result.output
    assert list_result.exit_code == 0
    assert "follow_up" in list_result.output
    assert dispatch_result.exit_code == 0
    assert "Dispatched 1 wakeup" in dispatch_result.output
    assert repeated.exit_code == 0
    assert "Dispatched 0 wakeups" in repeated.output


def test_start_uses_configured_port_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    save_config(tmp_path, Config(local_port=9099))
    captured = {}

    def fake_run(app, host, port):  # type: ignore[no-untyped-def]
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("offerpilot.cli.uvicorn.run", fake_run)
    runner = CliRunner()

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert captured == {"host": "127.0.0.1", "port": 9099}


def test_start_accepts_an_explicit_host(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    captured = {}

    def fake_run(app, host, port):  # type: ignore[no-untyped-def]
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("offerpilot.cli.uvicorn.run", fake_run)
    runner = CliRunner()

    result = runner.invoke(app, ["start", "--host", "0.0.0.0", "--port", "8080"])

    assert result.exit_code == 0
    assert captured == {"host": "0.0.0.0", "port": 8080}


def test_resume_add_and_list(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "data"))
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Built Python APIs", encoding="utf-8")
    runner = CliRunner()

    add_result = runner.invoke(app, ["resume", "add", "--file", str(resume_file), "--name", "Backend"])
    list_result = runner.invoke(app, ["resume", "list"])

    assert add_result.exit_code == 0
    assert "Resume saved" in add_result.output
    assert list_result.exit_code == 0
    assert "Backend" in list_result.output


def test_note_add_and_list_backfills_application(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["add", "--company", "ByteDance", "--position", "Backend"])

    add_note = runner.invoke(app, ["note", "add", "--app", "1", "--round", "一面", "--questions", "Go GMP"])
    list_notes = runner.invoke(app, ["note", "list", "--app", "1"])

    assert add_note.exit_code == 0
    assert "Note saved" in add_note.output
    assert list_notes.exit_code == 0
    assert "ByteDance" in list_notes.output
    assert "Go GMP" in list_notes.output


def test_offer_add_update_compare_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "offer",
            "add",
            "--company",
            "Acme",
            "--position",
            "Backend",
            "--base",
            "30000",
            "--months",
            "16",
            "--signing",
            "50000",
        ],
    )
    update_result = runner.invoke(app, ["offer", "update", "1", "--status", "accepted"])
    compare_result = runner.invoke(app, ["offer", "compare", "1"])
    delete_result = runner.invoke(app, ["offer", "delete", "1"])

    assert add_result.exit_code == 0
    assert "Offer added" in add_result.output
    assert update_result.exit_code == 0
    assert "updated" in update_result.output
    assert compare_result.exit_code == 0
    assert "Acme" in compare_result.output
    assert delete_result.exit_code == 0
    assert "deleted" in delete_result.output


def test_question_list_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["question", "list"])

    assert result.exit_code == 0
    assert "No questions yet" in result.output


def test_analyze_jd_cli_persists_result(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    _write_ai_config(tmp_path)
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: JSONModel(['{"summary":"Backend role","requirements":["Python"]}']),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["analyze", "--jd", "Python backend JD"])

    assert result.exit_code == 0
    assert "JD analysis saved" in result.output
    rows = JDAnalysesRepository(session_factory_for_data_dir(tmp_path)).list()
    assert len(rows) == 1
    assert rows[0].jd_text == "Python backend JD"
    assert "Backend role" in rows[0].result


def test_analyze_jd_cli_rejects_url_without_building_ai_model(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: (_ for _ in ()).throw(AssertionError("AI model must not be built")),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["analyze", "--jd-url", "https://jobs.example.test/backend"])

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "code": "jd_text_required",
        "error": "jd_text is required; jd_url is record-only",
    }


def test_analyze_jd_cli_rejects_jd_and_url_without_external_work(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: (_ for _ in ()).throw(AssertionError("AI model must not be built")),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["analyze", "--jd", "Backend JD", "--jd-url", "https://jobs.example.test/backend"],
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "code": "jd_url_not_supported",
        "error": "jd_url is record-only",
    }


def test_resume_match_cli_persists_match(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    _write_ai_config(tmp_path)
    resumes = ResumesRepository(session_factory_for_data_dir(tmp_path))
    resume = resumes.create(ResumeCreate(name="Backend", parsed_data="Python FastAPI"))
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: JSONModel(['{"match_score":88,"summary":"strong fit"}']),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["resume", "match", "--resume", str(resume.id), "--jd", "Python JD"])

    assert result.exit_code == 0
    assert "Resume match saved" in result.output
    matches = resumes.list_matches(resume.id)
    assert len(matches) == 1
    assert "strong fit" in matches[0].result


def test_resume_match_cli_rejects_url_without_building_ai_model(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: (_ for _ in ()).throw(AssertionError("AI model must not be built")),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["resume", "match", "--resume", "999", "--jd-url", "https://jobs.example.test/backend"],
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "code": "jd_text_required",
        "error": "jd_text is required; jd_url is record-only",
    }


def test_question_generate_cli_from_notes(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    _write_ai_config(tmp_path)
    notes_repo = NotesRepository(session_factory_for_data_dir(tmp_path))
    notes_repo.create(
        NoteCreate(
            company="OfferPilot",
            position="Backend",
            round="技术一面",
            date="2026-07-11",
            questions="如何设计缓存失效？请结合 Redis 说一下。",
        )
    )
    monkeypatch.setattr(
        "offerpilot.cli._build_ai_model",
        lambda: JSONModel(
            [
                (
                    '{"questions":[{"category":"系统设计","difficulty":"hard",'
                    '"question":"如何设计缓存失效？","reference_answer":"TTL + 主动失效",'
                    '"tags":["cache"]}]}'
                )
            ]
        ),
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["question", "generate", "--source", "notes", "--topic", "system-design", "--count", "1"],
    )

    assert result.exit_code == 0
    assert "Generated 1 questions" in result.output
    rows = QuestionsRepository(session_factory_for_data_dir(tmp_path)).list(topic="system-design")
    assert len(rows) == 1
    assert rows[0].question == "如何设计缓存失效？"
