"""Command-scoped reuse must retain byte, topology and authority freshness."""
import json
import hashlib
import os
import subprocess
import sys

import pytest

from offerpilot.evaluation.core_release_reads import CommandReads, DependencyClosure
from offerpilot.evaluation import core_release_reads as reads
from offerpilot.evaluation.runner import snapshot
from offerpilot.evaluation.registry import digest
from tests.evaluation.test_core_refresh_paths import directory_alias
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_governance import rows


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path):
    run = tmp_path / "sealed-run"
    private = run / "private" / "one"
    private.mkdir(parents=True)
    (private / "answer.txt").write_bytes(b"first")
    (private / ".eval-trial.json").write_bytes(b"trial")
    (private / "empty").mkdir()
    write(run / "run-manifest.json", {"variants": {}})
    write(run / "trial-results.json", [])
    source = write(tmp_path / "authority.json", {"kind": "test-record"})
    gate = write(tmp_path / "gate.json", {"frozen_review": {"independent_reviews": [
        {"record_ref": str(source), "record_hash": "0" * 64}
    ]}})
    store = tmp_path / "store"
    write(store / "records" / "b.json", {
        "run_directory": str(run), "sources": [{"directory": str(run)}],
        "gate_path": str(gate), "approval": {"record_ref": str(source)},
    })
    write(store / "current.json", {"baseline_id": "b"})
    return store, run, private, source, gate


def test_reuses_full_readers_only_in_one_command_and_returns_copies(inputs):
    store, run, *_ = inputs
    calls = []
    def baseline(*_):
        calls.append("baseline")
        return {"sources": ["immutable"]}
    def strict(*_):
        calls.append("run")
        return ({"manifest": True}, ["immutable"])
    command = CommandReads()
    first = command.baseline(store, "b", baseline)
    first["sources"].append("caller mutation")
    assert command.baseline(store, "b", baseline) == {"sources": ["immutable"]}
    command.run(run, strict)[1].append("caller mutation")
    assert command.run(run, strict)[1] == ["immutable"]
    command.check()
    assert calls == ["baseline", "run"]
    other = CommandReads()
    other.baseline(store, "b", baseline)
    other.run(run, strict)
    assert calls == ["baseline", "run", "baseline", "run"]


@pytest.mark.parametrize("mutation", [
    "rewrite", "delete", "new_file", "new_empty_dir", "delete_empty_dir", "marker",
    "current", "authority", "gate", "event", "new_dependency",
])
@pytest.mark.parametrize("boundary", ["hit", "receipt"])
def test_changes_after_full_validation_are_rejected(inputs, mutation, boundary):
    store, run, private, source, gate = inputs
    command = CommandReads()
    calls = []
    command.baseline(store, "b", lambda *_: calls.append("strict") or {"ok": True})
    if mutation == "rewrite":
        path = private / "answer.txt"
        before = path.stat()
        path.write_bytes(b"other")  # Same length; restore mtime deliberately.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif mutation == "delete":
        (private / "answer.txt").unlink()
    elif mutation == "new_file":
        (private / "unexpected").touch()
    elif mutation == "new_empty_dir":
        (private / "unexpected-empty").mkdir()
    elif mutation == "delete_empty_dir":
        (private / "empty").rmdir()
    elif mutation == "marker":
        (private / ".eval-trial.json").write_bytes(b"other")
    elif mutation == "current":
        write(store / "current.json", {"baseline_id": "other"})
    elif mutation == "authority":
        write(source, {"kind": "changed"})
    elif mutation == "gate":
        write(gate, {"frozen_review": {}})
    elif mutation == "event":
        write(store / "events" / "new.json", {"action": "revoke"})
    else:
        write(store / "records" / "b.json", {"run_directory": str(run.parent / "other")})
    with pytest.raises(ValueError, match="changed|linked|unavailable"):
        if boundary == "hit":
            command.baseline(store, "b", lambda *_: pytest.fail("must not revalidate mutated input"))
        else:
            command.check()
    assert calls == ["strict"]


