"""Explicit opt-in for historical, synthetic enabled-companion scenarios only."""
from contextlib import contextmanager
from uuid import uuid4

import pytest

from scripts.companion_ai_validation import driver


def install_legacy_companion_opt_in(monkeypatch):
    original_api = driver._api

    @contextmanager
    def opted_in_api(data_dir):
        with original_api(data_dir) as client:
            before = driver._request(client, "fixture", "GET", "/api/companion")
            assert before["settings"]["enabled"] is False
            after = driver._request(client, "fixture", "PUT", "/api/companion/settings", {
                "identity": before["identity"], "expected_revision": before["revision"],
                "command_id": str(uuid4()),
                "settings": {**before["settings"], "enabled": True},
            })
            assert after["settings"]["enabled"] is True
            assert after["state"] == before["state"]
            assert after["shared_participation"] == before["shared_participation"]
            yield client

    monkeypatch.setattr(driver, "_api", opted_in_api)


@pytest.fixture
def legacy_companion_opt_in(monkeypatch):
    install_legacy_companion_opt_in(monkeypatch)
