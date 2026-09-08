from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from offerpilot.config import Config, SkillPackage


class SkillRegistryError(ValueError):
    pass


def skills_payload(cfg: Config) -> dict[str, Any]:
    loaded = loaded_skill_ids(cfg)
    return {
        "packages": [_skill_payload(skill, loaded) for skill in cfg.skills],
        "loaded": loaded,
    }


def loaded_skill_ids(cfg: Config) -> list[str]:
    return [skill.id for skill in cfg.skills if skill.trusted and skill.enabled]


def register_skill(cfg: Config, payload: dict[str, Any]) -> Config:
    manifest = _manifest_from_payload(payload)
    skill_id = _required_text(manifest, "id", "manifest.id")
    requested_id = str(payload.get("id") or skill_id).strip()
    if requested_id and requested_id != skill_id:
        raise SkillRegistryError("manifest id must match id")
    current = _skill_by_id(cfg, skill_id)
    trusted = bool(payload.get("trusted", current.trusted if current is not None else False))
    enabled = bool(payload.get("enabled", current.enabled if current is not None else False))
    if enabled and not trusted:
        raise SkillRegistryError("skill must be trusted before enabling")
    source = str(payload.get("source") or (current.source if current is not None else ""))
    next_skill = SkillPackage(
        id=skill_id,
        label=str(manifest.get("label") or (current.label if current is not None else skill_id)),
        version=str(manifest.get("version") or (current.version if current is not None else "")),
        description=str(manifest.get("description") or (current.description if current is not None else "")),
        source=source,
        source_type=_source_type(source),
        entrypoint=str(manifest.get("entrypoint") or (current.entrypoint if current is not None else "")),
        manifest_digest=_manifest_digest(payload.get("manifest")),
        trusted=trusted,
        enabled=enabled,
    )
    skills = [next_skill if skill.id == skill_id else skill for skill in cfg.skills]
    if current is None:
        skills.append(next_skill)
    return cfg.model_copy(update={"skills": skills})


def update_skill(cfg: Config, skill_id: str, payload: dict[str, Any]) -> Config:
    current = _skill_by_id(cfg, skill_id)
    if current is None:
        raise KeyError(skill_id)
    trusted = bool(payload.get("trusted", current.trusted))
    enabled = bool(payload.get("enabled", current.enabled))
    if enabled and not trusted:
        raise SkillRegistryError("skill must be trusted before enabling")
    next_skill = current.model_copy(update={"trusted": trusted, "enabled": enabled})
    return cfg.model_copy(
        update={"skills": [next_skill if skill.id == skill_id else skill for skill in cfg.skills]}
    )


def _skill_payload(skill: SkillPackage, loaded: list[str]) -> dict[str, Any]:
    return {
        "id": skill.id,
        "label": skill.label,
        "version": skill.version,
        "description": skill.description,
        "source": skill.source,
        "source_type": skill.source_type or _source_type(skill.source),
        "entrypoint": skill.entrypoint,
        "manifest_digest": skill.manifest_digest,
        "trusted": skill.trusted,
        "enabled": skill.enabled,
        "loaded": skill.id in loaded,
    }


def _skill_by_id(cfg: Config, skill_id: str) -> SkillPackage | None:
    for skill in cfg.skills:
        if skill.id == skill_id:
            return skill
    return None


def _manifest_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_manifest = payload.get("manifest")
    if isinstance(raw_manifest, dict):
        return raw_manifest
    return payload


def _manifest_digest(raw_manifest: Any) -> str:
    if not isinstance(raw_manifest, dict):
        return ""
    canonical = json.dumps(raw_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _source_type(source: str) -> str:
    if source.startswith("file://") or (source and "://" not in source):
        return "local"
    if source.startswith("http://") or source.startswith("https://"):
        return "remote"
    return ""


def _required_text(payload: dict[str, Any], key: str, label: str | None = None) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise SkillRegistryError(f"{label or key} is required")
    return value
