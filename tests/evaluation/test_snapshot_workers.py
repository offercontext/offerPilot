"""Fresh file reads retain serial snapshot semantics and close every worker."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
from threading import Event, Lock, current_thread

import pytest

from offerpilot.evaluation import runner


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*args):
        subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={hooks}",
                        "-c", "user.name=Snapshot test", "-c", "user.email=snapshot@example.test",
                        *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init")
    (repo / "uv.lock").write_bytes(b"version = 1\n")
    for index in range(8):
        (repo / f"a{index}.txt").write_bytes(f"value-{index}".encode())
    git("add", ".")
    git("commit", "-m", "fixture")
    (repo / "空 格.txt").write_bytes(b"untracked")
    return repo


def serial_snapshot(checkout):
    tracked = runner.git(checkout, "ls-files", "-z").split(b"\0")
    untracked = runner.git(checkout, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    files = {}
    for raw in sorted(set(tracked + untracked) - {b""}):
        name = os.fsdecode(raw).replace("\\", "/")
        path = checkout / name
        files[name] = runner.file_hash(path) if path.is_file() else "missing"
    return {"commit": runner.git(checkout, "rev-parse", "HEAD").decode().strip(),
            "diff_hash": runner.digest([runner.git(checkout, "diff", "--binary", "HEAD").hex(), files]),
            "lock_hash": runner.file_hash(checkout / "uv.lock"), "files_hash": runner.digest(files)}


@pytest.mark.parametrize("state", ["regular", "missing", "directory"])
def test_snapshot_matches_original_serial_dictionary(checkout, state):
    if state != "regular":
        (checkout / "a0.txt").unlink()
    if state == "directory":
        (checkout / "a0.txt").mkdir()
    assert runner.snapshot(checkout) == serial_snapshot(checkout)


def test_every_snapshot_reads_changed_bytes_even_with_same_stat_metadata(checkout):
    before = runner.snapshot(checkout)
    path = checkout / "a0.txt"
    previous = path.stat()
    path.write_bytes(b"changed")  # Same length as value-0; restore the original mtime.
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    after = runner.snapshot(checkout)
    assert after == serial_snapshot(checkout)
    assert before["files_hash"] != after["files_hash"]
    assert before["diff_hash"] != after["diff_hash"]


def test_four_file_workers_join_before_head_diff_and_final_lock_hash(checkout, monkeypatch):
    original_hash, original_stat, original_git = runner.file_hash, Path.is_file, runner.git
    first_four, release = Event(), Event()
    lock = Lock()
    active = peak = 0
    entered, threads, hashes, stats, git_calls = set(), set(), Counter(), Counter(), []

    def stat(path):
        if path.parent == checkout:
            with lock:
                stats[path.name] += 1
        return original_stat(path)

    def hash_file(path):
        nonlocal active, peak
        with lock:
            hashes[path.name] += 1
            count = hashes[path.name]
        if path.name == "uv.lock" and count == 2:
            assert active == 0
            assert all(not thread.is_alive() for thread in threads)
            return original_hash(path)
        with lock:
            threads.add(current_thread())
            active += 1
            peak = max(peak, active)
            entered.add(path.name)
            if {f"a{i}.txt" for i in range(4)} <= entered:
                first_four.set()
        try:
            if path.name in {f"a{i}.txt" for i in range(4)}:
                assert release.wait(5)
            return original_hash(path)
        finally:
            with lock:
                active -= 1

    def git(path, *args):
        git_calls.append(args)
        if args[0] != "ls-files":
            assert active == 0 and all(not thread.is_alive() for thread in threads)
        return original_git(path, *args)

    monkeypatch.setattr(Path, "is_file", stat)
    monkeypatch.setattr(runner, "file_hash", hash_file)
    monkeypatch.setattr(runner, "git", git)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(runner.snapshot, checkout)
        try:
            assert first_four.wait(5), "file reads did not overlap with four workers"
            assert not future.done()
        finally:
            release.set()
        future.result(timeout=10)
    assert active == 0 and peak == 4 and len(threads) == 4
    assert stats == Counter({**{f"a{i}.txt": 1 for i in range(8)}, "uv.lock": 1, "空 格.txt": 1})
    assert hashes == stats + Counter({"uv.lock": 1})
    assert git_calls == [("ls-files", "-z"), ("ls-files", "--others", "--exclude-standard", "-z"),
                         ("rev-parse", "HEAD"), ("diff", "--binary", "HEAD")]


def test_first_sorted_error_keeps_identity_and_cause_after_all_workers_join(checkout, monkeypatch):
    original_hash, original_git = runner.file_hash, runner.git
    started = [Event() for _ in range(3)]
    ended = [Event() for _ in range(3)]
    release_first, release_peer = Event(), Event()
    cause = OSError("existing explicit cause")
    primary, secondary = ValueError("first sorted path"), RuntimeError("earlier completion")
    primary.__cause__ = cause
    threads, completed, git_calls = set(), set(), []
    lock = Lock()

    def hash_file(path):
        name = path.name
        with lock:
            threads.add(current_thread())
        index = int(name[1]) if name in {"a0.txt", "a1.txt", "a2.txt"} else None
        try:
            if index is not None:
                started[index].set()
            if index == 0:
                assert release_first.wait(5)
                raise primary
            if index == 1:
                raise secondary
            if index == 2:
                assert release_peer.wait(5)
            return original_hash(path)
        finally:
            with lock:
                completed.add(name)
            if index is not None:
                ended[index].set()

    def git(path, *args):
        git_calls.append(args)
        return original_git(path, *args)

    monkeypatch.setattr(runner, "file_hash", hash_file)
    monkeypatch.setattr(runner, "git", git)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(runner.snapshot, checkout)
        try:
            assert all(event.wait(5) for event in started)
            assert ended[1].wait(5) and not ended[0].is_set()
            release_first.set()
            assert ended[0].wait(5) and not ended[2].is_set()
            assert not future.done(), "first failure escaped before blocked work joined"
            assert all(args[0] == "ls-files" for args in git_calls)
        finally:
            release_first.set()
            release_peer.set()
        with pytest.raises(ValueError) as raised:
            future.result(timeout=10)
    assert raised.value is primary and raised.value.__cause__ is cause
    assert all(not thread.is_alive() for thread in threads)
    assert completed == {*(f"a{i}.txt" for i in range(8)), "uv.lock", "空 格.txt"}
    assert all(args[0] == "ls-files" for args in git_calls)
