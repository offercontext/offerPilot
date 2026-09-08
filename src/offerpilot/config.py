import json
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_PORT = 8080
DEFAULT_PROVIDER_ID = "default"
RuntimeMode = Literal["local", "server"]


class AIProviderProfile(BaseModel):
    id: str = DEFAULT_PROVIDER_ID
    label: str = "Default"
    provider: str = "openai"
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    enabled: bool = True
    context_window: int = 0
    max_output_tokens: int = 0
    supports_vision: bool = False
    supports_json_schema: bool = False

    @field_validator("supports_json_schema", mode="before")
    @classmethod
    def normalize_json_schema_capability(cls, value: Any) -> bool:
        return value if type(value) is bool else False


class SkillPackage(BaseModel):
    id: str
    label: str = ""
    version: str = ""
    description: str = ""
    source: str = ""
    source_type: str = ""
    entrypoint: str = ""
    manifest_digest: str = ""
    trusted: bool = False
    enabled: bool = False


class Config(BaseModel):
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    local_port: int = DEFAULT_PORT
    chat_auto_approve_writes: bool = False
    onboarding_force_open: bool = False
    active_provider_id: str = DEFAULT_PROVIDER_ID
    fallback_provider_id: str = ""
    multimodal_provider_id: str = ""
    providers: list[AIProviderProfile] = Field(default_factory=list)
    fallback_provider_ids: list[str] = Field(default_factory=list)
    runtime_mode: RuntimeMode = "local"
    auth_enabled: bool = False
    auth_token: str = ""
    log_level: str = "INFO"
    skills: list[SkillPackage] = Field(default_factory=list)
    confirmation_secret: str = ""

    @field_validator("chat_auto_approve_writes", mode="before")
    @classmethod
    def disable_agent_auto_approval(cls, value: Any) -> bool:
        return False

    def provider_profiles(self) -> list[AIProviderProfile]:
        if self.providers:
            return self.providers
        return [self.legacy_provider_profile()]

    def legacy_provider_profile(self) -> AIProviderProfile:
        return AIProviderProfile(
            id=DEFAULT_PROVIDER_ID,
            label="Default",
            provider=_infer_provider(self.base_url),
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            enabled=True,
        )

    def active_provider(self) -> AIProviderProfile:
        profiles = self.provider_profiles()
        for profile in profiles:
            if profile.id == self.active_provider_id:
                return profile
        return profiles[0]

    def provider_by_id(self, provider_id: str) -> AIProviderProfile | None:
        for profile in self.provider_profiles():
            if profile.id == provider_id:
                return profile
        return None

    def fallback_provider(self) -> AIProviderProfile | None:
        provider_id = self.fallback_provider_id or (
            self.fallback_provider_ids[0] if self.fallback_provider_ids else ""
        )
        if not provider_id:
            return None
        fallback = self.provider_by_id(provider_id)
        if fallback is None or fallback.id == self.active_provider().id:
            return None
        return fallback

    def ordered_provider_profiles(self) -> list[AIProviderProfile]:
        active = self.active_provider()
        profiles = [active]
        seen = {active.id}
        for provider_id in self.fallback_provider_ids:
            fallback = self.provider_by_id(provider_id)
            if fallback is None or fallback.id in seen:
                continue
            profiles.append(fallback)
            seen.add(fallback.id)
        return profiles

    def multimodal_provider(self) -> AIProviderProfile | None:
        if not self.multimodal_provider_id:
            return None
        return self.provider_by_id(self.multimodal_provider_id)


def resolve_data_dir() -> Path:
    configured = os.environ.get("OFFERPILOT_DATA")
    if configured:
        return Path(configured)
    return Path.home() / ".offerpilot"


def load_config(data_dir: Path) -> Config:
    path = data_dir / "config.json"
    if not path.exists():
        cfg = Config(confirmation_secret=secrets.token_urlsafe(32))
        save_config(data_dir, cfg)
        return cfg

    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    legacy_fallback = str(raw.pop("fallback_provider_id", "") or "")
    if not isinstance(raw.get("fallback_provider_ids"), list):
        raw["fallback_provider_ids"] = [legacy_fallback] if legacy_fallback else []
    cfg = Config.model_validate(raw)
    if not cfg.base_url:
        cfg.base_url = DEFAULT_BASE_URL
    if not cfg.model:
        cfg.model = DEFAULT_MODEL
    if cfg.local_port == 0:
        cfg.local_port = DEFAULT_PORT
    cfg.runtime_mode = normalize_runtime_mode(cfg.runtime_mode)
    cfg.log_level = _normalize_log_level(cfg.log_level)
    if not cfg.confirmation_secret:
        cfg.confirmation_secret = secrets.token_urlsafe(32)
        save_config(data_dir, cfg)
    return cfg


def save_config(data_dir: Path, config: Config) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "config.json"
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _infer_provider(base_url: str) -> str:
    lowered = base_url.lower()
    if "anthropic" in lowered:
        return "anthropic"
    if "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered:
        return "openai_compatible"
    return "openai"


def _normalize_log_level(value: str) -> str:
    normalized = (value or "INFO").upper()
    return normalized if normalized in {"DEBUG", "INFO", "WARNING", "ERROR"} else "INFO"


def normalize_runtime_mode(value: str, fallback: RuntimeMode = "local") -> RuntimeMode:
    if value == "local":
        return "local"
    if value == "server":
        return "server"
    return fallback
