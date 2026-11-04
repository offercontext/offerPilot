"""Immutable repaired product objects for the separately approved five-turn retest."""
from collections.abc import Mapping

SOURCE_SHA = "42a54a48a90e3cc7738d3c6029e11aebe46d276b"
TREE_SHA = "57e67e539fb0c7798ab9031cafc0fc73ddfdd75b"
OBJECTS = {
    "src": "4df7375c334aa1fe68395320b355bbe43eaa2bab",
    "pyproject.toml": "dfa900978a00026b0c7a222bcb6ba7ce62201121",
    "uv.lock": "eb7803ff8bf95818a70ad597a9e41d22187896c7",
    "web": "aa1d7428642416642524f304f5a2bd99012cd0fd",
}


def validate_objects(actual: Mapping[str, str]) -> None:
    from scripts.companion_ai_validation.broker import GateError
    if dict(actual) != OBJECTS:
        raise GateError("product_candidate_mismatch")
