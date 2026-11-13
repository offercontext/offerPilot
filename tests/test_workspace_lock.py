from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from offerpilot.workspace_lock import acquire_workspace, exclusive_control


def child(root: Path, exclusive: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; import os; "
         "from offerpilot.workspace_lock import acquire_workspace; "
         f"fd=acquire_workspace(Path({str(root)!r}), exclusive={exclusive}); os.close(fd)"],
        capture_output=True, text=True, timeout=10,
    )


def test_two_processes_share_and_exclusive_waits(tmp_path: Path) -> None:
    fd = acquire_workspace(tmp_path)
    try:
        assert child(tmp_path, False).returncode == 0
        assert child(tmp_path, True).returncode != 0
    finally:
        os.close(fd)
    assert child(tmp_path, True).returncode == 0


def test_exclusive_blocks_all_other_processes(tmp_path: Path) -> None:
    fd = acquire_workspace(tmp_path, exclusive=True)
    try:
        assert child(tmp_path, False).returncode != 0
        assert child(tmp_path, True).returncode != 0
    finally:
        os.close(fd)


def test_crashed_owner_releases_lock(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "from pathlib import Path; import time; "
         "from offerpilot.workspace_lock import acquire_workspace; "
         f"fd=acquire_workspace(Path({str(tmp_path)!r}), exclusive=True); "
         "print('locked',flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        assert child(tmp_path, False).returncode != 0
    finally:
        process.kill()
        process.wait(timeout=5)
    assert child(tmp_path, True).returncode == 0


def test_control_is_exclusive(tmp_path: Path) -> None:
    with exclusive_control(tmp_path):
        with pytest.raises(OSError):
            with exclusive_control(tmp_path):
                pass


def test_alias_uses_same_lock_identity(tmp_path: Path) -> None:
    from tests.conftest import symlink_or_skip

    root = tmp_path / "workspace"
    root.mkdir()
    alias = tmp_path / "alias"
    symlink_or_skip(alias, root, target_is_directory=True)
    fd = acquire_workspace(alias)
    try:
        assert child(root, False).returncode == 0
        assert child(root, True).returncode != 0
    finally:
        os.close(fd)
    assert child(root, True).returncode == 0
