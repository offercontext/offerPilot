"""Lifetime process leases on a stable inode outside replaceable workspace data."""

from __future__ import annotations

import atexit
import os
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_guard = threading.RLock()
_leases: dict[Path, int] = {}


def _lock(fd: int, *, exclusive: bool, unlock: bool = False) -> None:
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                        ("hEvent", wintypes.HANDLE)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        overlap = Overlapped()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        if unlock:
            function = kernel.UnlockFileEx
            function.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.POINTER(Overlapped)]
            function.restype = wintypes.BOOL
            ok = function(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            function = kernel.LockFileEx
            function.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            function.restype = wintypes.BOOL
            ok = function(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlap))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        import fcntl

        flags = fcntl.LOCK_UN if unlock else (
            (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        )
        fcntl.flock(fd, flags)


def _open(path: Path) -> int:
    if path.is_symlink():
        raise ValueError("unsafe_workspace_lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("unsafe_workspace_lock")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def exclusive_control(root: Path) -> Iterator[None]:
    """Serialize restore control and lease admission without replacing the lock file."""
    root = root.resolve()
    with _guard:
        fd = _open(root.parent / f".{root.name}.restore-control.lock")
        try:
            _lock(fd, exclusive=True)
            yield
        finally:
            os.close(fd)


def acquire_workspace(root: Path, *, exclusive: bool = False) -> int:
    """Caller holds admission lock; close returned descriptor to release ownership."""
    fd = _open(root.parent / f".{root.name}.workspace.lock")
    try:
        _lock(fd, exclusive=exclusive)
        return fd
    except BaseException:
        os.close(fd)
        raise


def retain_shared(root: Path, fd: int) -> None:
    """Downgrade only while admission is held, including Windows unlock/relock gap."""
    _lock(fd, exclusive=False, unlock=True)
    _lock(fd, exclusive=False)
    _leases[root.resolve()] = fd


def has_lease(root: Path) -> bool:
    return root.resolve() in _leases


@atexit.register
def _close_leases() -> None:
    for fd in _leases.values():
        os.close(fd)
    _leases.clear()
