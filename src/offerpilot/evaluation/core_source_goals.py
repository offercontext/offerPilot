"""Source-unit and final-wire prerequisites; answer quality remains human reviewed."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from offerpilot.context_projector.contracts import canonical_json

def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            for text in (
                [item]
                if key in {"content", "text", "excerpt", "preference"} and isinstance(item, str)
                else _strings(item)
            )
        ]
    return []


def _same(identity: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        identity.get(key) is not None and str(identity[key]) == str(value)
        for key, value in expected.items()
    )


def knowledge_provenance(
    rule: str, evidence: dict[str, Any], *, injection: bool
) -> tuple[bool | None, str]:
    facts = evidence.get("facts", {})
    aliases = facts.get("aliases", {})
    names = ("knowledge_note_id", "knowledge_note_version", "new_source_id", "new_source_snapshot")
    if any(aliases.get(key) is None for key in names):
        return None, "expected_source_unit_identity_missing"
    note_identity = dict(note_id=aliases["knowledge_note_id"], version_id=aliases["knowledge_note_version"])
    raw_identity = dict(source_id=aliases["new_source_id"], snapshot_id=aliases["new_source_snapshot"])
    frames = evidence.get("input_observations", [])
    selections = [frame for frame in frames if frame.get("category") == "sources"]
    finals = [frame for frame in frames if frame.get("category") == "final_input"]
    if not selections:
        return None, "source_selection_not_observed"

    def expected_units(frame: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        selected = []
        for source in frame.get("sources", []):
            if source.get("kind") != "knowledge_context":
                continue
            units = json.loads(source["content"])
            if not isinstance(units, list):
                raise ValueError("knowledge source must contain versioned units")
            for unit in units:
                if not isinstance(unit, dict):
                    raise ValueError("invalid knowledge unit")
                if (
                    unit.get("kind") == "confirmed_note" and _same(unit, note_identity)
                    or unit.get("kind") == "evidence" and _same(unit, raw_identity)
                ):
                    selected.append((source, unit))
        return selected

    if not finals:
        return None, "final_serialized_body_not_observed"
    for final in finals:
        preceding = [
            frame for frame in selections
            if frame.get("epoch", 1) == final.get("epoch", 1)
            and frame.get("seq", -1) < final.get("seq", -1)
            and all((final.get("identity") or {}).get(key) is not None
                    and (frame.get("identity") or {}).get(key) == final["identity"][key]
                    for key in ("model_call_id", "segment_id", "surface_fingerprint"))
        ]
        if not preceding:
            return None, "final_input_source_selection_unbound"
        selected = expected_units(max(preceding, key=lambda frame: frame["seq"]))
        if not all(any(unit.get("kind") == kind for _, unit in selected)
                   for kind in ("confirmed_note", "evidence")):
            return False, "expected_note_or_raw_evidence_not_selected"
        if rule == "source.selected":
            continue
        attack_delivered = False
        for source, unit in selected:
            note = unit["kind"] == "confirmed_note"
            identity = note_identity if note else {**raw_identity, "evidence_id": unit.get("evidence_id")}
            strings = _strings(unit.get("content")) if note else _strings(unit)
            if not note and injection:
                attack = facts.get("injection_text")
                if not isinstance(attack, str) or not attack:
                    return None, "original_injection_stimulus_unrecorded"
                attack_strings = [text for text in strings if attack in text]
                if attack_strings:
                    strings = attack_strings
                    attack_delivered = True
            hashes = {hashlib.sha256(text.encode()).hexdigest() for text in strings if text}
            if not hashes:
                return None, "source_unit_content_unobserved"
            matched = any(
                observation.get("kind") == "knowledge_context"
                and observation.get("source_id") == source.get("source_id")
                and observation.get("revision") == source.get("revision")
                and any(
                    _same(span.get("unit_identity") or {}, identity)
                    and span.get("unit_binding") == "complete-json-unit"
                    and span.get("unit_hash") == hashlib.sha256(canonical_json(unit)).hexdigest()
                    and span.get("source_content_hash") in hashes
                    for span in observation.get("ranges", [])
                )
                for observation in final.get("observations", [])
            )
            if not matched:
                if not note and injection:
                    return None, "injection_stimulus_not_delivered"
                return False, "expected_source_unit_not_in_final_input"
        if injection and not attack_delivered:
            return None, "injection_stimulus_not_selected"
    return True, ("both_versioned_source_units_selected" if rule == "source.selected"
                  else "both_source_units_and_required_stimulus_in_final_input")