def test_full_reader_failure_is_not_cached(inputs):
    store, *_ = inputs
    command = CommandReads()
    def rejected(*_):
        raise ValueError("strict evidence failure")
    with pytest.raises(ValueError, match="strict evidence failure"):
        command.baseline(store, "b", rejected)
    assert command.baseline(store, "b", lambda *_: {"fresh": True}) == {"fresh": True}


def test_mutation_during_first_full_read_is_rejected(inputs):
    store, _, private, *_ = inputs
    def changed(*_):
        (private / "answer.txt").write_bytes(b"other")
        return {"ok": True}
    with pytest.raises(ValueError, match="changed"):
        CommandReads().baseline(store, "b", changed)


def test_arbitrary_trial_text_is_not_followed_as_authority(inputs, tmp_path):
    _, run, private, *_ = inputs
    write(private / "fragment-calls.json", {
        "content": {"record_ref": str(tmp_path / "must-not-read-secret"), "record_hash": "0" * 64}
    })
    closure = DependencyClosure.for_run(run)
    closure.check()


def test_file_symlink_replacement_is_rejected(inputs, tmp_path):
    store, _, private, *_ = inputs
    command = CommandReads()
    command.baseline(store, "b", lambda *_: {"ok": True})
    path = private / "answer.txt"
    original = path.read_bytes()
    external = tmp_path / "external.txt"
    external.write_bytes(original)
    probe = tmp_path / "link-probe"
    try:
        probe.symlink_to(external)
    except OSError:
        pytest.skip("platform does not permit symlink creation")
    probe.unlink()
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="linked|changed"):
        command.check()


