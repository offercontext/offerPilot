"""Immutable product objects exercised by the separate experience-only helper.

The helper may change validation code, never quietly change the product whose
PR is being accepted. Git object IDs are inspected before secret access.
"""
from collections.abc import Mapping

SOURCE_SHA = "6d5ff92ac67fb01aad1752f2b307c79a706e943d"
TREE_SHA = "3de6eca96b06ee2fa031c4c5e9e9c1cc60c1af72"
OBJECTS = {
    "src": "19c4461c814db3cc32e098e7c889656b271fe15c",
    "pyproject.toml": "b7b8e27f31e1a402bc7e5330f0b13b3c9bc04dfe",
    "uv.lock": "eb7803ff8bf95818a70ad597a9e41d22187896c7",
    "web": "176cb3683a6db77390985ff436e61e98042f5532",
}


def validate_objects(actual: Mapping[str, str]) -> None:
    from scripts.companion_ai_validation.broker import GateError
    if dict(actual) != OBJECTS:
        raise GateError("product_candidate_mismatch")
