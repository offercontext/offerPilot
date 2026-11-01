"""Immutable repaired product objects for the separately approved five-turn retest."""
from collections.abc import Mapping

SOURCE_SHA = "e570fd170cc476324d0580bed894cc2c394f63b1"
TREE_SHA = "097c30d940546c3f5303f118bc4712b7339ec840"
OBJECTS = {
    "src": "43b8a9a25a48bc44869779b892ba0f7882a7972e",
    "pyproject.toml": "b7b8e27f31e1a402bc7e5330f0b13b3c9bc04dfe",
    "uv.lock": "eb7803ff8bf95818a70ad597a9e41d22187896c7",
    "web": "1aa6d4984c9e08c00181d0c2a762e730f430e9de",
}


def validate_objects(actual: Mapping[str, str]) -> None:
    from scripts.companion_ai_validation.broker import GateError
    if dict(actual) != OBJECTS:
        raise GateError("product_candidate_mismatch")
