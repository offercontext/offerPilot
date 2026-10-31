"""Cleanup failures must stay observable without dropping other resources or trial evidence."""
from __future__ import annotations

import errno
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest

from offerpilot.evaluation.core_subject import CoreSubject
from offerpilot.evaluation.runner import Subject


def stopped_subject(cls, failing=(), *, reader_alive=False, exit_proven=True, closed_after_error=False):
    calls = []
    class Stream(io.StringIO):
        def __init__(self, name):
            super().__init__()
            self.name = name
        def close(self):
            calls.append(self.name)
            if self.name in failing:
                if closed_after_error:
                    super().close()
                raise OSError(errno.EINVAL, "CLEANUP-SECRET")
            super().close()
    value = object.__new__(cls)
    value.closed = False
    value.errors = []
    value.cleanup_errors = []
    value._stop_lock = threading.Lock()
    value._tree_close_attempted = False
    value._tree_closed = False
    value._shutdown_graceful = False
    value._owned_tree_exit_proof = None
    value.config = dict(epoch=1, trial_key=dict(run_id="test", variant_id="candidate",
        execution_item_id="E01.runtime_read", mode="deterministic", repeat_index=1),
        limits=dict(shutdown_seconds=5))
    value.deadline = time.monotonic() + 30
    value.process = SimpleNamespace(stdin=Stream("stdin"), stdout=Stream("stdout"),
        returncode=0, poll=lambda: 0)
    def wait(**kwargs):
        calls.append("tree.wait")
        if not exit_proven:
            raise TimeoutError("WAIT-SECRET")
        return 0
    value.tree = SimpleNamespace(wait=wait, kill=lambda: calls.append("tree.kill"),
                                 close=lambda: calls.append("tree.close"))
    value.reader = SimpleNamespace(join=lambda **kwargs: calls.append("reader.join"),
                                    is_alive=lambda: reader_alive)
    value.stderr = Stream("stderr")
    if cls is CoreSubject:
        value._control_state = dict(reader_exited=not reader_alive, last_ack_seq=1)
    return value, calls


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
@pytest.mark.parametrize("failed", ["stderr", "stdin", "stdout"])
@pytest.mark.parametrize("closed_after_error", [False, True])
def test_each_stream_failure_does_not_skip_other_closes_or_claim_control_complete(cls, failed, closed_after_error):
    value, calls = stopped_subject(cls, {failed}, closed_after_error=closed_after_error)
    value.stop(crash=True)
    assert calls[-3:] == ["stderr", "stdin", "stdout"]
    assert value.closed is False
    assert value.owned_tree_exit_proven is True
    assert value.errors
    records = value.cleanup_errors
    assert any(r["operation"] == failed + ".close" and r["error_type"] == "OSError"
               and r["errno"] == errno.EINVAL for r in records)
    assert "SECRET" not in json.dumps(records)
    if cls is CoreSubject:
        observation = value.control_observation()
        assert observation["subject_closed"] is False and observation["errors"]
        assert observation["owned_tree_exit_proven"] is True


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_multiple_close_errors_and_second_cleanup_preserve_all_prior_failures(cls):
    value, calls = stopped_subject(cls, {"stderr", "stdin", "stdout"})
    value.errors.append("prior-driver-control-error")
    value.stop(crash=True)
    first = json.loads(json.dumps(value.cleanup_errors))
    assert {r["operation"] for r in first} == {"stderr.close", "stdin.close", "stdout.close"}
    value.stop(crash=True)
    assert value.cleanup_errors[:len(first)] == first
    assert "prior-driver-control-error" in value.errors and value.closed is False
    assert calls.count("stderr") == calls.count("stdin") == calls.count("stdout") == 2
    assert "SECRET" not in json.dumps(value.cleanup_errors)


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
@pytest.mark.parametrize("reader_alive,exit_proven", [(True, True), (False, False)])
def test_unverified_reader_or_tree_never_forces_pipe_close_or_closed_claim(cls, reader_alive, exit_proven):
    value, calls = stopped_subject(cls, reader_alive=reader_alive, exit_proven=exit_proven)
    value.stop(crash=True)
    assert value.closed is False
    assert "stdin" not in calls and "stdout" not in calls
    assert "stderr" in calls
    assert value.owned_tree_exit_proven is exit_proven
    assert value.errors


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_normal_stop_and_idempotent_second_stop_keep_original_proof(cls):
    value, calls = stopped_subject(cls)
    value.stop(crash=True)
    assert value.closed and value.owned_tree_exit_proven and not value.errors
    first = list(calls)
    value.stop(crash=True)
    assert calls == first


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_tree_handle_close_failure_does_not_skip_safe_pipe_closes(cls):
    value, calls = stopped_subject(cls)
    def bad_tree_close():
        calls.append("tree.close")
        raise OSError(errno.EINVAL, "TREE-SECRET")
    value.tree.close = bad_tree_close
    value.stop(crash=True)
    assert calls[-3:] == ["stderr", "stdin", "stdout"]
    assert not value.closed and value.owned_tree_exit_proven
    assert any(r["operation"] == "tree.close" for r in value.cleanup_errors)
    assert "SECRET" not in json.dumps(value.cleanup_errors)


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_shutdown_write_error_is_not_reported_as_timeout(cls):
    value, calls = stopped_subject(cls)
    polls = iter([None, 0])
    value.process.poll = lambda: next(polls)
    def bad_send(message):
        raise OSError(errno.EINVAL, "SEND-SECRET")
    value.send = bad_send
    value.stop()
    assert "shutdown_failed" in value.errors and "shutdown_timeout" not in value.errors
    assert value.cleanup_errors[0]["operation"] == "shutdown.send"
    assert calls[-3:] == ["stderr", "stdin", "stdout"]
    assert "SECRET" not in json.dumps(value.cleanup_errors)


