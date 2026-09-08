from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from offerpilot.cli import app
from offerpilot.smoke import SmokeReport, SmokeStep, run_interview_story_smoke


def test_verify_interview_stories_cli_is_explicitly_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path))
    observed: dict[str, object] = {}

    def fake_verify(data_dir: Path, static_dir: Path | None, *, real_ai: bool) -> SmokeReport:
        observed.update({"data_dir": data_dir, "static_dir": static_dir, "real_ai": real_ai})
        return SmokeReport(ok=True, steps=[SmokeStep("story", "isolated flow")])

    monkeypatch.setattr("offerpilot.cli.run_interview_story_smoke", fake_verify)
    result = CliRunner().invoke(app, ["verify-interview-stories", "--profile", "local"])

    assert result.exit_code == 0
    assert observed["real_ai"] is False
    assert "Isolated Interview Story API verification" in result.output
    assert "does not replace full verify or browser/CDP evidence" in result.output


def test_local_interview_story_smoke_exercises_ui_and_pilot_without_chat_writes(tmp_path: Path) -> None:
    report = run_interview_story_smoke(tmp_path, real_ai=False)

    names = [step.name for step in report.steps]
    assert report.ok is True
    assert "story_manual_lifecycle" in names
    assert "story_ui_proposal_confirm" in names
    assert "story_pilot_proposal_confirm" in names
    assert "story_source_changed" in names
    assert "story_chat_isolation" in names


@pytest.mark.parametrize(
    ("real_ai", "expected_timeout"),
    [(False, 60.0), (True, 180.0)],
)
def test_full_http_smoke_uses_a_longer_client_timeout_only_for_real_ai(
    monkeypatch, tmp_path: Path, real_ai: bool, expected_timeout: float
) -> None:
    import offerpilot.smoke as smoke

    captured_timeouts: list[float] = []

    class StopAfterClientCreation(RuntimeError):
        pass

    class RecordingClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            assert base_url == "http://smoke.test"
            captured_timeouts.append(timeout)

        def __enter__(self):
            raise StopAfterClientCreation

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(smoke, "_run_unconfigured_chat_smoke", lambda *args: None)
    monkeypatch.setattr(smoke, "create_app", lambda **kwargs: object())
    monkeypatch.setattr(smoke, "_running_server", lambda app: nullcontext("http://smoke.test"))
    monkeypatch.setattr(smoke.httpx, "Client", RecordingClient)

    with pytest.raises(StopAfterClientCreation):
        smoke._run_http_smoke(tmp_path / "data", real_ai=real_ai)

    assert captured_timeouts == [expected_timeout]


def test_real_ai_stage_diagnostic_names_the_failed_operation() -> None:
    import offerpilot.smoke as smoke

    def fail() -> None:
        raise httpx.ReadTimeout("provider response exceeded the client boundary")

    with pytest.raises(
        RuntimeError,
        match=r"real-ai smoke stage interview_preparation failed after \d+ ms: ReadTimeout",
    ) as caught:
        smoke._run_named_real_ai_smoke_stage("interview_preparation", fail)

    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
