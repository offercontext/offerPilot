"""Offline exact-location contract, not semantic attribution or production repair."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.shadow_quote_locator.locator import LocatorError, locate_quote


def sources(text="prefix quote suffix"):
    return [{"id": "user", "text": text}]


@pytest.mark.parametrize("text,quote,start,end", [
    ("prefix quote suffix", "quote", 7, 12),
    (" x ", " x ", 0, 3),
    ("a" * 128, "a" * 128, 0, 128),
    ("x" * 4095 + "y", "y", 4095, 4096),
    ("😀中quote", "quote", 2, 7),
    ("xe\u0301y", "e\u0301", 1, 3),
    ("x👩‍💻y", "👩‍💻", 1, 4),
    ("x👍🏽y", "👍🏽", 1, 3),
])
def test_exact_codepoint_end_exclusive_location(text, quote, start, end):
    assert locate_quote(sources(text), "user", quote) == {
        "source_id": "user", "start": start, "end": end, "quote": quote}
    assert text[start:end] == quote


@pytest.mark.parametrize("text,quote,code", [
    ("abc", "missing", "quote_not_found"),
    ("quote and quote", "quote", "quote_not_unique"),
    ("aaa", "aa", "quote_not_unique"),
    ("é", "e\u0301", "quote_not_found"),
    ("Quote", "quote", "quote_not_found"),
    ("x y", "x  y", "quote_not_found"),
    ("xe\u0301y", "e", "quote_splits_grapheme"),
    ("xe\u0301y", "\u0301", "quote_splits_grapheme"),
    ("x👩‍💻y", "👩", "quote_splits_grapheme"),
    ("x👩‍💻y", "💻", "quote_splits_grapheme"),
    ("x👍🏽y", "👍", "quote_splits_grapheme"),
    ("x👍🏽y", "🏽", "quote_splits_grapheme"),
    ("x🇨🇳y", "🇨", "quote_splits_grapheme"),
    ("x\r\ny", "\ny", "quote_splits_grapheme"),
])
def test_nonmatching_ambiguous_and_split_quotes_reject(text, quote, code):
    with pytest.raises(LocatorError, match=f"^{code}$"):
        locate_quote(sources(text), "user", quote)


@pytest.mark.parametrize("value", [None, True, 1, b"quote", [], {}, "", " \t\n", "\u3000", "x" * 129, "\ud800"])
def test_invalid_quote(value):
    with pytest.raises(LocatorError, match="^quote_invalid$"):
        locate_quote(sources(), "user", value)


@pytest.mark.parametrize("value", [None, (), {}, [], sources() * 9])
def test_invalid_sources_container(value):
    with pytest.raises(LocatorError, match="^sources_invalid$"):
        locate_quote(value, "user", "quote")


@pytest.mark.parametrize("value", [None, [], {"id": "user"}, {"text": "quote"},
    {"id": "user", "text": "quote", "field": "user_input"}])
def test_invalid_source_schema(value):
    with pytest.raises(LocatorError, match="^source_invalid$"):
        locate_quote([value], "user", "quote")


@pytest.mark.parametrize("value", [None, True, 1, b"quote", "", " \n", "x" * 4097, "\udfff"])
def test_invalid_source_text(value):
    with pytest.raises(LocatorError, match="^source_invalid$"):
        locate_quote(sources(value), "user", "quote")


@pytest.mark.parametrize("value", [None, True, 1, "", "user/other", "用户", "user\n", "a" * 65])
def test_invalid_source_identifier(value):
    with pytest.raises(LocatorError, match="^source_invalid$"):
        locate_quote([{"id": value, "text": "quote"}], "user", "quote")


@pytest.mark.parametrize("value", [None, True, 1, [], "reference", "USER"])
def test_requested_source_must_be_explicitly_present(value):
    with pytest.raises(LocatorError, match="^source_id_invalid$"):
        locate_quote(sources(), value, "quote")


def test_duplicate_source_id_rejects_even_when_text_differs():
    with pytest.raises(LocatorError, match="^source_invalid$"):
        locate_quote(sources() + sources("other"), "user", "quote")


def test_only_selected_source_is_searched_and_eight_sources_allowed():
    rows = [{"id": f"source_{i}-", "text": "quote"} for i in range(8)]
    assert locate_quote(rows, "source_7-", "quote")["end"] == 5
    with pytest.raises(LocatorError, match="^quote_not_found$"):
        locate_quote(sources("absent") + [{"id": "reference", "text": "quote"}], "user", "quote")


def test_s01_full_short_quote_locates_without_changing_original_or_claiming_support():
    path = Path(__file__).resolve().parents[2] / "scripts/companion_ai_validation/shadow_cases/cases.json"
    original_bytes = path.read_bytes()
    case = json.loads(original_bytes)[0]
    original_case = deepcopy(case)
    rows = [{"id": row["id"], "text": row["text"]} for row in case["sources"]]
    original_rows = deepcopy(rows)
    quote = rows[0]["text"]
    assert len(quote) == 51
    result = locate_quote(rows, "user", quote)
    assert result == {"source_id": "user", "start": 0, "end": 51, "quote": quote}
    assert rows == original_rows and case == original_case
    assert path.read_bytes() == original_bytes
    assert "verdict" not in result and "target" not in result


def test_builtin_subclasses_are_not_accepted_as_protocol_types():
    class Text(str):
        pass
    class Rows(list):
        pass
    class Row(dict):
        pass
    for rows, source_id, quote in [
        (Rows(sources()), "user", "quote"),
        ([Row(id="user", text="quote")], "user", "quote"),
        ([{"id": Text("user"), "text": "quote"}], "user", "quote"),
        (sources(Text("quote")), "user", "quote"),
        (sources(), Text("user"), "quote"),
        (sources(), "user", Text("quote")),
    ]:
        with pytest.raises(LocatorError):
            locate_quote(rows, source_id, quote)
