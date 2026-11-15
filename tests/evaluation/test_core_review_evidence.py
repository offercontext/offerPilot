from __future__ import annotations

import json

import pytest

from offerpilot.evaluation.core_review_evidence import review_evidence


def call(number, seq):
    return dict(
        number=number,
        epoch=1,
        seq=seq,
        identity=dict(
            model_call_id=f"private-model-{number}",
            segment_id=f"private-segment-{number}",
            surface_fingerprint=f"private-fingerprint-{number}",
        ),
    )


def selection(binding, sources, seq=5):
    return dict(
        category="sources",
        epoch=binding["epoch"],
        seq=seq,
        identity=binding["identity"].copy(),
        sources=sources,
        contributors=[],
    )


def memory():
    return dict(
        kind="confirmed_memory",
        source_id="memory-v1",
        revision="r1",
        content=json.dumps([dict(id="M1", version=1, preference="五段长文")]),
    )


def test_call_bound_sources_do_not_carry_old_memory_into_post_withdraw_call():
    first, after = call(1, 10), call(2, 30)
    evidence = dict(input_observations=[selection(first, [memory()]), selection(after, [], 25)])
    public, execution, audit = review_evidence(evidence, [first, after])
    assert [p["selection_status"] for p in public] == ["bound", "bound"]
    assert public[0]["selected_sources"][0]["content"][0]["version"] == 1
    assert public[1]["selected_sources"] == []
    assert [p["call_number"] for p in public] == [1, 2]
    assert "private-model" not in json.dumps([public, execution])
    assert audit["source_selection_bindings"][1]["identity"] == after["identity"]


@pytest.mark.parametrize("damage", ["identity", "missing", "epoch", "late", "ambiguous"])
def test_unrelated_selection_cannot_prove_sources_for_this_call(damage):
    binding = call(1, 10)
    frame = selection(binding, [memory()])
    if damage == "identity":
        frame["identity"]["model_call_id"] = "different"
    elif damage == "missing":
        frame["identity"].pop("surface_fingerprint")
    elif damage == "epoch":
        frame["epoch"] = 2
    elif damage == "late":
        frame["seq"] = 11
    frames = [frame, frame.copy()] if damage == "ambiguous" else [frame]
    public, _, _ = review_evidence(dict(input_observations=frames), [binding])
    assert public[0]["selection_status"] == "unbound"
    assert public[0]["selected_sources"] is None


def test_wire_source_revision_must_match_selected_revision():
    binding = call(1, 10)
    wire = dict(
        category="final_input",
        epoch=1,
        seq=10,
        identity=binding["identity"],
        observations=[
            dict(
                kind="confirmed_memory",
                source_id="memory-v1",
                revision="r2",
                field_path="messages[2].content",
                ranges=[],
            )
        ],
    )
    evidence = dict(input_observations=[selection(binding, [memory()]), wire])
    public, _, _ = review_evidence(evidence, [binding])
    assert public[0]["final_input_sources"][0]["matches_selection"] is False


def test_missing_process_capture_does_not_become_zero_write_claim():
    _, execution, _ = review_evidence({}, [call(1, 10)])
    assert execution["status"] == "not_captured"
    assert execution["signals"]["write_intent_present"] is None
    assert execution["signals"]["pending_present"] is None
    assert execution["database_observer"] is None