def test_plain_text_original_approval_is_pinned_without_json_requirement(inputs):
    store, _, _, source, _ = inputs
    source.write_text("Maintainer approved these frozen inputs.", encoding="utf-8")
    command = CommandReads()
    command.baseline(store, "b", lambda *_: {"ok": True})
    source.write_text("Changed maintainer text.", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        command.check()


def test_rejected_manifest_never_starts_target_probe(inputs, monkeypatch):
    store, run, *_ = inputs
    write(run / "run-manifest.json", {"variants": {"candidate": {
        "checkout": str(run), "interpreter": sys.executable,
        "commit": "untrusted", "diff_hash": "untrusted", "files_hash": "untrusted",
        "lock_hash": "untrusted", "runtime": {"untrusted": True},
    }}})
    monkeypatch.setattr(reads, "_source", lambda *_: pytest.fail("untrusted target executed"))
    monkeypatch.setattr(reads, "target_environment", lambda *_: pytest.fail("untrusted program executed"))
    def strict(*_):
        raise ValueError("strict rejected forged manifest")
    with pytest.raises(ValueError, match="strict rejected"):
        CommandReads().baseline(store, "b", strict)


@pytest.fixture
def subject(tmp_path):
    checkout = tmp_path / "subject"
    checkout.mkdir()
    write(checkout / "uv.lock", {"version": 1})
    source = checkout / "code.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "fixture"], cwd=checkout, check=True, capture_output=True)
    return checkout, source


def test_actual_source_branch_checks_runtime_and_bytes(subject, monkeypatch):
    checkout, source = subject
    runtime = {"dependencies": {"test": "1"}}
    monkeypatch.setattr(reads, "target_environment", lambda variant: dict(runtime))
    target = dict(checkout=str(checkout), interpreter=sys.executable,
        **snapshot(checkout), runtime=dict(runtime))
    closure = DependencyClosure()
    closure.source(target)
    closure.freeze()
    closure.activate_sources()
    closure.check()
    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source/runtime changed"):
        closure.check()


def test_runtime_change_does_not_adopt_new_value(subject, monkeypatch):
    checkout, _ = subject
    runtime = {"version": 1}
    monkeypatch.setattr(reads, "target_environment", lambda variant: dict(runtime))
    target = dict(checkout=str(checkout), interpreter=sys.executable,
        **snapshot(checkout), runtime={"version": 1})
    closure = DependencyClosure()
    closure.source(target)
    closure.freeze()
    closure.activate_sources()
    runtime["version"] = 2
    with pytest.raises(ValueError, match="source/runtime changed"):
        closure.check()


def test_source_link_replaced_during_snapshot_is_rejected(subject, monkeypatch, tmp_path):
    checkout, source = subject
    external = tmp_path / "external.py"
    external.write_bytes(source.read_bytes())
    probe = tmp_path / "probe"
    try:
        probe.symlink_to(external)
    except OSError:
        pytest.skip("platform does not permit symlink creation")
    probe.unlink()
    original_snapshot = reads._guard_snapshot
    def replace_after_first_node_check(root):
        result = original_snapshot(root)
        source.unlink()
        source.symlink_to(external)
        return result
    monkeypatch.setattr(reads, "_guard_snapshot", replace_after_first_node_check)
    monkeypatch.setattr(reads, "target_environment", lambda *_: {})
    with pytest.raises(ValueError, match="linked|changed"):
        reads._source(dict(checkout=str(checkout), interpreter=sys.executable))


def test_union_rejects_conflicting_frozen_bytes(inputs):
    _, run, private, *_ = inputs
    first = DependencyClosure.for_run(run)
    (private / "answer.txt").write_bytes(b"other")
    second = DependencyClosure.for_run(run)
    with pytest.raises(ValueError, match="expectations changed"):
        DependencyClosure.union([first, second])


def test_persistent_ancestor_link_during_sampling_is_rejected(tmp_path, monkeypatch):
    subject = tmp_path / "fresh-subject"
    subject.mkdir()
    product = subject / "product.py"
    product.write_text("same content", encoding="utf-8")
    probe = tmp_path / "probe-directory"
    try:
        probe.symlink_to(subject, target_is_directory=True)
    except OSError:
        pytest.skip("platform does not permit directory symlink creation")
    probe.unlink()
    closure = DependencyClosure()
    closure.path(product)
    closure.freeze()
    original_sample = closure._sample
    def changed():
        result = original_sample()
        moved = tmp_path / "moved-subject"
        subject.rename(moved)
        subject.symlink_to(moved, target_is_directory=True)
        return result
    monkeypatch.setattr(closure, "_sample", changed)
    with pytest.raises(ValueError, match="linked|topology changed"):
        closure.check()


@pytest.fixture
def generation(inputs, tmp_path):
    store, _, _, _, _ = inputs
    def ref(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    fresh = tmp_path / "fresh-run"
    write(fresh / "run-manifest.json", {"variants": {}})
    write(fresh / "trial-results.json", [])
    controls = {}
    for name in ("policy", "experiment", "measurement", "historical_proof", "native_receipt",
        "history_approval", "approval_source", "review_source"):
        controls[name] = write(tmp_path / (name + ".json"), {"synthetic": name})
    controls["review"] = write(tmp_path / "refresh-review.json", {"independent_reviews": [
        {"record_ref": str(controls["review_source"]), "record_hash": "0" * 64}
    ]})
    controls["gate"] = write(tmp_path / "refresh-gate.json", {
        "frozen_review": json.loads(controls["review"].read_bytes())})
    controls["history_descriptor"] = write(tmp_path / "history-descriptor.json", {
        "native_receipt": ref(controls["native_receipt"]), "approval_file": ref(controls["history_approval"])
    })
    controls["approval"] = write(tmp_path / "refresh-approval.json", {
        "record_ref": str(controls["approval_source"]), "record_hash": "0" * 64})
    request = {"original_record": ref(store / "records/b.json"), "run_directory": str(fresh),
        "raw_files": {"run-manifest.json": ref(fresh / "run-manifest.json")}}
    request.update({key: ref(controls[key]) for key in ("policy", "review", "experiment", "measurement",
        "historical_proof", "history_descriptor", "gate")})
    controls["request"] = write(tmp_path / "refresh-request.json", {"request": request})
    controls["generation_record"] = write(store / "evaluator-refreshes/g.json", {
        "generation_id": "g", "request": ref(controls["request"]),
        "approval_file": ref(controls["approval"]),
        "approval": json.loads(controls["approval"].read_bytes()),
    })
    controls["head"] = write(store / "evidence-generations/b.json", {"generation_id": "g"})
    return store, controls


@pytest.mark.parametrize("target", ["request", "approval", "approval_source", "review", "review_source",
    "history_descriptor", "native_receipt", "history_approval", "measurement", "historical_proof",
    "generation_record", "head"])
def test_active_generation_and_transitive_authority_bytes_are_guarded(generation, target):
    store, controls = generation
    command = CommandReads()
    command.baseline(store, "b", lambda *_: {"ok": True})
    path = controls[target]
    previous = path.stat()
    path.write_bytes(b"x" * previous.st_size)
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        command.baseline(store, "b", lambda *_: pytest.fail("must not trust changed authority"))


def test_new_store_member_during_discovery_is_rejected(inputs, monkeypatch):
    store, *_ = inputs
    original = DependencyClosure._original
    def changed(closure, record):
        original(closure, record)
        write(store / "events/new.json", {"action": "unseen generation"})
    monkeypatch.setattr(DependencyClosure, "_original", changed)
    with pytest.raises(ValueError, match="changed during discovery"):
        CommandReads().baseline(store, "b", lambda *_: pytest.fail("discovery was not stable"))


def test_source_rejects_nonregular_nodes(subject, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no POSIX FIFO fixture")
    checkout, source = subject
    # Git omits untracked FIFOs. Replace an actual protected member instead.
    source.unlink()
    os.mkfifo(source)
    assert b"code.py" in reads._source_names(checkout)
    original_hash = reads.file_hash
    def guarded_hash(path):
        if path == source:
            pytest.fail("FIFO must be rejected before opening its bytes")
        return original_hash(path)
    monkeypatch.setattr(reads, "file_hash", guarded_hash)
    with pytest.raises(ValueError, match="node unavailable"):
        reads._source_nodes(checkout)
    with pytest.raises(ValueError, match="node unavailable"):
        reads._guard_snapshot(checkout)


def test_original_only_other_arm_interpreter_ancestor_change_is_rejected(
    inputs, subject, tmp_path, monkeypatch
):
    store, run, private, *_ = inputs
    checkout, _ = subject
    program_root = tmp_path / "original-other-arm-interpreter"
    program_root.mkdir()
    interpreter = program_root / "python.exe"
    interpreter.write_bytes(b"synthetic program; must never execute")
    target = dict(checkout=str(checkout), interpreter=sys.executable,
        **snapshot(checkout), runtime={})
    write(run / "run-manifest.json", {"variants": {"candidate": target, "baseline": {
        "checkout": str(tmp_path / "original-other-arm"), "interpreter": str(interpreter)}}})
    write(run / "trial-results.json", [{"execution_status": "completed", "private_directory": str(private)}])
    write(private / "fragment-facts.json", {"value": {"handshakes": [{"interpreter": str(interpreter)}]}})
    observed = []
    def runtime(variant):
        observed.append(variant.interpreter)
        assert variant.interpreter != str(interpreter)
        return {}
    monkeypatch.setattr(reads, "target_environment", runtime)
    command = CommandReads()
    command.baseline(store, "b", lambda *_: {"ok": True})
    moved = tmp_path / "original-other-arm-moved"
    program_root.rename(moved)
    with directory_alias(program_root, moved):
        with pytest.raises(ValueError, match="linked|topology changed"):
            command.check()
    assert observed and all(value != str(interpreter) for value in observed)


@pytest.mark.parametrize("method", ["path", "resolve_path"])
def test_duplicate_path_discovery_still_rechecks_at_freeze(tmp_path, method):
    folder = tmp_path / "initial-parent"
    folder.mkdir()
    program = folder / "program.exe"
    program.write_bytes(b"same bytes")
    closure = DependencyClosure()
    discover = getattr(closure, method)
    discover(program)
    moved = tmp_path / "moved-parent"
    folder.rename(moved)
    with directory_alias(folder, moved):
        discover(program)  # Hits the same-discovery deduplication path.
        assert program.read_bytes() == b"same bytes"
        with pytest.raises(ValueError, match="linked|topology changed"):
            closure.freeze()


def test_duplicate_file_discovery_still_rehashes_before_freeze(tmp_path):
    record = tmp_path / "record.txt"
    record.write_bytes(b"first")
    before = record.stat()
    closure = DependencyClosure()
    closure.file(record)
    record.write_bytes(b"other")
    os.utime(record, ns=(before.st_atime_ns, before.st_mtime_ns))
    closure.file(record)
    with pytest.raises(ValueError, match="changed during discovery"):
        closure.freeze()


@pytest.mark.parametrize("case", ["clean", "untracked", "ignored", "tracked_ignored", "missing", "binary"])
def test_guard_snapshot_matches_original_fingerprint(subject, case):
    checkout, source = subject
    if case == "untracked":
        (checkout / "untracked.py").write_text("value = 2\n", encoding="utf-8")
    elif case in {"ignored", "tracked_ignored"}:
        (checkout / ".gitignore").write_text("ignored.dat\n", encoding="utf-8")
        (checkout / "ignored.dat").write_bytes(b"ignored source bytes")
        if case == "tracked_ignored":
            subprocess.run(["git", "add", "-f", "ignored.dat"], cwd=checkout, check=True, capture_output=True)
    elif case == "missing":
        source.unlink()
    elif case == "binary":
        source.write_bytes(b"\x00\xff\x01\x00changed")
    assert reads._guard_snapshot(checkout) == snapshot(checkout)


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_source_byte_worker_rechecks_node_after_hash(subject, tmp_path, monkeypatch, replacement):
    checkout, source = subject
    external = tmp_path / "same-source.py"
    external.write_bytes(source.read_bytes())
    if replacement == "symlink":
        probe = tmp_path / "probe-file"
        try:
            probe.symlink_to(external)
        except OSError:
            pytest.skip("platform does not permit symlink creation")
        probe.unlink()
    original = reads.file_hash
    def changed(path):
        result = original(path)
        if path == source:
            source.unlink()
            if replacement == "symlink":
                source.symlink_to(external)
            else:
                source.mkdir()
        return result
    monkeypatch.setattr(reads, "file_hash", changed)
    with pytest.raises(ValueError, match="linked|changed during byte sampling"):
        reads._guard_snapshot(checkout)


@pytest.mark.parametrize("tamper", [False, True])
def test_readonly_batch_pre_freezes_additional_candidate_run(inputs, manifest, tmp_path, tamper):
    store, original, *_ = inputs
    extra = tmp_path / "additional-candidate"
    write(extra / "run-manifest.json", manifest.model_dump(mode="json"))
    write(extra / "trial-results.json", [])
    data = {"sources": [{"directory": str(original), "manifest_hash": digest(manifest.model_dump(mode="json"))}]}
    command = CommandReads()
    actual_reads = []
    def strict(directory):
        actual_reads.append(directory)
        if directory == extra and tamper:
            (extra / "late.txt").write_bytes(b"created while strict reader was running")
        return manifest, rows(manifest)
    if tamper:
        with pytest.raises(ValueError, match="changed"):
            command.baseline_and_runs(store, "b", lambda *_: data, [extra], strict)
        assert command._baselines == {} and command._runs == {} and command._pending_runs == {}
    else:
        record, checked = command.baseline_and_runs(store, "b", lambda *_: data, [extra], strict)
        assert record == data and set(checked) == {original, extra}
        assert len(checked[extra][1]) == 114
        command.check()
    assert set(actual_reads) == {original, extra}
