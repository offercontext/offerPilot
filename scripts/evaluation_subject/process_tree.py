"""Neutral process ownership for isolated developer commands, Windows and POSIX."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from typing import Any


class ProcessTree:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.job: Any = None
        self.kernel: Any = None
        self.watcher: Any = None
        self.watch_fd: int | None = None
        if os.name != "nt":
            self.process = subprocess.Popen(argv, start_new_session=True, **kwargs)
            read_fd, self.watch_fd = os.pipe()
            monitor = ("import os,sys,signal; fd=int(sys.argv[1]); pg=int(sys.argv[2]); "
                       "os.read(fd,1); os.close(fd);\n"
                       "try: os.killpg(pg,signal.SIGKILL)\nexcept ProcessLookupError: pass\n")
            try:
                self.watcher = subprocess.Popen([sys.executable, "-c", monitor, str(read_fd),
                    str(self.process.pid)], pass_fds=(read_fd,), start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except BaseException:
                self.kill()
                self.process.wait(timeout=5)
                os.close(self.watch_fd)
                self.watch_fd = None
                raise
            finally:
                os.close(read_fd)
            return
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if job:
                kernel.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "cannot create isolated process job")
        self.job, self.kernel = job, kernel
        try:
            # A suspended process cannot create descendants before assignment.
            self.process = subprocess.Popen(argv, creationflags=0x4, **kwargs)
            handle = getattr(self.process, "_handle")
            if not kernel.AssignProcessToJobObject(job, handle):
                self.process.kill()
                self.process.wait(timeout=5)
                raise OSError(ctypes.get_last_error(), "cannot assign process ownership")
            ntdll = ctypes.WinDLL("ntdll")
            ntdll.NtResumeProcess.argtypes = [w.HANDLE]
            if ntdll.NtResumeProcess(handle) != 0:
                raise OSError("cannot resume isolated process")
        except BaseException:
            kernel.CloseHandle(job)
            self.job = None
            raise

    def kill(self) -> None:
        if self.job:
            if not self.kernel.TerminateJobObject(self.job, 3):
                raise OSError(ctypes.get_last_error(), "cannot terminate process job")
        else:
            try:
                getattr(os, "killpg")(self.process.pid, getattr(signal, "SIGKILL"))
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        if self.watch_fd is not None:
            os.close(self.watch_fd)
            self.watch_fd = None
            self.watcher.wait(timeout=5)

    def wait(self, timeout: float) -> int:
        result = self.process.wait(timeout=timeout)
        if os.name != "nt":
            limit = time.monotonic() + min(timeout, 5)
            while self._posix_group_alive():
                if time.monotonic() >= limit:
                    raise TimeoutError("owned POSIX descendants did not exit")
                time.sleep(0.02)
        if os.name == "nt" and self.job:
            from ctypes import wintypes as w
            class Accounting(ctypes.Structure):
                _fields_ = [(n, ctypes.c_int64) for n in ("user", "kernel", "period_user", "period_kernel")] + [
                    (n, w.DWORD) for n in ("faults", "total", "active", "terminated")]
            self.kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
            limit = time.monotonic() + min(timeout, 5)
            while True:
                counts = Accounting()
                if not self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(counts), ctypes.sizeof(counts), None):
                    raise OSError("cannot verify process job exit")
                if counts.active == 0:
                    break
                if time.monotonic() >= limit:
                    raise TimeoutError("owned descendants did not exit")
                time.sleep(0.02)
        return result

    def _posix_group_alive(self) -> bool:
        if os.path.isdir("/proc"):
            for name in os.listdir("/proc"):
                if not name.isdigit():
                    continue
                try:
                    with open(f"/proc/{name}/stat", encoding="utf-8") as stream:
                        fields = stream.read().rsplit(")", 1)[1].split()
                    if int(fields[2]) == self.process.pid and fields[0] != "Z":
                        return True
                except (OSError, ValueError, IndexError):
                    continue
            return False
        try:
            getattr(os, "killpg")(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
