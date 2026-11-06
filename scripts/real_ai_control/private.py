"""Owned secret-copy paths outside all evidence; no recursive deletion or live cleanup."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
from typing import Any

from offerpilot.evaluation.core_targeted_account import safe_path


def work_path(run_id: str, evidence: Path) -> Path:
    if not re.fullmatch(r"offline-[a-f0-9]{16}", run_id):
        raise ValueError("Fresh owned offline run identifier required")
    base = safe_path(Path(tempfile.gettempdir()).absolute())
    path = base / ("offerpilot-verify-owned-" + run_id)
    if (
        path.exists()
        or path.is_relative_to(evidence.resolve())
        or evidence.resolve().is_relative_to(path)
    ):
        raise ValueError("Private work must be fresh and outside evidence")
    return path


def remove_owned_config_copies(root: Path, *, owned_tree_exited: Any) -> dict[str, Any]:
    if owned_tree_exited is not True:
        return dict(removed=[], held_for_audit=True, reason="ownership not proven")
    root = safe_path(root)
    base = safe_path(Path(tempfile.gettempdir()).absolute())
    if root.parent != base or not re.fullmatch(
        r"offerpilot-verify-owned-offline-[a-f0-9]{16}", root.name
    ):
        raise ValueError("Cleanup target is not this protocol owned work root")
    removed = []
    # Only these own config copies; never recursive deletion or original key refs.
    targets = [
        root / "source-config/config.json",
        *root.glob("main-*/config.json"),
        *root.glob("unconfigured-*/config.json"),
    ]
    for target in targets:
        target = safe_path(target)
        if target.exists():
            if not target.is_file() or not target.is_relative_to(root):
                raise ValueError("Unsafe own config cleanup target")
            target.unlink()
            removed.append(str(target))
    return dict(
        removed=removed,
        held_for_audit=False,
        original_credentials_touched=False,
        directories_deleted=False,
    )
