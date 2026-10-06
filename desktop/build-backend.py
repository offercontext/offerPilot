"""Build on the target OS with the repository's frozen uv environment.

Run from the repository root:
uv run --frozen --with pyinstaller==6.16.0 --with pyinstaller-hooks-contrib==2025.9 \
    python desktop/build-backend.py
"""

from __future__ import annotations

import os
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"


def main() -> int:
    for name, expected in (("pyinstaller", "6.16.0"), ("pyinstaller-hooks-contrib", "2025.9")):
        if version(name) != expected:
            raise RuntimeError(f"Build requires {name}=={expected}; use the documented uv command")

    cache = DESKTOP / "build" / "tiktoken-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import tiktoken

    # The library verifies the upstream hashes. Cache every shipped OpenAI
    # encoding, including Knowledge's fixed cl100k_base and LiteLLM's encodings.
    # Download failure is a build failure, never an implicit runtime dependency.
    for encoding in tiktoken.list_encoding_names():
        tiktoken.get_encoding(encoding)
    if not any(cache.iterdir()):
        raise RuntimeError("Tokenizer cache is empty; refusing an incomplete frozen build")

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DESKTOP / "backend-dist"),
         "--workpath", str(DESKTOP / "build" / "pyinstaller"),
         str(DESKTOP / "backend.spec")],
        cwd=ROOT,
        check=True,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = DESKTOP / "backend-dist" / "offerpilot-backend" / f"offerpilot-backend{suffix}"
    if not executable.is_file():
        raise RuntimeError(f"Missing frozen backend: {executable}")
    print(f"Frozen backend: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
