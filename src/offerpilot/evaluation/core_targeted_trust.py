"""Read-only local ownership checks for authority configuration; no ACL changes."""
from __future__ import annotations

import os
from pathlib import Path


def require_owned(path: Path) -> None:
    if os.name != "nt":
        info = path.stat()
        if info.st_uid != getattr(os, "getuid")() or info.st_mode & 0o022:
            raise ValueError("authority must be OS-account-owned and not writable by others")
        return
    import ctypes as c
    from ctypes import wintypes as w

    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, c.c_int, w.DWORD,
        c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.GetAce.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
    adv.GetAce.restype = w.BOOL
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.CloseHandle.argtypes = [w.HANDLE]

    def sid_text(pointer: int | None) -> str:
        text = c.c_void_p()
        if not pointer or not adv.ConvertSidToStringSidW(pointer, c.byref(text)):
            raise OSError("cannot inspect authority SID")
        try:
            assert text.value is not None
            return c.wstring_at(text.value)
        finally:
            kernel.LocalFree(text)

    token = w.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise OSError("cannot inspect authority owner")
    try:
        length = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, c.byref(length))
        buffer = c.create_string_buffer(length.value)
        if not adv.GetTokenInformation(token, 1, buffer, length, c.byref(length)):
            raise OSError("cannot inspect current SID")
        current = sid_text(c.cast(buffer, c.POINTER(c.c_void_p)).contents.value)
    finally:
        kernel.CloseHandle(token)
    owner, dacl, descriptor = c.c_void_p(), c.c_void_p(), c.c_void_p()
    if adv.GetNamedSecurityInfoW(str(path), 1, 5, c.byref(owner), None,
                               c.byref(dacl), None, c.byref(descriptor)):
        raise OSError("cannot inspect authority ACL")
    try:
        if sid_text(owner.value) != current or not dacl.value:
            raise ValueError("authority owner/null DACL invalid")
        # ACL header: revision(1), reserved(1), size(2), AceCount(2), reserved(2).
        count = c.c_ushort.from_address(dacl.value + 4).value
        for number in range(count):
            ace = c.c_void_p()
            if not adv.GetAce(dacl, number, c.byref(ace)) or not ace.value:
                raise OSError("cannot inspect authority ACE")
            kind = c.c_ubyte.from_address(ace.value).value
            flags = c.c_ubyte.from_address(ace.value + 1).value
            if kind not in {0, 5, 9, 11} or (flags & 8 and not path.is_dir()):
                continue  # deny/audit; inherit-only grants cannot affect a file
            # On a directory, inherit-only write grants affect future children.
            # Those children include claims/anchors that may not exist yet.
            mask = c.c_uint32.from_address(ace.value + 4).value
            if not mask & 0x500D0156:
                continue
            if kind != 0 or sid_text(ace.value + 8) not in {
                # OWNER RIGHTS applies only to the owner verified above.
                current, "S-1-5-18", "S-1-5-32-544", "S-1-3-0", "S-1-3-4"
            }:
                raise ValueError("authority writable by another principal")
    finally:
        kernel.LocalFree(descriptor)
