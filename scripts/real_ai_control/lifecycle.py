"""Owned whole-tree control deadline, distinct from product server/worker waits."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import subprocess
import time
from typing import Any

from offerpilot.evaluation.runner import process_tree


@dataclass
class ExitEvidence:
    native_exit: int | None = None
    owned_tree_exited: bool | None = None
    forced_kill: bool = False
    watchdog_expired: bool = False
    error_type: str | None = None
    elapsed_seconds: float = 0
    directories_deleted: bool = False
    allocation_releasable: bool = False


def supervise(
    argv: list[str],
    *,
    cwd: Path,
    output: Path,
    watchdog_seconds: float,
    env: dict[str, str],
    control: Any = None,
) -> dict[str, Any]:
    if not 0 < watchdog_seconds <= 900:
        raise ValueError("Explicit control watchdog required (at most 900 seconds)")
    output.mkdir(exist_ok=False)
    evidence, tree = ExitEvidence(), None
    started = time.monotonic()
    try:
        with (
            (output / "stdout.log").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            tree = process_tree(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
            while True:
                if control is not None and control.failed.is_set():
                    raise RuntimeError("Control pipe or archive failed")
                remaining = watchdog_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    evidence.watchdog_expired = True
                    raise TimeoutError("Whole-tree control deadline exhausted")
                try:
                    evidence.native_exit = tree.wait(timeout=min(0.1, remaining))
                    evidence.owned_tree_exited = True
                    break
                except subprocess.TimeoutExpired:
                    continue
    except BaseException as error:
        evidence.error_type = type(error).__name__
        if tree is not None and evidence.owned_tree_exited is not True:
            try:
                tree.kill()
                evidence.forced_kill = True
                evidence.native_exit = tree.wait(timeout=5)
                evidence.owned_tree_exited = True
            except BaseException:
                evidence.owned_tree_exited = None
    finally:
        if tree is not None:
            tree.close(timeout=5)
        evidence.elapsed_seconds = time.monotonic() - started
    # Ownership alone cannot establish complete control, billing or model evidence.
    return asdict(evidence)
