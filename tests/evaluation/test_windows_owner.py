"""Test-only default ownership is restored even after an exceptional exit."""
import os

import pytest

from tests.evaluation._windows_owner import account_default_owner


@pytest.mark.skipif(os.name != "nt", reason="Windows token ownership")
@pytest.mark.parametrize("fail", [False, True])
def test_process_default_owner_is_restored(tmp_path, fail):
    import ctypes as c
    from ctypes import wintypes as w

    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [c.c_void_p]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
    adv.ConvertSidToStringSidW.restype = w.BOOL

    def default_owner():
        token = w.HANDLE()
        assert adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token))
        try:
            length = w.DWORD()
            adv.GetTokenInformation(token, 4, None, 0, c.byref(length))
            buffer = c.create_string_buffer(length.value)
            assert adv.GetTokenInformation(token, 4, buffer, length, c.byref(length))
            sid = c.cast(buffer, c.POINTER(c.c_void_p)).contents.value
            text = c.c_void_p()
            assert adv.ConvertSidToStringSidW(sid, c.byref(text))
            try:
                return c.wstring_at(text.value)
            finally:
                kernel.LocalFree(text)
        finally:
            kernel.CloseHandle(token)

    before = default_owner()
    try:
        with account_default_owner():
            # The production check inspects both the user SID and real DACL.
            from tests.evaluation.test_targeted_authorized import private_fixture
            from offerpilot.evaluation.core_targeted_trust import require_owned
            root = tmp_path / "private"
            root.mkdir()
            private_fixture(root)
            child = root / "child"
            child.mkdir()
            target = child / "account.json"
            target.write_text("test only")
            require_owned(child)
            require_owned(target)
            if fail:
                raise RuntimeError("expected test exit")
    except RuntimeError as error:
        assert fail and str(error) == "expected test exit"
    assert default_owner() == before