def test_cleanup_resources_attempts_each_subject_and_driver_after_multiple_errors():
    from offerpilot.evaluation.runner import cleanup_resources
    first, first_calls = stopped_subject(CoreSubject, {"stdin"})
    second, second_calls = stopped_subject(CoreSubject, {"stdout"})
    second.config["epoch"] = 2
    closed = []
    def driver(index):
        def close():
            closed.append(index)
            raise OSError(errno.EPIPE, "DRIVER-SECRET")
        return SimpleNamespace(close=close)
    one, two = driver(1), driver(2)
    records = cleanup_resources([first, second], [(1, one), (2, two), (1, one)])
    assert first_calls[-3:] == second_calls[-3:] == ["stderr", "stdin", "stdout"]
    assert closed == [1, 2]
    assert [(r["operation"], r["epoch"]) for r in records] == [
        ("stdin.close", 1), ("stdout.close", 2), ("http_driver.close", 1), ("http_driver.close", 2)]
    assert not first.closed and not second.closed
    assert "SECRET" not in json.dumps(records)


def test_unexpected_stop_exception_does_not_skip_later_resources():
    from offerpilot.evaluation.runner import cleanup_resources
    first, _ = stopped_subject(CoreSubject)
    second, calls = stopped_subject(CoreSubject)
    def bad_stop():
        raise ValueError("STOP-SECRET")
    first.stop = bad_stop
    http_calls = []
    records = cleanup_resources([first, second], [(1, SimpleNamespace(close=lambda: http_calls.append(1)))])
    assert calls[-3:] == ["stderr", "stdin", "stdout"] and second.closed
    assert http_calls == [1]
    assert records == [dict(operation="subject.stop", error_type="ValueError", errno=None, epoch=1)]
    assert not first.closed


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_repeated_stop_cannot_upgrade_parent_only_wait_after_ownership_handle_closed(cls):
    value, calls = stopped_subject(cls)
    lease = {"closed": False, "waits": 0}
    def wait(**kwargs):
        lease["waits"] += 1
        if not lease["closed"]:
            raise TimeoutError("owned descendants still unverified")
        return 0  # Models Windows ProcessTree.wait(job=None): only parent exited.
    def close():
        lease["closed"] = True
    value.tree.wait, value.tree.close = wait, close
    value.stop(crash=True)
    assert not value.owned_tree_exit_proven and not value.closed
    first_errors = list(value.errors)
    value.stop(crash=True)
    assert lease["waits"] == 2
    assert not value.owned_tree_exit_proven and not value.closed
    assert value._owned_tree_exit_proof is None
    assert value.errors[:len(first_errors)] == first_errors
    assert "stdin" not in calls and "stdout" not in calls


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_valid_owned_exit_proof_survives_close_error_and_repeated_cleanup(cls):
    failed = {"stdin"}
    value, calls = stopped_subject(cls, failed, closed_after_error=True)
    value.stop(crash=True)
    assert value.owned_tree_exit_proven and not value.closed
    proof = dict(value._owned_tree_exit_proof)
    first_errors = list(value.errors)
    waits = calls.count("tree.wait")
    failed.clear()
    value.stop(crash=True)
    assert value.owned_tree_exit_proven and value._owned_tree_exit_proof == proof
    assert calls.count("tree.wait") == waits
    assert value.errors[:len(first_errors)] == first_errors and value.errors

