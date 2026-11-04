"""Current-head retest setup, with no provider configuration or model calls."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from offerpilot.api import _chat_response_system_message
from offerpilot.companion.api import register_companion_routes
from offerpilot.db import session_factory_for_data_dir
from scripts.companion_ai_validation import driver as base
from scripts.companion_ai_validation import experience_driver as experience


@pytest.fixture
def fixture_matrix(tmp_path):
    sessions = session_factory_for_data_dir(tmp_path)
    app = FastAPI()
    register_companion_routes(app, sessions)

    def no_provider(_operation):
        pytest.fail("fixture setup must never open a provider stage")

    with TestClient(app) as client:
        matrix = experience._Experience(tmp_path, client, no_provider, (), None)
        try:
            yield matrix
        finally:
            matrix.sessions.kw["bind"].dispose()
            sessions.kw["bind"].dispose()


def test_retest_explicit_http_opt_in_preserves_state_and_history(fixture_matrix):
    matrix = fixture_matrix
    before = matrix.view()
    matrix.opt_in_retest()
    after = matrix.view()
    assert after["settings"]["enabled"] is True
    assert after["state"] == before["state"]
    assert after["recent_events"] == before["recent_events"]
    assert matrix.responses == matrix.controls == []
    assert matrix.checks == {
        "fresh_default_closed": True,
        "default_closed_context_absent": True,
        "explicit_http_opt_in": True,
    }
    matrix.event("app_open")
    assert matrix.view()["state"]["activity"] is not None
    assert matrix.context("你的小日常怎么样了？", 0) is not None


def test_retest_rejects_already_enabled_fixture_before_changing_it(fixture_matrix):
    matrix = fixture_matrix
    matrix.settings(enabled=True)
    before = matrix.snapshot(matrix.view())
    with pytest.raises(base.MatrixFailure, match="fresh_default_closed"):
        matrix.opt_in_retest()
    assert matrix.snapshot(matrix.view()) == before
    assert matrix.responses == matrix.controls == []


def test_default_closed_and_disabled_again_suppress_context_and_persona(fixture_matrix):
    """Offline source/policy comparison, not a paid off/on semantic trial."""
    matrix = fixture_matrix
    initial = matrix.snapshot(matrix.view())
    assert matrix.event("app_open")["changed"] is False
    assert matrix.snapshot(matrix.view()) == initial
    assert matrix.context("你的小日常怎么样了？", 0) is None
    matrix.opt_in_retest()
    matrix.event("app_open")
    state = matrix.view()["state"]
    assert matrix.context("你的小日常怎么样了？", 0) is not None
    matrix.settings(enabled=False)
    assert matrix.view()["state"] == state
    assert matrix.context("你的小日常怎么样了？", 0) is None
    plain = _chat_response_system_message(general=True, companion_enabled=False)
    custom_off = _chat_response_system_message(
        general=True, custom_persona=True, companion_enabled=False,
    )
    enabled = _chat_response_system_message(general=True, companion_enabled=True)
    assert plain.surface_revision == custom_off.surface_revision == "pilot-plain"
    assert plain.content == custom_off.content
    assert enabled.surface_revision == "companion-expression"
    assert enabled.content != plain.content


@pytest.mark.parametrize("retest", [False, True])
def test_opt_in_is_scoped_to_retest_not_legacy_experience(tmp_path, monkeypatch, retest):
    opted_in = []

    @contextmanager
    def fake_api(_data_dir):
        yield None

    class Matrix:
        def __init__(self, data_dir, _client, _stage, rows, _record):
            self.data_dir, self.rows = data_dir, rows
            self.sessions = SimpleNamespace(kw={"bind": SimpleNamespace(dispose=lambda: None)})

        def opt_in_retest(self):
            opted_in.append(self.data_dir.name)

        def run_group(self, group):
            return {"group": group, "responses": [
                {"operation": row["id"], "expected_context": None,
                 "provenance": {"fixture_reference_not_projected": None}}
                for row in self.rows
            ]}

    monkeypatch.setattr(base, "_api", fake_api)
    monkeypatch.setattr(experience, "_Experience", Matrix)
    report = experience.run_matrix(tmp_path, lambda _: None, retest=retest)
    assert opted_in == (["a", "d", "e"] if retest else [])
    assert report["ordinary_count"] == (5 if retest else 12)
