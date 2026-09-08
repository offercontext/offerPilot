import base64
import json
import os
import threading
import time
from pathlib import Path
from uuid import UUID

import pytest

import offerpilot.agent_runtime.keyring as journal_keyring
from offerpilot.agent_runtime.keyring import JOURNAL_KEY_FILENAME, load_or_create_journal_key


def test_journal_key_round_trips_from_dedicated_file(tmp_path: Path) -> None:
    first = load_or_create_journal_key(tmp_path)
    second = load_or_create_journal_key(tmp_path)

    assert first is not None
    assert second == first
    assert first.key_id == str(UUID(first.key_id))
    assert len(first.secret) == 32
    payload = json.loads((tmp_path / JOURNAL_KEY_FILENAME).read_text("utf-8"))
    assert payload == {
        "schema_version": 1,
        "key_id": first.key_id,
        "secret": base64.urlsafe_b64encode(first.secret).decode("ascii").rstrip("="),
    }


def test_journal_key_persist_failure_disables_journal_without_ephemeral_key(
    tmp_path: Path,
) -> None:
    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic write failure with private path")

    assert load_or_create_journal_key(tmp_path, replace_file=fail_replace) is None
    assert not (tmp_path / JOURNAL_KEY_FILENAME).exists()
    assert list(tmp_path.glob(f".{JOURNAL_KEY_FILENAME}.*.tmp")) == []


def test_invalid_existing_key_disables_journal(tmp_path: Path) -> None:
    (tmp_path / JOURNAL_KEY_FILENAME).write_text(
        json.dumps({"schema_version": 1, "key_id": "not-a-uuid", "secret": "bad"}),
        encoding="utf-8",
    )

    assert load_or_create_journal_key(tmp_path) is None


def test_posix_key_file_is_owner_only(tmp_path: Path) -> None:
    calls: list[tuple[Path, int]] = []
    domain = load_or_create_journal_key(
        tmp_path,
        platform_name="posix",
        chmod_file=lambda path, mode: calls.append((path, mode)),
    )

    assert domain is not None
    assert len(calls) == 2
    assert all(mode == 0o600 for _, mode in calls)
    assert calls[-1][0] == tmp_path.resolve() / JOURNAL_KEY_FILENAME


def test_windows_key_inherits_data_directory_acl_without_posix_chmod(tmp_path: Path) -> None:
    chmod_calls: list[tuple[Path, int]] = []

    domain = load_or_create_journal_key(
        tmp_path,
        platform_name="nt",
        chmod_file=lambda path, mode: chmod_calls.append((path, mode)),
    )

    assert domain is not None
    assert (tmp_path / JOURNAL_KEY_FILENAME).resolve().parent == tmp_path.resolve()
    assert chmod_calls == []


def test_existing_posix_key_is_restricted_before_use(tmp_path: Path) -> None:
    created = load_or_create_journal_key(tmp_path)
    assert created is not None
    calls: list[tuple[Path, int]] = []

    loaded = load_or_create_journal_key(
        tmp_path,
        platform_name="posix",
        chmod_file=lambda path, mode: calls.append((path, mode)),
    )

    assert loaded == created
    assert calls == [(tmp_path.resolve() / JOURNAL_KEY_FILENAME, 0o600)]


def test_concurrent_creation_never_returns_two_different_key_domains(tmp_path: Path) -> None:
    entered_replace = threading.Event()
    release_replace = threading.Event()
    results: list[object] = []

    def delayed_replace(source: Path, target: Path) -> None:
        entered_replace.set()
        assert release_replace.wait(timeout=2)
        os.replace(source, target)

    owner = threading.Thread(
        target=lambda: results.append(load_or_create_journal_key(tmp_path, replace_file=delayed_replace))
    )
    owner.start()
    assert entered_replace.wait(timeout=2)
    contender = load_or_create_journal_key(tmp_path)
    release_replace.set()
    owner.join(timeout=2)

    assert not owner.is_alive()
    persisted = load_or_create_journal_key(tmp_path)
    non_null = [item for item in [*results, contender, persisted] if item is not None]
    assert persisted is not None
    assert {item.key_id for item in non_null} == {persisted.key_id}  # type: ignore[attr-defined]


def test_stale_creation_lock_with_confirmed_dead_owner_is_reclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / f".{JOURNAL_KEY_FILENAME}.lock"
    lock_path.write_text(json.dumps({"pid": 424242}), encoding="ascii")
    old = time.time() - 120
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(
        journal_keyring,
        "_process_is_alive",
        lambda _pid, *, platform_name: False,
    )

    created = load_or_create_journal_key(tmp_path)

    assert created is not None
    assert (tmp_path / JOURNAL_KEY_FILENAME).exists()
    assert not lock_path.exists()


def test_stale_lock_without_valid_owner_metadata_is_not_deleted(tmp_path: Path) -> None:
    lock_path = tmp_path / f".{JOURNAL_KEY_FILENAME}.lock"
    lock_path.write_bytes(b"")
    old = time.time() - 120
    os.utime(lock_path, (old, old))

    assert load_or_create_journal_key(tmp_path) is None
    assert lock_path.exists()


def test_old_lock_owned_by_live_process_is_not_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":

        def fail_if_posix_probe_is_used(_pid: int, _signal: int) -> None:
            raise AssertionError("os.kill(pid, 0) terminates the target process on Windows")

        monkeypatch.setattr(journal_keyring.os, "kill", fail_if_posix_probe_is_used)
    lock_path = tmp_path / f".{JOURNAL_KEY_FILENAME}.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="ascii")
    old = time.time() - 120
    os.utime(lock_path, (old, old))

    assert load_or_create_journal_key(tmp_path) is None
    assert lock_path.exists()


def test_journal_key_does_not_swallow_base_exception(tmp_path: Path) -> None:
    def interrupt(_source: Path, _target: Path) -> None:
        raise KeyboardInterrupt

    try:
        load_or_create_journal_key(tmp_path, replace_file=interrupt)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt must propagate")


def test_default_platform_matches_os_name() -> None:
    assert os.name in {"nt", "posix"}