@pytest.mark.parametrize("primary", [None, TimeoutError, "product"])
@pytest.mark.parametrize("cleanup_mode", ["subject", "http", "both"])
def test_legacy_runner_seals_primary_failure_and_cleanup_errors(tmp_path, monkeypatch, primary, cleanup_mode):
    import sys
    from uuid import uuid4
    from offerpilot.evaluation import runner
    from offerpilot.evaluation.adapters import ProductFailure
    from offerpilot.evaluation.contracts import PlannedTrial, VariantConfig
    from offerpilot.evaluation.registry import file_hash, load_suite
    source = dict(commit="a"*40, diff_hash="b"*64, lock_hash="c"*64, files_hash="d"*64)
    runtime = dict(python="python", sqlite="sqlite", dependencies={})
    monkeypatch.setattr(runner, "snapshot", lambda path: source)
    monkeypatch.setattr(runner.observer, "marker", lambda *args: None)
    suite, specs = load_suite(runner.ROOT/"evals/suites/harness-p0-v1.json", runner.ROOT/"evals")
    spec = specs["E01.runtime_read"]
    declaration = next(item for item in suite.execution_items if item.execution_item_id == spec.execution_item_id)
    key = PlannedTrial(run_id=uuid4(), variant_id="candidate", execution_item_id=spec.execution_item_id,
        mode="deterministic", repeat_index=1, case_id=spec.case_id, case_version=1,
        spec_hash=file_hash(runner.ROOT/"evals"/declaration.spec_path),
        fixture_hash=file_hash(runner.ROOT/"evals/fixtures/synthetic-interviews-v1.json"),
        coverage_kind=declaration.coverage_kind, required_rules=spec.required_rules)
    calls = []
    private = tmp_path/"private"
    class FakeSubject:
        def __init__(self, variant, config, folder, events, deadline):
            self.config, self.closed, self.errors = config, False, []
        def wait(self, kind, timeout):
            if kind == "prepared":
                return dict(initial={}, aliases=dict(target_application=1),
                    coverage=dict(columns={},schema_hash="a"*64,trigger_hash="b"*64,
                        sqlite_settings={},epochs=[1],start_seq=0))
            return dict(checkout_sha=source["commit"], bridge_version="p0-v1",
                product_file=str(runner.ROOT/"src/offerpilot/api.py"),data_dir=str(private),
                database_file=str(private/"data.db"),interpreter=sys.executable,
                address="http://127.0.0.1:1",capabilities=[spec.adapter],**runtime)
        def stop(self):
            calls.append("subject")
            self.closed = True
            if cleanup_mode in {"subject", "both"}:
                raise OSError(22, "SUBJECT-CLEANUP-SECRET")
    class FakeDriver:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            if primary == "product":
                raise ProductFailure("business rejected")
            if primary is TimeoutError:
                raise TimeoutError("PRIMARY-SECRET")
            return dict(conversation_id=1,turn_id="turn")
        def wait_turn(self, *args):
            return dict(state="completed",terminal=dict(response=dict(message="[]")))
        def close(self):
            calls.append("http")
            if cleanup_mode in {"http", "both"}:
                raise OSError(22, "HTTP-CLEANUP-SECRET")
    monkeypatch.setattr(runner, "Subject", FakeSubject)
    monkeypatch.setattr(runner, "HTTPDriver", FakeDriver)
    variant = VariantConfig(variant_id="candidate",checkout=str(runner.ROOT),interpreter=sys.executable)
    result, evidence = runner.run_trial(key,spec,variant,private,source | dict(target_runtime=runtime))
    assert calls == ["subject","http"]
    assert result.execution_status == ("product_failed" if primary=="product" else
                                       "interrupted" if primary is TimeoutError else "eval_error")
    assert result.verdict != "pass"
    assert (private/"evidence.json").is_file()
    assert evidence.facts["cleanup_errors"]
    assert not evidence.facts["control_closed"]
    assert evidence.facts["consumption"]["model_attempts"] is None
    assert "SECRET" not in json.dumps(evidence.facts["cleanup_errors"])
    if primary:
        assert evidence.fragment_errors["driver"] == ("ProductFailure" if primary=="product" else "TimeoutError")
    assert evidence.fragment_errors["cleanup"]

@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_graceful_stop_retains_original_shutdown_and_reader_deadlines(cls):
    value, calls = stopped_subject(cls)
    polls = iter([None, 0])
    value.process.poll = lambda: next(polls)
    sent, timeouts, joins = [], [], []
    value.send = lambda message: sent.append(message)
    value.wait = lambda kind, timeout: timeouts.append((kind, timeout))
    value.reader.join = lambda timeout: joins.append(timeout)
    value.stop()
    assert sent == [{"command": "request_shutdown"}]
    assert timeouts[0][0] == "shutdown_complete" and 0 <= timeouts[0][1] <= 5
    assert joins == [5]
    assert value.closed and value.owned_tree_exit_proven and not value.errors
    assert value._owned_tree_exit_proof["source"] == "owned_tree.wait"


@pytest.mark.parametrize("cls", [Subject, CoreSubject])
def test_concurrent_cleanup_cannot_release_ownership_before_the_active_wait(cls):
    value, calls = stopped_subject(cls)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    failures, waits = [], []
    def wait(**kwargs):
        waits.append(1)
        entered.set()
        assert release.wait(2)
        return 0
    value.tree.wait = wait
    def work():
        try:
            value.stop(crash=True)
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()
    first = threading.Thread(target=work)
    second = threading.Thread(target=work)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        assert not finished.wait(0.05)
        assert not value._tree_close_attempted
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not first.is_alive() and not second.is_alive() and not failures
    assert waits == [1]
    assert value.closed and value.owned_tree_exit_proven
