"""PyInstaller entry point; the desktop API keeps its implementation in offerpilot."""

import multiprocessing
import os
import json
import sys
from pathlib import Path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # PyInstaller's one-directory bundle contains these read-only resources.
    # Set them before importing LiteLLM or OfferPilot: startup must not fetch pricing.
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["TIKTOKEN_CACHE_DIR"] = str(bundle / "tiktoken-cache")
    # LiteLLM otherwise replaces TIKTOKEN_CACHE_DIR with its own partial cache.
    os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = str(bundle / "tiktoken-cache")
    if sys.argv[1:] == ["--packaging-self-check"]:
        import tiktoken
        from charset_normalizer import from_bytes
        from litellm import completion
        from offerpilot.knowledge.tokenizer import count_tokens

        for name in tiktoken.list_encoding_names():
            tiktoken.get_encoding(name).encode("OfferPilot desktop validation")
        if str(from_bytes(b"OfferPilot desktop validation").best()) != "OfferPilot desktop validation":
            raise RuntimeError("Frozen charset detector failed")
        if not callable(completion) or count_tokens("OfferPilot").count < 1:
            raise RuntimeError("Frozen dependency self-check failed")
        print(json.dumps({"packaging_self_check": "ok", "tokenizer": "cl100k_base"}))
    else:
        from offerpilot.desktop import main

        raise SystemExit(main())
