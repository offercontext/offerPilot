"""Standalone standard-library native-lock checks; suitable for Linux/Windows."""
import os
import subprocess
import sys

from offerpilot.job_mail.process_lock import LocalMailProcessLock


def test_native_lock_excludes_independent_process_and_release_allows_reopen(tmp_path):
    database = tmp_path / "data.db"
    database.touch()
    owner = LocalMailProcessLock.acquire(str(database))
    assert owner is not None
    script = "from offerpilot.job_mail.process_lock import LocalMailProcessLock as L; import sys; x=L.acquire(sys.argv[1]); print('locked' if x else 'busy'); x and x.close()"
    def child():
        return subprocess.check_output([sys.executable, "-c", script, str(database)], text=True).strip()
    assert child() == "busy"
    assert LocalMailProcessLock.acquire(str(database)) is None
    owner.close()
    owner.close()
    assert child() == "locked"
    assert database.with_name("data.db.job-mail.lock").read_bytes() == b"\0"


def test_process_crash_releases_native_lock_without_deleting_file(tmp_path):
    database = tmp_path / "data.db"
    database.touch()
    script = "from offerpilot.job_mail.process_lock import LocalMailProcessLock as L; import sys,os; x=L.acquire(sys.argv[1]); print('locked' if x else 'busy',flush=True); os._exit(7)"
    result = subprocess.run([sys.executable, "-c", script, str(database)], capture_output=True, text=True)
    assert result.returncode == 7 and result.stdout.strip() == "locked"
    lock = LocalMailProcessLock.acquire(str(database))
    assert lock is not None
    lock.close()


def test_unsupported_or_failed_native_lock_is_closed_without_soft_fallback(tmp_path, monkeypatch):
    database = tmp_path / "data.db"
    database.touch()
    def fail(handle):
        raise OSError("not supported")
    monkeypatch.setattr("offerpilot.job_mail.process_lock._acquire_native_lock", fail)
    assert LocalMailProcessLock.acquire(str(database)) is None
    assert LocalMailProcessLock.acquire(":memory:") is None
    assert LocalMailProcessLock.acquire(str(tmp_path / "missing.db")) is None


def test_posix_symlink_lock_path_fails_closed(tmp_path):
    if os.name != "posix":
        return
    database = tmp_path / "data.db"
    database.touch()
    target = tmp_path / "unrelated.txt"
    target.write_text("preserve")
    database.with_name("data.db.job-mail.lock").symlink_to(target)
    assert LocalMailProcessLock.acquire(str(database)) is None
    assert target.read_text() == "preserve"
