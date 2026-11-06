"""Separate raw product/dependency and controller manifests for offline qualification."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from offerpilot.evaluation.core_targeted_account import safe_path
from offerpilot.evaluation.runner import environment, git
from .protocol import sha


def implementation(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): sha(safe_path(p).read_bytes())
        for p in sorted((root / "scripts/real_ai_control").glob("*.py"))
    }


def product_source(root: Path) -> dict[str, Any]:
    paths = [
        *(root / "src").rglob("*.py"),
        *(root / "scripts/evaluation_subject").glob("*.py"),
        root / "pyproject.toml",
        root / "uv.lock",
        root / "web/package-lock.json",
    ]
    return dict(
        scope="all product Python, evaluation subject helpers, dependency locks and interpreter",
        checkout=str(root.resolve()),
        head=git(root, "rev-parse", "HEAD").decode().strip(),
        files={
            str(p.relative_to(root)).replace("\\", "/"): sha(safe_path(p).read_bytes())
            for p in sorted(set(paths))
        },
        interpreter=dict(
            path=str(Path(sys.executable).resolve()), sha256=sha(Path(sys.executable).read_bytes())
        ),
        environment=environment(),
    )