def test_execution_exports_control_and_observer_coordinates_without_private_call_identity():
    binding = call(1, 10)
    evidence = dict(
        input_observations=[],
        calls=[
            dict(
                purpose="provider_send",
                epoch=1,
                seq=8,
                observer_high_water=2,
                **binding["identity"],
                provider_attempt_id="private-provider",
            )
        ],
        facts=dict(
            write_intent=[],
            pending=False,
            control_closed=True,
            withdraw_control_seq=7,
            control_writes=[
                dict(
                    actor="fixture_control",
                    action="withdraw",
                    start=3,
                    end=7,
                    memory_id="M1",
                    expected_version=1,
                    mutation_id="mutation-2",
                    wire_hash="hash",
                    result=dict(id="M1", current_version=2, state="withdrawn", secret="hidden"),
                )
            ],
        ),
        observed=dict(
            coverage=dict(version="v1", tables={"confirmed_memories": ["id"]}, gaps=[]),
            high_water=9,
            markers=[
                dict(
                    epoch=1,
                    seq=7,
                    name="fixture_control_completed",
                    memory_id="M1",
                    token="hidden-token",
                )
            ],
            deltas=[
                dict(
                    epoch=1,
                    seq=5,
                    table="confirmed_memories",
                    operation="UPDATE",
                    old_json=dict(current_version=1),
                    new_json=dict(current_version=2),
                )
            ],
        ),
    )
    _, execution, audit = review_evidence(evidence, [binding])
    assert execution["status"] == "captured"
    assert execution["calls"][0]["model_input_number"] == 1
    assert execution["calls"][0]["observer_high_water"] == 2
    assert execution["controls"]["receipts"][0]["start"] == 3
    assert execution["database_observer"]["deltas"][0]["seq"] == 5
    assert not any(s in json.dumps(execution) for s in ["private-", "hidden-token", "secret"])
    assert set(audit["process_fragment_hashes"]) == {
        "calls",
        "observed",
        "facts",
        "input_observations",
    }


@pytest.mark.parametrize("damage", ["no_epoch", "null_epoch", "empty_id", "blank_id"])
def test_partial_call_identity_is_not_a_binding(damage):
    binding = call(1, 10)
    frame = selection(binding, [memory()])
    if damage == "no_epoch":
        binding.pop("epoch")
        frame.pop("epoch")
    elif damage == "null_epoch":
        binding["epoch"] = frame["epoch"] = None
    else:
        value = "" if damage == "empty_id" else " "
        binding["identity"]["segment_id"] = frame["identity"]["segment_id"] = value
    public, _, _ = review_evidence(dict(input_observations=[frame]), [binding])
    assert public[0]["selection_status"] == "unbound"
    assert public[0]["selected_sources"] is None


@pytest.mark.parametrize("value", [None, "absent"])
def test_missing_selected_sources_are_unknown_not_captured_empty(value):
    binding = call(1, 10)
    frame = selection(binding, [])
    if value == "absent":
        frame.pop("sources")
    else:
        frame["sources"] = value
    public, _, _ = review_evidence(dict(input_observations=[frame]), [binding])
    assert public[0]["selection_status"] == "not_captured"
    assert public[0]["selected_sources"] is None


def test_partial_source_identity_does_not_match_another_partial_identity():
    binding = call(1, 10)
    frame = selection(binding, [dict(content="[]")])
    final = dict(
        category="final_input",
        epoch=1,
        seq=10,
        identity=binding["identity"],
        observations=[dict(field_path="messages[1].content", ranges=[])],
    )
    public, _, _ = review_evidence(dict(input_observations=[frame, final]), [binding])
    assert public[0]["final_input_sources"][0]["matches_selection"] is None


def test_partial_process_capture_and_inactive_observer_sentinel_stay_unknown():
    binding = call(1, 10)
    evidence = dict(
        facts=dict(write_intent=None, pending=None, control_writes=None),
        observed=dict(coverage=dict(version="v1")),
        calls=[
            dict(
                purpose="provider_send",
                epoch=1,
                seq=8,
                observer_high_water=0,
                **binding["identity"],
            )
        ],
    )
    _, execution, _ = review_evidence(evidence, [binding])
    assert execution["signals"]["write_intent_present"] is None
    assert execution["signals"]["pending_present"] is None
    assert execution["database_observer"]["markers"] is None
    assert execution["database_observer"]["deltas"] is None
    assert execution["controls"]["receipts"] is None
    assert execution["calls"][0]["observer_high_water"] is None
