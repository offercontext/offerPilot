"""One real-mail runtime per SQLite path, enforced by crash-released OS locks.

The lock file contains no credential or user data. Never unlink it: replacing its
inode would allow a second process to lock a different file under the same name.
There is deliberately no existence-file / soft-lock fallback.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO


def _acquire_native_lock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    elif os.name == "posix":
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise OSError("native runtime lock unavailable")


class LocalMailProcessLock:
    def __init__(self, handle: BinaryIO):
        self._handle: BinaryIO | None = handle

    @classmethod
    def acquire(cls, database_path: str) -> LocalMailProcessLock | None:
        handle: BinaryIO | None = None
        try:
            if not database_path or database_path == ":memory:":
                return None
            database = Path(database_path).resolve(strict=True)
            path = database.with_name(database.name + ".job-mail.lock")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("invalid native lock file")
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
            _acquire_native_lock(handle)
            return cls(handle)
        except (OSError, ValueError, ImportError):
            if handle is not None:
                handle.close()
            return None

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            # Both flock and Windows byte locks are released on descriptor close,
            # including process termination. Keep the stable on-disk inode.
            handle.close()
