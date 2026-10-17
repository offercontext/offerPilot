"""History preparation must fail closed, using real isolated Git repositories."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_history", ROOT / "scripts/test_shards/check_history.py"
)
assert SPEC is not None and SPEC.loader is not None
HISTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HISTORY)


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repository, text=True, encoding="utf-8"
    ).strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "-c", "user.name=cyq", "-c",
         "user.email=112178718+XiaoZheBrother@users.noreply.github.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    baseline = _commit(root, "baseline")
    head = _commit(root, "head")
    return root, baseline, head


def test_preflight_requirements_match_the_existing_source_gate_pins():
    source = ast.parse((ROOT / "tests/test_core_task_surface_assets.py").read_text("utf-8"))
    baseline = next(
        ast.literal_eval(node.value) for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BASELINE"
                for target in node.targets)
    )
    required = {baseline: False}
    for relative, ancestry in [
        ("coreTaskSurface/coreTaskSurfaceGate.test.ts", False),
        ("assistantSurface/assistantSurfaceGate.test.ts", True),
        ("workspaceExperience/desktopTaskFlowGate.test.ts", True),
    ]:
        text = (ROOT / "web/src/features" / relative).read_text("utf-8")
        pins = re.findall(r"^const (?:BASELINE|PROJECT_END) = '([0-9a-f]{40})';$", text, re.M)
        assert len(pins) == (2 if ancestry else 1)
        for pin in pins:
            required[pin] = ancestry or required.get(pin, False)
    assert HISTORY.REQUIRED_HISTORY == required


def test_preflight_rejects_missing_commit(repository, monkeypatch):
    root, _, _ = repository
    monkeypatch.setattr(HISTORY, "REQUIRED_HISTORY", {"f" * 40: False})
    with pytest.raises(subprocess.CalledProcessError, match="cat-file"):
        HISTORY.verify_history(root)


def test_preflight_rejects_existing_nonancestor_commit(repository, monkeypatch):
    root, _, _ = repository
    _git(root, "checkout", "-q", "--orphan", "unrelated")
    unrelated = _commit(root, "unrelated")
    _git(root, "checkout", "-q", "main")
    monkeypatch.setattr(HISTORY, "REQUIRED_HISTORY", {unrelated: True})
    with pytest.raises(subprocess.CalledProcessError, match="merge-base"):
        HISTORY.verify_history(root)


def test_preflight_rejects_shallow_history_then_accepts_real_restoration(
    repository, tmp_path, monkeypatch
):
    root, baseline, head = repository
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-tags", root.as_uri(), str(shallow))
    assert _git(shallow, "rev-parse", "--is-shallow-repository") == "true"
    monkeypatch.setattr(HISTORY, "REQUIRED_HISTORY", {baseline: True})
    with pytest.raises(subprocess.CalledProcessError, match="cat-file"):
        HISTORY.verify_history(shallow)
    # Even a present pin must not hide an incomplete checkout ancestry graph.
    monkeypatch.setattr(HISTORY, "REQUIRED_HISTORY", {head: True})
    with pytest.raises(ValueError, match="fetch-depth: 0"):
        HISTORY.verify_history(shallow)
    _git(shallow, "fetch", "-q", "--unshallow", "origin")
    monkeypatch.setattr(HISTORY, "REQUIRED_HISTORY", {baseline: True, head: False})
    HISTORY.verify_history(shallow)
