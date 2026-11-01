"""Offline regression; immutable live failure is never repaired or re-sent."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.companion_ai_validation import broker as gate, shadow_driver as shadow


RAW = Path(__file__).with_name("fixtures") / "shadow-s01-40812959831-response.json"
RAW_SHA256 = "91678126eb9d71037bccfdbbad462ad5062e6401ba97768a04511a61d6f2075b"


def recorded():
    raw = RAW.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA256
    return json.loads(raw)["choices"][0]["message"]["content"]


def test_original_zero_width_nonempty_quote_is_rejected():
    content = recorded()
    value = json.loads(content)
    case = shadow.cases()[0]
    source = next(s["text"] for s in case["sources"] if s["id"] == "user")
    evidence = value["evidence"][0]
    assert evidence == {"source_id": "user", "start": 0, "end": 0, "quote": source}
    assert len(source) == 51
    with pytest.raises(gate.GateError, match="shadow_reference_span"):
        shadow.validate(content, case)


def test_synthetic_valid_range_is_structure_only_not_a_live_pass():
    original = recorded()
    synthetic = deepcopy(json.loads(original))
    synthetic["evidence"][0]["end"] = 51
    result = shadow.validate(gate.encode(synthetic).decode(), shadow.cases()[0])
    assert result["verdict"] == "ambiguous"
    labels = json.loads((shadow.ASSETS / "blind-labels.json").read_text())
    assert labels[0]["label"] == "contradicted"
    assert labels[0]["alternative"].startswith("ambiguous")
    assert recorded() == original
    with pytest.raises(gate.GateError, match="shadow_reference_span"):
        shadow.validate(original, shadow.cases()[0])


@pytest.mark.parametrize("start,end", [(0, 50), (0, 52), (0, 153), (1, 51)])
def test_nonexact_source_ranges_still_rejected(start, end):
    synthetic = deepcopy(json.loads(recorded()))
    synthetic["evidence"][0].update(start=start, end=end)
    with pytest.raises(gate.GateError, match="shadow_reference_span"):
        shadow.validate(gate.encode(synthetic).decode(), shadow.cases()[0])
