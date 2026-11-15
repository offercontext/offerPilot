"""Offline regressions; metadata fixtures are synthetic except original text bytes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from experiments.shadow_input_projection.projection import LONG, SHORT, ProjectionError, project

REPO = Path(__file__).resolve().parents[3]


def event(**changes):
    return {"source_message_id": 7, "conversation_id": 1, "turn": 9,
            "activity_id": "test-activity", "step": "选颜色", "value": "蓝色",
            "status": "completed", "time": "2026-10-24T03:00:00Z",
            "time_source": "synthetic.actual_participation.completed_at", **changes}


def fixture(text=None):
    text = text or ("prefix😀\n" + SHORT + "\n" + LONG + "\nsuffix")
    spans = []
    for literal in (SHORT, LONG):
        start = text.index(literal)
        spans.append({"start": start, "end": start + len(literal), "text": literal, "event": event()})
    return text, spans


def assert_mapped(result, original):
    originals = []
    last = 0
    for mapping in result["mappings"]:
        span, view = mapping["original"], mapping["view"]
        assert span["start"] == last
        assert original[span["start"]:span["end"]] == span["text"]
        assert result["view"][view["start"]:view["end"]] == view["text"]
        assert original[:span["start"]].encode() + span["text"].encode() + original[span["end"]:].encode() == original.encode()
        originals.append(span["text"])
        last = span["end"]
    assert last == len(original) and "".join(originals) == original


@pytest.mark.parametrize("reverse", [False, True])
def test_same_complete_event_keeps_more_complete_original_and_all_provenance(reverse):
    text, spans = fixture("head\n" + LONG + "\n" + SHORT + "\ntail" if reverse else None)
    original = deepcopy(spans)
    result = project(text, spans)
    assert result["merged_count"] == 1
    assert SHORT not in result["view"] and LONG in result["view"]
    assert "也不是长期偏好" in result["view"]
    assert "不证明共同制作或整项完成" in result["view"]
    retained = next(m for m in result["mappings"] if m["status"] == "kept")
    merged = next(m for m in result["mappings"] if m["status"] == "merged")
    assert retained["view"] == merged["view"]
    assert retained["original"]["event"] == merged["original"]["event"]
    assert spans == original
    assert_mapped(result, text)


@pytest.mark.parametrize("key", ["source_message_id", "conversation_id", "turn", "activity_id", "step",
                                  "value", "status", "time", "time_source"])
@pytest.mark.parametrize("missing", [True, False])
def test_incomplete_annotations_never_authorize_merge(key, missing):
    text, spans = fixture()
    if missing:
        spans[0]["event"].pop(key)
    else:
        spans[0]["event"][key] = None
    result = project(text, spans)
    assert result["view"] == text and result["merged_count"] == 0
    assert_mapped(result, text)


@pytest.mark.parametrize("key,value", [("source_message_id", 8), ("conversation_id", 2), ("turn", 10),
                                       ("activity_id", "different"), ("source_message_id", "7")])
def test_different_event_identity_is_not_deduplicated(key, value):
    text, spans = fixture()
    spans[1]["event"][key] = value
    result = project(text, spans)
    assert result["view"] == text and result["merged_count"] == 0


def test_same_value_at_different_time_and_different_event_is_preserved():
    text, spans = fixture()
    spans[1]["event"].update(source_message_id=8, turn=10, time="2026-10-25T03:00:00Z")
    assert project(text, spans)["view"] == text


@pytest.mark.parametrize("key,value", [("value", "红色"), ("status", "cancelled"), ("time", "2026-10-25T03:00:00Z")])
def test_same_identity_conflicting_fact_status_or_time_fails_closed(key, value):
    text, spans = fixture()
    spans[1]["event"][key] = value
    with pytest.raises(ProjectionError, match="event_fact_conflict"):
        project(text, spans)


def test_time_provenance_paths_may_differ_but_both_are_preserved():
    text, spans = fixture()
    spans[1]["event"]["time_source"] = "joined_by_explicit_identity.actual_participation.completed_at"
    result = project(text, spans)
    assert result["merged_count"] == 1
    assert {m["original"]["event"]["time_source"] for m in result["mappings"] if m["status"] != "passthrough"} == {
        span["event"]["time_source"] for span in spans}


@pytest.mark.parametrize("bad", [True, [], {}, -1, 2**63, "", " "])
def test_malicious_identity_types_are_rejected(bad):
    text, spans = fixture()
    spans[0]["event"]["source_message_id"] = bad
    with pytest.raises(ProjectionError):
        project(text, spans)


def test_unknown_metadata_field_cannot_inject_a_merge_instruction():
    text, spans = fixture()
    spans[0]["event"]["force_merge"] = True
    with pytest.raises(ProjectionError, match="event_schema"):
        project(text, spans)


def test_equal_text_without_supported_pair_or_with_changed_qualifier_is_unchanged():
    text = SHORT + "\n" + LONG.replace("也不是长期偏好", "可以认定长期偏好")
    spans = [{"start": 0, "end": len(SHORT), "text": SHORT, "event": event()},
             {"start": len(SHORT) + 1, "end": len(text), "text": text[len(SHORT) + 1:], "event": event()}]
    assert project(text, spans)["view"] == text
    text = SHORT + "\n" + SHORT
    spans[1].update(end=len(text), text=SHORT)
    assert project(text, spans)["view"] == text


@pytest.mark.parametrize("text,start,end", [("xe\u0301y", 1, 2), ("x👩‍💻y", 1, 2), ("x👍🏽y", 2, 3)])
def test_annotations_cannot_split_graphemes(text, start, end):
    with pytest.raises(ProjectionError, match="span_splits_grapheme"):
        project(text, [{"start": start, "end": end, "text": text[start:end], "event": event()}])


def test_no_annotations_keeps_entire_source_with_reversible_mapping():
    text = "👩‍💻 e\u0301 nothing identified"
    result = project(text, [])
    assert result["view"] == text and result["merged_count"] == 0
    assert_mapped(result, text)


def test_real_s03_s04_original_reference_stays_ambiguous_but_view_has_one_quote():
    path = REPO / "scripts/companion_ai_validation/shadow_cases/cases.json"
    original = path.read_bytes()
    cases = json.loads(original)
    spec = importlib.util.spec_from_file_location("offline_locator", REPO / "experiments/shadow_quote_locator/locator.py")
    locator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(locator)
    for case in cases[2:4]:
        text = next(s["text"] for s in case["sources"] if s["id"] == "reference")
        quote = '选择内容是"蓝色"'
        with pytest.raises(locator.LocatorError, match="quote_not_unique"):
            locator.locate_quote([{"id": "reference", "text": text}], "reference", quote)
        _, spans = fixture(text)  # Synthetic identity annotations, never claimed archive truth.
        result = project(text, spans)
        found = locator.locate_quote([{"id": "reference", "text": result["view"]}], "reference", quote)
        assert result["view"][found["start"]:found["end"]] == quote
        assert_mapped(result, text)
    assert path.read_bytes() == original


@pytest.mark.parametrize("damage", ["wrong_text", "boolean_start", "overlap", "extra_field", "wrong_template_value"])
def test_invalid_annotation_cannot_silently_delete_source(damage):
    text, spans = fixture()
    if damage == "wrong_text":
        spans[0]["text"] = "not the original"
    elif damage == "boolean_start":
        spans[0]["start"] = True
    elif damage == "overlap":
        spans.append(deepcopy(spans[0]))
    elif damage == "extra_field":
        spans[0]["delete"] = True
    else:
        for span in spans:
            span["event"]["value"] = "红色"
    with pytest.raises(ProjectionError):
        project(text, spans)
