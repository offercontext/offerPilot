"""E09 v3 prerequisites: versioned memory and current request on the actual wire."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from offerpilot.context_projector.contracts import canonical_json

FIXTURE = Path(__file__).resolve().parents[3] / "evals/fixtures/synthetic-e09-current-goal-v1.json"
CALL_FIELDS = ("model_call_id", "segment_id", "surface_fingerprint")


def input_provenance(
    rule: str, evidence: dict[str, Any], private_directory: str | None,
) -> tuple[bool | None, str]:
    fixture = json.loads(FIXTURE.read_bytes())
    aliases = evidence.get("facts", {}).get("aliases", {})
    if not aliases.get("memory_id") or type(aliases.get("memory_version")) is not int:
        return None, "expected_memory_identity_missing"
    expected = dict(
        id=str(aliases["memory_id"]), version=aliases["memory_version"],
        preference=fixture["memory"],
    )
    requests = [
        row for row in evidence.get("http", [])
        if row.get("method") == "POST" and row.get("path") == "/api/pilot/runtime/v1/turns"
    ]
    if len(requests) != 1:
        return None, "current_goal_request_binding_incomplete"
    body, response = requests[0].get("body") or {}, requests[0].get("response") or {}
    if body.get("message") != fixture["current_request"]:
        return False, "current_goal_stimulus_changed"
    execution: dict[str, Any] = dict(
        request_id=body.get("request_id"), turn_id=response.get("turn_id"),
        generation=response.get("execution_generation"),
        conversation_id=response.get("conversation_id"),
    )
    if (
        not all(isinstance(execution[k], str) and execution[k] for k in ("request_id", "turn_id"))
        or not all(type(execution[k]) is int and execution[k] > 0
                   for k in ("generation", "conversation_id"))
    ):
        return None, "current_goal_execution_identity_missing"
    frames = evidence.get("input_observations", [])
    finals = [v for v in frames if v.get("category") == "final_input"]
    sources = [v for v in frames if v.get("category") == "sources"]
    if not finals or not sources:
        return None, "current_goal_input_observation_missing"
    for final in finals:
        identity = final.get("identity") or {}
        if identity.get("execution") != execution or not all(
            isinstance(identity.get(k), str) and identity[k] for k in CALL_FIELDS
        ):
            return None, "current_goal_final_call_unbound"
        preceding = [
            v for v in sources
            if v.get("epoch") == final.get("epoch")
            and type(v.get("seq")) is int and type(final.get("seq")) is int
            and 0 < v["seq"] < final["seq"]
            and all((v.get("identity") or {}).get(k) == identity[k] for k in CALL_FIELDS)
        ]
        if not preceding:
            return None, "current_goal_selection_call_unbound"
        selected = [
            s for s in max(preceding, key=lambda v: v["seq"]).get("sources", [])
            if s.get("kind") == "confirmed_memory"
        ]
        matching = []
        for source in selected:
            units = json.loads(source["content"])
            if not isinstance(units, list):
                return None, "current_goal_memory_units_invalid"
            if expected in units:
                matching.append(source)
        if not matching:
            return False, "expected_memory_id_version_or_preference_not_selected"
        if rule == "source.current_goal_selected":
            continue
        name = final.get("request_file")
        if private_directory is None or not isinstance(name, str) or Path(name).name != name:
            return None, "current_goal_serialized_request_unavailable"
        private = Path(private_directory)
        path = private / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(private.resolve()):
            return None, "current_goal_serialized_request_unavailable"
        raw = path.read_bytes()
        actual = sha256(raw).hexdigest()
        if (
            actual != final.get("payload_hash")
            or actual != evidence.get("files", {}).get(name, {}).get("sha256")
        ):
            return None, "current_goal_serialized_request_unbound"
        messages = json.loads(raw).get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            return None, "current_goal_serialized_messages_invalid"
        if not any(
            m.get("role") == "user" and isinstance(m.get("content"), str)
            and fixture["current_request"] in m["content"] for m in messages
        ):
            return False, "current_goal_request_not_in_final_input"
        delivered = False
        for observation in final.get("observations", []):
            if not any(
                observation.get("kind") == "confirmed_memory"
                and observation.get("source_id") == s.get("source_id")
                and observation.get("revision") == s.get("revision") for s in matching
            ):
                continue
            field = re.fullmatch(r"messages\[(\d+)\]\.content", observation.get("field_path", ""))
            if field is None or int(field[1]) >= len(messages):
                continue
            message = messages[int(field[1])]
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, str):
                continue
            # Match a complete unit, never a matching sentence in chat history.
            serialized = {
                json.dumps(expected, ensure_ascii=ascii_only, sort_keys=compact,
                           separators=(",", ":") if compact else None)
                for ascii_only in (True, False) for compact in (True, False)
            }
            if not any(unit in content for unit in serialized):
                continue
            encoded = content.encode()
            for span in observation.get("ranges", []):
                start, end = span.get("start"), span.get("end")
                if (
                    span.get("unit_identity") == {"id": expected["id"], "version": expected["version"]}
                    and span.get("unit_binding") == "complete-json-unit"
                    and span.get("unit_hash") == sha256(canonical_json(expected)).hexdigest()
                    and span.get("source_content_hash") == sha256(fixture["memory"].encode()).hexdigest()
                    and type(start) is int and type(end) is int and 0 <= start < end <= len(encoded)
                    and sha256(encoded[start:end]).hexdigest() == span.get("content_hash")
                ):
                    delivered = True
        if not delivered:
            return False, "expected_versioned_memory_not_in_final_input"
    return True, (
        "expected_memory_selected_for_current_call" if rule == "source.current_goal_selected"
        else "current_request_and_expected_memory_bound_to_actual_wire"
    )
