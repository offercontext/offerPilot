"""Present sealed process observations without turning them into functional verdicts."""

from __future__ import annotations

import json
from typing import Any

from offerpilot.evaluation.registry import digest

IDENTITY = ("model_call_id", "segment_id", "surface_fingerprint")


def _same(frame: dict[str, Any], binding: dict[str, Any]) -> bool:
    identity, expected = frame.get("identity"), binding.get("identity")
    return (
        _positive(binding.get("epoch")) is not None
        and _positive(frame.get("epoch")) is not None
        and frame.get("epoch") == binding["epoch"]
        and isinstance(identity, dict)
        and isinstance(expected, dict)
        and all(_text(expected.get(key)) and identity.get(key) == expected[key] for key in IDENTITY)
    )


def _fields(value: dict[str, Any], names: str) -> dict[str, Any]:
    return {key: value[key] for key in names.split() if key in value}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _source_key(source: dict[str, Any]) -> tuple[str, str, str] | None:
    if not all(_text(source.get(k)) for k in ("kind", "source_id", "revision")):
        return None
    return source["kind"], source["source_id"], source["revision"]


def _project(values: Any, names: str) -> list[dict[str, Any]] | None:
    return [_fields(v, names) for v in values] if isinstance(values, list) else None


def _presence(value: Any) -> bool | None:
    return bool(value) if isinstance(value, (bool, list, dict)) else None


def review_evidence(
    evidence: dict[str, Any], bindings: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    frames = evidence.get("input_observations", [])
    selections = [f for f in frames if f.get("category") == "sources"]
    provenance, audit_bindings = [], []
    for binding in bindings:
        preceding = [
            f
            for f in selections
            if _same(f, binding)
            and _positive(f.get("seq")) is not None
            and _positive(binding.get("seq")) is not None
            and f["seq"] < binding["seq"]
        ]
        latest = max((f["seq"] for f in preceding), default=None)
        matched = [f for f in preceding if f["seq"] == latest]
        selected = matched[0] if len(matched) == 1 else None
        raw_sources = selected.get("sources") if selected is not None else None
        status = (
            ("bound" if isinstance(raw_sources, list) else "not_captured")
            if selected is not None
            else ("unbound" if selections else "not_captured")
        )
        sources = (
            None
            if not isinstance(raw_sources, list)
            else [
                {**_fields(s, "kind source_id revision"), "content": json.loads(s["content"])}
                for s in raw_sources
            ]
        )
        source_ids = {_source_key(s) for s in sources or [] if _source_key(s) is not None}
        finals = [
            f
            for f in frames
            if f.get("category") == "final_input"
            and _same(f, binding)
            and _positive(binding.get("seq")) is not None
            and _positive(f.get("seq")) is not None
            and f.get("seq") == binding["seq"]
        ]
        observations = finals[0].get("observations") if len(finals) == 1 else None
        wire = (
            None
            if not isinstance(observations, list)
            else [
                {
                    **_fields(o, "kind source_id revision field_path"),
                    "ranges": _project(
                        o.get("ranges"),
                        "start end coordinate_space wire_encoding content_hash "
                        "source_content_hash source_field_path unit_binding unit_hash unit_identity",
                    ),
                    "matches_selection": None
                    if sources is None or _source_key(o) is None
                    else _source_key(o) in source_ids,
                }
                for o in observations
            ]
        )
        provenance.append(
            dict(
                call_number=binding["number"],
                selection_status=status,
                selected_sources=sources,
                selection_position=None if selected is None else _fields(selected, "epoch seq"),
                contributors=None
                if selected is None
                else _project(selected.get("contributors"), "name status"),
                final_input_sources=wire,
            )
        )
        audit_bindings.append(
            dict(
                call_number=binding["number"],
                status=status,
                identity=None if selected is None else selected.get("identity"),
                position=None if selected is None else _fields(selected, "epoch seq"),
                selection_hash=None if selected is None else digest(selected),
                final_observation_hash=digest(finals[0]) if len(finals) == 1 else None,
            )
        )

    facts = evidence.get("facts", {})
    observed = evidence.get("observed", {})
    database = None
    coverage = observed.get("coverage")
    observer_active = isinstance(coverage, dict) and _text(coverage.get("version"))
    if isinstance(coverage, dict):
        database = dict(
            coverage=_fields(
                coverage,
                "version profile epoch epochs gaps schema_hash trigger_hash tables",
            ),
            high_water=_positive(observed.get("high_water")) if observer_active else None,
            markers=_project(
                observed.get("markers"),
                "epoch seq name method path request_hash memory_id terminal_received",
            ),
            deltas=_project(
                observed.get("deltas"),
                "epoch seq evidence_id table operation old_key new_key old_json new_json",
            ),
        )
    calls = []
    recorded_calls = evidence.get("calls")
    for event in recorded_calls if isinstance(recorded_calls, list) else []:
        matched_numbers = [
            b["number"]
            for b in bindings
            if _same(dict(epoch=event.get("epoch"), identity=event), b)
        ]
        calls.append(
            {
                **_fields(event, "purpose epoch seq blocked actor name tool_name"),
                "observer_high_water": _positive(event.get("observer_high_water"))
                if observer_active
                else None,
                "model_input_number": matched_numbers[0] if len(matched_numbers) == 1 else None,
            }
        )
    controls = []
    recorded_controls = facts.get("control_writes")
    for receipt in recorded_controls if isinstance(recorded_controls, list) else []:
        controls.append(
            {
                **_fields(
                    receipt,
                    "actor action memory_id expected_version mutation_id start end wire_hash",
                ),
                "result": _fields(receipt["result"], "id state current_version")
                if isinstance(receipt.get("result"), dict)
                else None,
            }
        )
    execution = dict(
        status="captured" if observer_active else "not_captured",
        scope="Captured observations only; missing evidence stays unknown. No automatic functional verdict.",
        coordinates=dict(
            journal="Input/source/call seq and withdrawal_journal_seq share the runtime journal.",
            database_observer="Database markers/deltas, control start/end and observer_high_water share the DB observer; these seq values are not journal seq.",
        ),
        calls=calls if isinstance(recorded_calls, list) else None,
        database_observer=database,
        controls=dict(
            withdrawal_journal_seq=_positive(facts.get("withdraw_control_seq")),
            receipts=controls if isinstance(recorded_controls, list) else None,
        ),
        signals=dict(
            write_intent_present=_presence(facts.get("write_intent")),
            pending_present=_presence(facts.get("pending")),
            observation_closed=facts.get("control_closed")
            if type(facts.get("control_closed")) is bool
            else None,
            terminal_state=facts.get("terminal_state"),
        ),
    )
    audit = dict(
        source_selection_bindings=audit_bindings,
        process_fragment_hashes={
            key: digest(evidence.get(key))
            for key in ("calls", "observed", "facts", "input_observations")
        },
    )
    return provenance, execution, audit
