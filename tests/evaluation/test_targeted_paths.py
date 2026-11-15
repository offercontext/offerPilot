"""Authority path boundaries: missing tails are permitted, links/errors are not."""
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from offerpilot.evaluation.core_targeted_account import safe_path


def test_existing_path_and_missing_tail_remain_canonical(tmp_path):
    path = tmp_path / "account.json"
    path.write_text("unchanged", encoding="utf-8")
    assert safe_path(path) == path
    future = tmp_path / "future" / "account.json"
    assert safe_path(future) == future
    assert not future.parent.exists()


@pytest.mark.parametrize("error", [PermissionError, NotADirectoryError])
def test_path_inspection_errors_fail_closed(tmp_path, monkeypatch, error):
    path = tmp_path / "account.json"
    path.touch()
    original = Path.lstat

    def inspect(self):
        if self == path:
            raise error("inspection unavailable")
        return original(self)

    monkeypatch.setattr(Path, "lstat", inspect)
    with pytest.raises(error, match="inspection unavailable"):
        safe_path(path)


@pytest.mark.parametrize("link_mode,reparse", [(True, False), (False, True)])
def test_link_stat_fields_fail_closed(tmp_path, monkeypatch, link_mode, reparse):
    path = tmp_path / "account.json"
    path.touch()
    original = Path.lstat

    def inspect(self):
        if self == path:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if link_mode else stat.S_IFREG,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024) if reparse else 0,
            )
        return original(self)

    monkeypatch.setattr(Path, "lstat", inspect)
    with pytest.raises(ValueError, match="linked"):
        safe_path(path)


def test_actual_directory_link_and_missing_child_are_rejected(tmp_path):
    target, link = tmp_path / "target", tmp_path / "alias"
    target.mkdir()
    marker = target / "account.json"
    marker.write_bytes(b"must-remain")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        for path in (link, link / "account.json", link / "missing.json"):
            with pytest.raises(ValueError, match="linked|noncanonical"):
                safe_path(path)
    finally:
        # Only this fixture's alias; removing the junction does not recurse.
        assert link.absolute().parent == target.parent == tmp_path
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
    assert marker.read_bytes() == b"must-remain"
