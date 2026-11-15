"""Bounded expression data, not executable instructions or user facts."""
from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

PERSONA_POLICY_VERSION = "companion-persona-core-v1"
MAX_PERSONA_CHARACTERS = 2000
MAX_PERSONA_BYTES = 6000
ActivityTemplate = Literal["paper_boat", "cloud_catalog", "paper_stars"]
ACTIVITY_TEMPLATE_IDS: tuple[ActivityTemplate, ...] = ("paper_boat", "cloud_catalog", "paper_stars")
ShortTrait = Annotated[str, Field(strict=True, min_length=1, max_length=40)]


class PersonaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str = Field(default="Haru", min_length=1, max_length=24)
    traits: list[ShortTrait] = Field(default_factory=lambda: ["温和", "自然", "有玩心"], max_length=5)
    speaking_style: str = Field(default="自然亲切，偶尔开轻轻的玩笑；不刻意装幼、不逞强，也不用每次都卖萌。", max_length=200)
    verbosity: Literal["brief", "balanced", "detailed"] = "balanced"
    interests_text: str = Field(default="手工和小点子；兴趣只因喜欢而存在。", max_length=300)
    fictional_background: str = Field(default="", max_length=500)
    additional_style_notes: str = Field(default="日常里是被好好照顾着、无忧无虑、自然有点呆萌爱玩的伙伴；工作时认真可靠。", max_length=500)
    enabled_activity_templates: list[ActivityTemplate] = Field(
        default_factory=lambda: list(ACTIVITY_TEMPLATE_IDS), max_length=3,
    )

    @field_validator("display_name", "speaking_style", "interests_text",
                     "fictional_background", "additional_style_notes", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            cls._check_controls(value)
            return value.strip()
        return value

    @field_validator("traits", mode="before")
    @classmethod
    def normalize_traits(cls, value: object) -> object:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cls._check_controls(item)
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @staticmethod
    def _check_controls(value: str) -> None:
        # Newlines/tabs are useful in descriptions; invisible bidi/control
        # characters and surrogate code points are not displayable persona data.
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"}
               and char not in "\n\t" for char in value):
            raise ValueError("persona_control_character")

    @model_validator(mode="after")
    def bounded_content(self) -> PersonaSpec:
        if any(char in "\n\t" for char in self.display_name):
            raise ValueError("persona_name_must_be_single_line")
        if any(any(char in "\n\t" for char in trait) for trait in self.traits):
            raise ValueError("persona_traits_must_be_single_line")
        if len(set(self.enabled_activity_templates)) != len(self.enabled_activity_templates):
            raise ValueError("persona_duplicate_activity_template")
        if len(set(self.traits)) != len(self.traits):
            raise ValueError("persona_duplicate_trait")
        encoded = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        if len(encoded) > MAX_PERSONA_CHARACTERS or len(encoded.encode("utf-8")) > MAX_PERSONA_BYTES:
            raise ValueError("persona_total_size_exceeded")
        return self


class PersonaCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: UUID
    expected_revision: StrictInt = Field(ge=0)
    command_id: UUID
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("persona_confirmation_required")
        return value


class PersonaUpdateCommand(PersonaCommand):
    operation: Literal["update"]
    spec: PersonaSpec


class PersonaRestoreCommand(PersonaCommand):
    operation: Literal["restore"]
    target_revision: StrictInt = Field(ge=0)


PersonaMutation = Annotated[
    PersonaUpdateCommand | PersonaRestoreCommand, Field(discriminator="operation")
]


def persona_presets() -> list[dict[str, object]]:
    official = PersonaSpec()
    gentle = official.model_copy(update={"traits": ["温和", "耐心"], "verbosity": "brief",
                                        "speaking_style": "温和简洁，少用追问，尊重停顿。",
                                        "additional_style_notes": "温和接住日常话题，不强行追问；工作时认真可靠。"})
    concise = official.model_copy(update={"traits": ["冷静", "直接"], "verbosity": "brief",
                                         "speaking_style": "措辞清楚直接，不额外渲染情绪。",
                                         "additional_style_notes": "日常冷静简洁；工作时认真可靠。"})
    return [{"id": key, "spec": spec.model_dump()} for key, spec in
            (("official", official), ("gentle", gentle), ("concise", concise))]
