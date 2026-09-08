from __future__ import annotations

import pytest

from offerpilot.knowledge.interview_capture import (
    FragmentValidationError,
    canonicalize_fragments,
    parse_capture_snapshot,
    serialize_capture_snapshot,
    slice_utf16,
)


def fragment(path: str, start: int, end: int, text: str, fragment_id: str = "client") -> dict[str, object]:
    return {
        "fragment_id": fragment_id,
        "path": path,
        "start": start,
        "end": end,
        "text": text,
    }


def test_utf16_offsets_match_browser_for_cjk_emoji_and_combining_text() -> None:
    value = "问题：Kafka 🚀 e\u0301"
    assert slice_utf16(value, 3, 8) == "Kafka"
    assert slice_utf16(value, 9, 11) == "🚀"
    assert slice_utf16(value, 12, 14) == "e\u0301"


def test_utf16_slice_rejects_surrogate_pair_boundary() -> None:
    with pytest.raises(FragmentValidationError, match="surrogate"):
        slice_utf16("🚀", 1, 2)


def test_canonicalize_uses_source_fields_and_rejects_normalization_or_overlap() -> None:
    source_fields = {"/questions": "é cafe\u0301"}
    with pytest.raises(FragmentValidationError, match="text"):
        canonicalize_fragments(
            [fragment("/questions", 0, 1, "e")],
            source_fields,
        )

    with pytest.raises(FragmentValidationError, match="overlap"):
        canonicalize_fragments(
            [
                fragment("/questions", 0, 3, "é c", "a"),
                fragment("/questions", 2, 7, "cafe\u0301", "b"),
            ],
            source_fields,
        )


def test_selected_fragments_reject_count_and_utf8_byte_limits() -> None:
    source_fields = {"/questions": "x" * 21}
    too_many = [fragment("/questions", i, i + 1, "x", str(i)) for i in range(21)]
    with pytest.raises(FragmentValidationError, match="fragment_count"):
        canonicalize_fragments(too_many, source_fields)

    oversized_source = "x" * 4097
    oversized = [fragment("/questions", 0, 4097, oversized_source)]
    with pytest.raises(FragmentValidationError, match="utf8_bytes"):
        canonicalize_fragments(oversized, {"/questions": oversized_source})

    total_source = "x" * 32769
    total_fragments = [
        fragment("/questions", i * 4096, (i + 1) * 4096, "x" * 4096, str(i))
        for i in range(8)
    ]
    total_fragments.append(fragment("/questions", 32768, 32769, "x", "last"))
    with pytest.raises(FragmentValidationError, match="total_utf8_bytes"):
        canonicalize_fragments(total_fragments, {"/questions": total_source})


def test_snapshot_round_trip_reads_exact_text_bytes_then_one_lf() -> None:
    fragments = canonicalize_fragments(
        [fragment("/questions", 0, 2, "问题", "a"), fragment("/mood", 0, 2, "🚀", "b")],
        {"/questions": "问题", "/mood": "🚀"},
    )
    encoded = serialize_capture_snapshot(fragments)
    assert parse_capture_snapshot(encoded) == fragments
    assert serialize_capture_snapshot(parse_capture_snapshot(encoded)) == encoded


def test_snapshot_parser_rejects_wrong_text_byte_length_or_missing_lf() -> None:
    fragments = canonicalize_fragments(
        [fragment("/questions", 0, 2, "问题")], {"/questions": "问题"}
    )
    encoded = serialize_capture_snapshot(fragments)
    with pytest.raises(FragmentValidationError):
        parse_capture_snapshot(encoded.replace(b"bytes=6", b"bytes=5"))
    with pytest.raises(FragmentValidationError):
        parse_capture_snapshot(encoded[:-1])


def test_canonical_ids_are_reassigned_deterministically() -> None:
    fragments = canonicalize_fragments(
        [
            fragment("/mood", 0, 2, "🚀", "second"),
            fragment("/questions", 0, 2, "问题", "first"),
        ],
        {"/questions": "问题", "/mood": "🚀"},
    )
    assert [item.fragment_id for item in fragments] == ["fragment_001", "fragment_002"]
    assert [item.path for item in fragments] == ["/questions", "/mood"]
