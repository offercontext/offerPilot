"""TEMP spelling compatibility must not normalize away filesystem links."""
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts.real_ai_control import private


@pytest.mark.parametrize("component", ["base", "ancestor"])
@pytest.mark.parametrize("attribute", ["symlink", "reparse"])
def test_windows_temp_rejects_original_link_before_resolving(tmp_path, monkeypatch, component, attribute):
    base = tmp_path / "temp"
    base.mkdir()
    monkeypatch.setattr(private, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(private.tempfile, "gettempdir", lambda: str(base))
    original = Path.lstat
    linked = base if component == "base" else tmp_path

    def inspect(path, *args, **kwargs):
        if path == linked:
            return SimpleNamespace(st_mode=stat.S_IFLNK if attribute == "symlink" else stat.S_IFDIR,
                                   st_file_attributes=1024 if attribute == "reparse" else 0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspect)
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("resolved a linked TEMP"))
    with pytest.raises(ValueError, match="linked"):
        private._temp_base()


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError, NotADirectoryError])
def test_temp_inspection_errors_fail_closed(tmp_path, monkeypatch, error):
    monkeypatch.setattr(private, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(private.tempfile, "gettempdir", lambda: str(tmp_path))
    original = Path.lstat

    def inspect(path, *args, **kwargs):
        if path == tmp_path:
            raise error("cannot inspect TEMP")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspect)
    with pytest.raises(error, match="cannot inspect"):
        private._temp_base()


def test_windows_temp_canonicalizes_only_after_original_checks(tmp_path, monkeypatch):
    alias, canonical = tmp_path / "SHORT~1", tmp_path / "long-name"
    alias.mkdir()
    canonical.mkdir()
    monkeypatch.setattr(private, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(private.tempfile, "gettempdir", lambda: str(alias))
    original = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda p, *a, **k: canonical if p == alias else original(p, *a, **k))
    assert private._temp_base() == canonical
    run = private.work_path("offline-" + "c" * 16, tmp_path / "evidence")
    assert run.parent == canonical
    run.mkdir()
    config = run / "source-config/config.json"
    config.parent.mkdir()
    config.write_text("test only")
    result = private.remove_owned_config_copies(run, owned_tree_exited=True)
    assert result["removed"] == [str(config)]
    assert run.is_dir() and not config.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows short path API")
def test_real_windows_short_temp_spelling(tmp_path, monkeypatch):
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
    kernel.GetShortPathNameW.restype = w.DWORD
    base = tmp_path / "long-private-temp-directory"
    base.mkdir()
    buffer = c.create_unicode_buffer(32768)
    length = kernel.GetShortPathNameW(str(base), buffer, len(buffer))
    assert 0 < length < len(buffer), c.get_last_error()
    # Hosts with 8.3 disabled may return the long spelling. The synthetic case
    # above still exercises spelling changes; CI TEMP supplies the real alias.
    monkeypatch.setattr(private.tempfile, "gettempdir", lambda: buffer.value)
    assert private._temp_base() == base.resolve()
