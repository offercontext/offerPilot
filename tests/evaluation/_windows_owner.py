"""Temporarily use the test account, not its admin group, for new file ownership."""
from contextlib import contextmanager
import os


@contextmanager
def account_default_owner():
    if os.name != "nt":
        yield
        return
    import ctypes as c
    from ctypes import wintypes as w

    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.SetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    adv.SetTokenInformation.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL

    token = w.HANDLE()
    # QUERY | ADJUST_DEFAULT: this process only; no privileges or system policy changed.
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x88, c.byref(token)):
        raise c.WinError(c.get_last_error())

    def read(kind):
        size = w.DWORD()
        adv.GetTokenInformation(token, kind, None, 0, c.byref(size))
        if not size.value:
            raise c.WinError(c.get_last_error())
        buffer = c.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, kind, buffer, size, c.byref(size)):
            raise c.WinError(c.get_last_error())
        return buffer

    try:
        previous = read(4)  # TokenOwner; retain buffer containing the SID through restore.
        user = read(1)  # TokenUser starts with SID_AND_ATTRIBUTES.
        owner = c.c_void_p(c.cast(user, c.POINTER(c.c_void_p)).contents.value)
        if not adv.SetTokenInformation(token, 4, c.byref(owner), c.sizeof(owner)):
            raise c.WinError(c.get_last_error())
        try:
            yield
        finally:
            if not adv.SetTokenInformation(token, 4, previous, c.sizeof(c.c_void_p)):
                raise c.WinError(c.get_last_error())
    finally:
        if not kernel.CloseHandle(token):
            raise c.WinError(c.get_last_error())
