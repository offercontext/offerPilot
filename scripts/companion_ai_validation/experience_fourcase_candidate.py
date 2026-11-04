"""Immutable repaired product objects for the separately approved four-turn switch retest."""
from collections.abc import Mapping

SOURCE_SHA = "9fa2b58c7c036f5ae7303af7a0323c0970fc283e"
TREE_SHA = "31ec69b3f121401905bac2ebf2809466ad34fae0"
OBJECTS = {'src': '22e2afc3132aa14d70b2ba117ba866d9a5e9e2d6', 'pyproject.toml': 'dfa900978a00026b0c7a222bcb6ba7ce62201121', 'uv.lock': 'eb7803ff8bf95818a70ad597a9e41d22187896c7', 'web': 'aa1d7428642416642524f304f5a2bd99012cd0fd'}

def validate_objects(actual: Mapping[str, str]) -> None:
    from scripts.companion_ai_validation.broker import GateError
    if dict(actual) != OBJECTS:
        raise GateError("product_candidate_mismatch")
