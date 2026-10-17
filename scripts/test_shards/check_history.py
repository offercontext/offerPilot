"""Fail before regression work if checkout omitted immutable source-gate history."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


# Commit -> whether the existing source gate also requires HEAD ancestry.
# Keep these aligned with the immutable pins, never rewrite their fixtures.
REQUIRED_HISTORY = {
    # tests/test_core_task_surface_assets.py and coreTaskSurfaceGate.test.ts
    "93fb0063118761f2c76e71e4209000feee0f755b": False,
    # assistantSurfaceGate.test.ts: baseline and completed project range
    "7c36957176445c5213a31b013251fbbce8d610db": True,
    "0c10e05e256eb757d5f89a8b009dcea193f2fc78": True,
    # desktopTaskFlowGate.test.ts: the same baseline and completed project range
    "78195000bd94fdfd6fe8508033e0a8687fde3323": True,
}


def verify_history(repository: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=repository, text=True, encoding="utf-8"
        ).strip()

    for revision, requires_ancestry in REQUIRED_HISTORY.items():
        git("cat-file", "-e", f"{revision}^{{commit}}")
        if requires_ancestry:
            git("merge-base", "--is-ancestor", revision, "HEAD")
    if git("rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("Regression checkout must use fetch-depth: 0 (complete history)")


if __name__ == "__main__":
    try:
        verify_history(Path(__file__).resolve().parents[2])
    except (subprocess.CalledProcessError, ValueError) as exc:
        sys.exit(f"Required regression Git history is unavailable: {exc}")
    print("Required regression Git history verified (4 immutable commits, complete ancestry)")
