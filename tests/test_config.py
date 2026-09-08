import json
from pathlib import Path

import pytest

from offerpilot.config import (
    AIProviderProfile,
    Config,
    load_config,
    resolve_data_dir,
    save_config,
)


def test_resolve_data_dir_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFERPILOT_DATA", str(tmp_path / "custom"))

    assert resolve_data_dir() == tmp_path / "custom"


def test_resolve_data_dir_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("OFFERPILOT_DATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert resolve_data_dir() == tmp_path / ".offerpilot"


def test_load_missing_config_returns_defaults(tmp_path):
    cfg = load_config(tmp_path)

    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o"
    assert cfg.local_port == 8080
    assert cfg.chat_auto_approve_writes is False
    assert cfg.runtime_mode == "local"
    assert cfg.auth_enabled is False
    assert cfg.log_level == "INFO"


def test_confirmation_secret_survives_config_reload(tmp_path):
    first = load_config(tmp_path)
    second = load_config(tmp_path)

    assert first.confirmation_secret
    assert second.confirmation_secret == first.confirmation_secret


def test_load_config_disables_legacy_agent_auto_approval(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"chat_auto_approve_writes": True}),
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)

    assert cfg.chat_auto_approve_writes is False


@pytest.mark.parametrize(
    ("raw_capability", "expected"),
    [(True, True), ("true", False), ("1", False), (1, False), (None, False)],
)
def test_load_config_only_accepts_json_boolean_for_schema_capability(
    tmp_path, raw_capability, expected
):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "active_provider_id": "provider",
                "providers": [
                    {
                        "id": "provider",
                        "label": "Provider",
                        "provider": "openai",
                        "supports_json_schema": raw_capability,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_config(tmp_path).active_provider().supports_json_schema is expected


def test_save_and_load_config_round_trip(tmp_path):
    cfg = Config(
        api_key="sk-test",
        base_url="https://example.test/v1",
        model="model",
        local_port=9999,
        chat_auto_approve_writes=True,
        runtime_mode="server",
        auth_enabled=True,
        log_level="DEBUG",
        confirmation_secret="test-confirmation-secret",
    )

    save_config(tmp_path, cfg)
    loaded = load_config(tmp_path)

    assert loaded == cfg
    assert (tmp_path / "config.json").exists()


def test_loaded_ordered_fallback_is_available_to_knowledge_brief(tmp_path):
    providers = [
        AIProviderProfile(id="primary", label="Primary", api_key="sk-primary"),
        AIProviderProfile(id="backup", label="Backup", api_key="sk-backup"),
    ]
    save_config(
        tmp_path,
        Config(
            active_provider_id="primary",
            fallback_provider_ids=["backup"],
            providers=providers,
        ),
    )

    loaded = load_config(tmp_path)

    assert loaded.fallback_provider() == providers[1]
