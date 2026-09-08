from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

JOURNAL_KEY_FILENAME = "agent-journal-key.json"


@dataclass(frozen=True)
class JournalKeyDomain:
    key_id: str
    secret: bytes


def _decode_secret(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("invalid journal secret")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if len(decoded) != 32:
        raise ValueError("invalid journal secret length")
    return decoded


def _read_key(path: Path) -> JournalKeyDomain:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "key_id", "secret"}:
        raise ValueError("invalid journal key payload")
    if payload["schema_version"] != 1 or not isinstance(payload["key_id"], str):
        raise ValueError("invalid journal key schema")
    parsed_id = UUID(payload["key_id"])
    key_id = str(parsed_id)
    if payload["key_id"] != key_id:
        raise ValueError("journal key id must be canonical")
    return JournalKeyDomain(key_id=key_id, secret=_decode_secret(payload["secret"]))


def load_or_create_journal_key(
    data_dir: Path,
    *,
    replace_file: Callable[[Path, Path], None] = os.replace,
    platform_name: str = os.name,
    chmod_file: Callable[[Path, int], None] = os.chmod,
) -> JournalKeyDomain | None:
    """Return the durable Journal key domain, or disable journaling on ordinary failure."""

    key_path = data_dir.resolve() / JOURNAL_KEY_FILENAME
    lock_path = key_path.with_name(f".{JOURNAL_KEY_FILENAME}.lock")
    temp_path: Path | None = None
    owns_lock = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            if platform_name != "nt":
                chmod_file(key_path, 0o600)
            return _read_key(key_path)

        for lock_attempt in range(2):
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if key_path.exists():
                    if platform_name != "nt":
                        chmod_file(key_path, 0o600)
                    return _read_key(key_path)
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 30.0
                except OSError:
                    stale = False
                if stale and _lock_owner_alive(
                    lock_path,
                    platform_name=platform_name,
                ) is False and lock_attempt == 0:
                    lock_path.unlink(missing_ok=True)
                    continue
                return None
            else:
                owns_lock = True
                try:
                    os.write(
                        lock_fd,
                        json.dumps({"pid": os.getpid()}, separators=(",", ":")).encode(
                            "ascii"
                        ),
                    )
                    os.fsync(lock_fd)
                finally:
                    os.close(lock_fd)
                break
        if not owns_lock:
            return None

        domain = JournalKeyDomain(key_id=str(uuid4()), secret=secrets.token_bytes(32))
        payload = {
            "schema_version": 1,
            "key_id": domain.key_id,
            "secret": base64.urlsafe_b64encode(domain.secret).decode("ascii").rstrip("="),
        }
        temp_path = key_path.with_name(f".{JOURNAL_KEY_FILENAME}.{uuid4().hex}.tmp")
        if platform_name != "nt":
            temp_path.touch(mode=0o600, exist_ok=False)
            chmod_file(temp_path, 0o600)
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temp_path, key_path)
        temp_path = None
        if platform_name != "nt":
            chmod_file(key_path, 0o600)
        return domain
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return None
    finally:
        if owns_lock:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _lock_owner_alive(lock_path: Path, *, platform_name: str) -> bool | None:
    try:
        value = json.loads(lock_path.read_text(encoding="ascii"))
        pid = value.get("pid") if type(value) is dict else None
        if type(pid) is not int or pid <= 0:
            return None
    except (OSError, ValueError):
        return None
    return _process_is_alive(pid, platform_name=platform_name)


def _process_is_alive(pid: int, *, platform_name: str) -> bool | None:
    if platform_name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _windows_process_is_alive(pid: int) -> bool | None:
    process_synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    error_invalid_parameter = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    handle = open_process(process_synchronize, 0, pid)
    if not handle:
        return False if ctypes.get_last_error() == error_invalid_parameter else None

    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    try:
        wait_result = wait_for_single_object(handle, 0)
        if wait_result == wait_object_0:
            return False
        if wait_result == wait_timeout:
            return True
        return None
    finally:
        close_handle(handle)
