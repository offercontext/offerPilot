"""Failed original-reader attempts retain complete receipts and never become usable proof."""

import json
from pathlib import Path
from base64 import b64decode
from hashlib import sha256
import subprocess
import os
import py_compile
import sys
from types import SimpleNamespace

import pytest

from offerpilot.evaluation import core_refresh_history as history
from offerpilot.evaluation.core_refresh_contracts import reference
from offerpilot.evaluation.registry import digest, file_hash
from offerpilot.evaluation.runner import ROOT


def setup_history(tmp_path, monkeypatch):
    checkout = tmp_path / "old-checkout"
    reader = checkout / "src/offerpilot/evaluation/core_runner.py"
    reader.parent.mkdir(parents=True)
    reader.write_text("synthetic reader")
    source = dict(commit="a" * 40, diff_hash="b" * 64, files_hash="c" * 64, lock_hash="d" * 64)
    runtime = dict(python="synthetic", sqlite="synthetic", dependencies={})
    target = source | dict(
        checkout=str(checkout), interpreter=str(tmp_path / "old-python"), runtime=runtime
    )
    old_run = tmp_path / "old-run"
    old_run.mkdir()
    manifest = dict(variants=dict(candidate=target))
    (old_run / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    record = dict(
        baseline_id="original",
        evaluator_hash="0" * 64,
        run_directory=str(old_run),
        manifest_hash=digest(manifest),
    )
    store = tmp_path / "store"
    (store / "records").mkdir(parents=True)
    (store / "records/original.json").write_text(json.dumps(record), encoding="utf-8")
    native = tmp_path / "old-native.json"
    native.write_text("{}")
    approval = tmp_path / "old-approval.json"
    approval.write_text("{}")
    descriptor = tmp_path / "history.json"
    descriptor.write_text(
        json.dumps(
            dict(
                schema_version=3,
                kind="baseline-history-reader",
                native_receipt=reference(native),
                approval_file=reference(approval),
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(history, "snapshot", lambda *_: source)
    monkeypatch.setattr(history, "target_environment", lambda *_: runtime)
    return store, record, descriptor


@pytest.mark.parametrize("failure", ["native_failure", "timeout"])
def test_failed_history_keeps_full_native_output_and_timeout(tmp_path, monkeypatch, failure):
    store, record, descriptor = setup_history(tmp_path, monkeypatch)
    stdout, stderr = "full native stdout" * 1000, "full native error chain" * 1000

    def run(argv, **_):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 180, stdout.encode(), stderr.encode())
        return SimpleNamespace(returncode=7, stdout=stdout.encode(), stderr=stderr.encode())

    monkeypatch.setattr(history.subprocess, "run", run)
    output = tmp_path / "new-attempt"
    with pytest.raises(history.HistoryReadError) as caught:
        history.audit_history(store, record, descriptor, output)
    receipt = caught.value.receipt
    assert receipt["stdout"] == stdout and receipt["stderr"] == stderr
    assert receipt["timed_out"] is (failure == "timeout")
    assert receipt["native_exit"] == (None if failure == "timeout" else 7)
    saved = json.loads(next(output.glob("historical-native-*.json")).read_bytes())
    assert saved == receipt
    assert receipt["child_before"] == receipt["child_after"]


def test_child_script_change_during_execution_is_rejected(tmp_path, monkeypatch):
    store, record, descriptor = setup_history(tmp_path, monkeypatch)
    original = history.reference
    count = [0]

    def changing(path):
        if path == ROOT / "scripts/evaluation_subject/baseline_history.py":
            count[0] += 1
            return dict(path=str(path), sha256=("a" if count[0] == 1 else "b") * 64)
        return original(path)

    monkeypatch.setattr(history, "reference", changing)
    monkeypatch.setattr(
        history.subprocess,
        "run",
        lambda *_, **kw: SimpleNamespace(returncode=0, stdout=b"{}", stderr=b""),
    )
    with pytest.raises(ValueError, match="script changed"):
        history.audit_history(store, record, descriptor, tmp_path / "attempt")


def test_native_output_hash_preserves_original_crlf_and_nontext_stderr(tmp_path, monkeypatch):
    store, record, descriptor = setup_history(tmp_path, monkeypatch)
    target = json.loads((Path(record["run_directory"]) / "run-manifest.json").read_bytes())[
        "variants"
    ]["candidate"]
    reader = Path(target["checkout"]) / "src/offerpilot/evaluation/core_runner.py"
    proof = dict(
        schema_version=3,
        kind="original-evaluator-readonly-audit",
        source_loading_policy="empty-prefix-bytecode-disabled",
        original_raw_byte_binding=True,
        original_record_digest=digest(record),
        evaluator_hash=record["evaluator_hash"],
        reader_file=str(reader),
        reader_file_sha256=file_hash(reader),
        interpreter=target["interpreter"],
        approved=dict(target=target),
    )
    raw = json.dumps(proof).encode("utf-8") + b"\r\n"
    error = b"preserved native stderr\xff\r\n"
    monkeypatch.setattr(
        history.subprocess,
        "run",
        lambda *_, **kw: SimpleNamespace(returncode=0, stdout=raw, stderr=error),
    )
    result = history.audit_history(store, record, descriptor, tmp_path / "native-attempt")
    assert result["stdout_sha256"] == sha256(raw).hexdigest()
    assert result["native_receipt"]["stdout"].endswith("\r\n")
    assert b64decode(result["native_receipt"]["stdout_base64"]) == raw
    assert b64decode(result["native_receipt"]["stderr_base64"]) == error


def test_historical_launcher_ignores_valid_stale_checkout_bytecode(tmp_path, monkeypatch):
    store, record, descriptor = setup_history(tmp_path, monkeypatch)
    target = json.loads((Path(record["run_directory"]) / "run-manifest.json").read_bytes())[
        "variants"
    ]["candidate"]
    checkout = Path(target["checkout"])
    module = checkout / "src/read_only_product.py"
    module.write_bytes(b"VALUE = 41\n")
    stamp = module.stat()
    py_compile.compile(
        str(module), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP
    )
    module.write_bytes(b"VALUE = 42\n")
    os.utime(module, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    before = {
        p.relative_to(checkout).as_posix(): p.read_bytes()
        for p in checkout.rglob("*")
        if p.is_file()
    }
    native = subprocess.run
    probe = (
        f"import sys; sys.path.insert(0,{str(checkout / 'src')!r}); "
        "import read_only_product; print(read_only_product.VALUE)"
    )
    stale = native([sys.executable, "-I", "-B", "-c", probe], capture_output=True, timeout=30)
    assert stale.returncode == 0 and stale.stdout.strip() == b"41"

    def execute(argv, **kwargs):
        actual = [sys.executable, *argv[1:-1], "-c", probe]
        checked = native(actual, capture_output=True, cwd=checkout, timeout=30)
        assert checked.returncode == 0 and checked.stdout.strip() == b"42", checked.stderr
        reader = checkout / "src/offerpilot/evaluation/core_runner.py"
        proof = dict(
            schema_version=3,
            kind="original-evaluator-readonly-audit",
            source_loading_policy="empty-prefix-bytecode-disabled",
            original_raw_byte_binding=True,
            original_record_digest=digest(record),
            evaluator_hash=record["evaluator_hash"],
            reader_file=str(reader),
            reader_file_sha256=file_hash(reader),
            interpreter=target["interpreter"],
            approved=dict(target=target),
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(proof).encode(), stderr=b"")

    monkeypatch.setattr(history.subprocess, "run", execute)
    history.audit_history(store, record, descriptor, tmp_path / "attempt")
    assert {
        p.relative_to(checkout).as_posix(): p.read_bytes()
        for p in checkout.rglob("*")
        if p.is_file()
    } == before
