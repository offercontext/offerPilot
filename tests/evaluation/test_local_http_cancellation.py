"""Actual local HTTP and authenticated helper races; no Provider requests."""
from __future__ import annotations

import contextlib
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from offerpilot.evaluation.adapters import DriverError
from offerpilot.evaluation.core_actions import CoreHTTPDriver, CoreHTTPStatusFailure
from scripts.evaluation_subject.local_http import HeaderCancellationArbiter, sign_control, verify_control


def test_header_first_and_cancel_first_are_ordered_and_late_headers_are_retained():
    rows = []
    arbiter = HeaderCancellationArbiter(rows.append)
    arbiter.headers(502)
    assert arbiter.cancel() is False
    assert [r['stage'] for r in rows] == ['headers', 'cancel_refused_after_headers']
    rows = []
    arbiter = HeaderCancellationArbiter(rows.append)
    assert arbiter.cancel() is True
    arbiter.headers(502)
    assert rows[1] == dict(stage='headers', status=502, arbitration='headers_after_cancellation')


@pytest.mark.parametrize('damage', ['wrong_key', 'wrong_domain', 'changed_value', 'missing_mac'])
def test_target_cannot_forge_or_repurpose_private_helper_receipts(damage):
    key = b'a'*32
    value = sign_control(dict(identity={'observation_id':'one'}, observation_seq=1), key, 'progress')
    if damage == 'wrong_key':
        key = b'b'*32
    if damage == 'changed_value':
        value['observation_seq'] = 2
    if damage == 'missing_mac':
        value.pop('control_mac')
    with pytest.raises(ValueError):
        verify_control(value, key, 'cancel' if damage == 'wrong_domain' else 'progress')


def driver_for(address, tmp_path, callback):
    return CoreHTTPDriver(address, 'synthetic', time.monotonic()+30, [], 8,
        blocking_waits={'/api/chat':120}, diagnostic_dir=tmp_path, cleanup_reserve=2,
        request_context={'epoch':1, 'trial_key':{'fixture':'local-only'}},
        cancellation_check=callback)


@contextlib.contextmanager
def controlled_server(status, *, truncate=False):
    entered, release_headers, release_body = (threading.Event() for _ in range(3))
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append(self.path)
            entered.set()
            if not release_headers.wait(25):
                return
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(b'{"error":"unchanged original reply"}')))
                self.end_headers()
                self.wfile.flush()
                if release_body.wait(25):
                    if not truncate:
                        self.wfile.write(b'{"error":"unchanged original reply"}')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', entered, release_headers, release_body, calls
    finally:
        release_headers.set()
        release_body.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def observed_stages(tmp_path):
    rows = []
    for path in tmp_path.glob('local-http-*.jsonl'):
        try:
            rows.extend(json.loads(line) for line in path.read_bytes().splitlines())
        except (ValueError, OSError):
            continue  # Test coordination only; the driver authenticates receipts.
    return [row.get('stage') for row in rows]


def test_actual_cancel_before_headers_uses_one_request_and_keeps_response_unknown(tmp_path):
    with controlled_server(200) as (address, entered, _, _, calls):
        refusal = TimeoutError('synthetic budget refusal')
        driver = driver_for(address, tmp_path, lambda: refusal if entered.is_set() else None)
        try:
            with pytest.raises(TimeoutError) as caught:
                driver.request('POST','/api/chat',{'message':'synthetic'})
            assert caught.value is refusal
            row = driver.local_http_observations[0]
            assert row['cancelled_by_budget'] is True and row['owned_tree_exited'] is True
            assert row['progress_control_authenticated'] is True
            assert row['response_received'] is None and row['body_complete'] is None
            assert 'cancel_accepted_before_headers' in observed_stages(tmp_path)
            assert calls == ['/api/chat'] and driver.observations == []
        finally:
            driver.close()


def test_actual_headers_first_cannot_be_replaced_by_budget_denial(tmp_path):
    with controlled_server(502) as (address, _, release_headers, release_body, calls):
        release_headers.set()
        refusal = TimeoutError('synthetic budget refusal')
        stop = threading.Event()
        def release_after_refusal():
            while not stop.wait(.01):
                if 'cancel_refused_after_headers' in observed_stages(tmp_path):
                    release_body.set()
                    return
        observer = threading.Thread(target=release_after_refusal, daemon=True)
        observer.start()
        driver = driver_for(address, tmp_path,
            lambda: refusal if 'headers' in observed_stages(tmp_path) else None)
        try:
            with pytest.raises(CoreHTTPStatusFailure) as caught:
                driver.request('POST','/api/chat',{'message':'synthetic'})
            assert caught.value.status == 502
            row = driver.local_http_observations[0]
            assert row['cancelled_by_budget'] is False and row['body_complete'] is True, dict(
                record=row, errors=driver.request_errors,
                original_error_type=type(caught.value.__context__).__name__)
            assert row['owned_tree_exited'] is True
            assert driver.observations[0]['response'] == {'error':'unchanged original reply'}
            assert calls == ['/api/chat']
        finally:
            stop.set()
            release_body.set()
            observer.join(2)
            driver.close()


def fake_cancel_worker(monkeypatch, *, late_status=None, corrupt_archive=False, cancel_collision=False,
                       partial_pipe_tail=False, completed_terminal_status=None,
                       helper_exit_after_terminal=None, cleanup_failure=False, on_close=None,
                       exit_capture_failure=False, pending_terminal_only=False, before_timeout=None,
                       normal_terminal_exit=None):
    from offerpilot.evaluation import core_actions
    rows, state = [], {}
    process = SimpleNamespace(returncode=None, stdin=None, stdout=None)
    def append(stage, **extra):
        row = sign_control(dict(identity=state['value']['identity'], observation_seq=len(rows)+1,
                                stage=stage, **extra), state['key'], 'local-http-progress-v1')
        rows.append(row)
        with Path(state['value']['progress_file']).open('ab') as stream:
            stream.write(json.dumps(row).encode()+b'\n')
    def raw():
        result = b''.join(json.dumps(row).encode()+b'\n' for row in rows)
        if state.get('terminal'):
            result += json.dumps(state['terminal']).encode()+b'\n'
        return result
    def communicate(payload=None, timeout=None):
        if payload is not None:
            state['value'] = json.loads(payload)
            state['key'] = bytes.fromhex(state['value']['control_key'])
            append('send_started')
            if cancel_collision:
                Path(state['value']['cancel_file']).write_bytes(b'protected canary')
            if completed_terminal_status is not None:
                append('headers', status=completed_terminal_status)
                state['terminal'] = dict(stage='result', identity=state['value']['identity'],
                    ok=True, status=completed_terminal_status, headers={'content-type':'application/json'},
                    content=base64.b64encode(b'{"message":"complete before exit"}').decode())
                if normal_terminal_exit is not None:
                    process.returncode = normal_terminal_exit
                    return raw(), None
        elif process.returncode is None:
            if completed_terminal_status is not None:
                raise AssertionError('Complete private terminal was not consumed before waiting again')
            command = verify_control(json.loads(Path(state['value']['cancel_file']).read_bytes()),
                                     state['key'], 'local-http-cancel-v1')
            assert command['identity'] == state['value']['identity']
            append('cancel_accepted_before_headers')
        else:
            if late_status is not None:
                append('headers', status=late_status, arbitration='headers_after_cancellation')
            if corrupt_archive:
                Path(state['value']['progress_file']).write_bytes(b'{"forged":"target"}\n')
            return raw() + (b'{"partial-result":' if partial_pipe_tail else b''), None
        if before_timeout is not None:
            before_timeout()
        output = (json.dumps(state['terminal']).encode()+b'\n' if pending_terminal_only else raw())
        raise subprocess.TimeoutExpired('private helper', timeout, output=output)
    def kill():
        process.returncode = -9
    process.communicate = communicate
    def poll():
        if exit_capture_failure and process.returncode is not None:
            raise RuntimeError('synthetic exit capture failure')
        if (helper_exit_after_terminal is not None and state.get('terminal')
                and process.returncode is None):
            process.returncode = helper_exit_after_terminal
        return process.returncode
    process.poll = poll
    def close(**kwargs):
        if on_close is not None:
            on_close()
        if cleanup_failure:
            raise OSError('synthetic cleanup failure')
    tree = SimpleNamespace(process=process, kill=kill,
        wait=lambda **kwargs: process.returncode, close=close)
    monkeypatch.setattr(core_actions, 'process_tree', lambda *args, **kwargs: tree)


@pytest.mark.parametrize('late_status',[500,502,503])
def test_authenticated_late_failed_headers_win_after_owned_cancellation(tmp_path, monkeypatch, late_status):
    fake_cancel_worker(monkeypatch, late_status=late_status)
    driver = driver_for('http://127.0.0.1:1', tmp_path, lambda: TimeoutError('unit refusal'))
    try:
        with pytest.raises(CoreHTTPStatusFailure) as caught:
            driver.request('POST','/api/chat')
        assert caught.value.status == late_status
        row = driver.local_http_observations[0]
        assert row['response_received'] is True and row['response_status'] == late_status
        assert row['body_complete'] is None and row['owned_tree_exited'] is True
        assert not driver.observations  # No invented response body.
    finally:
        driver.close()


def test_target_archive_tampering_cannot_certify_clean_interruption(tmp_path, monkeypatch):
    fake_cancel_worker(monkeypatch, corrupt_archive=True)
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda: TimeoutError('unit refusal'))
    try:
        with pytest.raises(DriverError,match='cancellation evidence incomplete'):
            driver.request('POST','/api/chat')
        row = driver.local_http_observations[0]
        assert row['response_received'] is None and row['capture_error_type']
        assert not driver.observations
    finally:
        driver.close()


@pytest.mark.parametrize('status', [200, 502])
def test_partial_terminal_tail_never_erases_prior_authenticated_error_headers(tmp_path, monkeypatch, status):
    fake_cancel_worker(monkeypatch, late_status=status, partial_pipe_tail=True)
    driver = driver_for('http://127.0.0.1:1', tmp_path, lambda: TimeoutError('unit refusal'))
    try:
        with pytest.raises(CoreHTTPStatusFailure if status == 502 else DriverError):
            driver.request('POST','/api/chat')
        row = driver.local_http_observations[0]
        assert row['response_status'] == status and row['capture_error_type']
        assert row['body_complete'] is None
    finally:
        driver.close()


@pytest.mark.parametrize('status', [200, 502])
def test_actual_truncated_body_keeps_success_status_unknown_and_error_status_failed(tmp_path, status):
    with controlled_server(status, truncate=True) as (address, _, headers, body, calls):
        headers.set()
        body.set()
        driver = driver_for(address, tmp_path, lambda: None)
        try:
            with pytest.raises(CoreHTTPStatusFailure if status == 502 else httpx.RemoteProtocolError) as caught:
                driver.request('POST','/api/chat')
            if status == 502:
                assert caught.value.status == status
            assert driver.local_http_observations[0]['body_complete'] is None
            assert driver.local_http_observations[0]['response_received'] is True
            assert calls == ['/api/chat']
        finally:
            driver.close()


def test_cancel_publish_collision_never_overwrites_existing_target_file(tmp_path, monkeypatch):
    fake_cancel_worker(monkeypatch, cancel_collision=True)
    driver = driver_for('http://127.0.0.1:1', tmp_path, lambda: TimeoutError('unit refusal'))
    try:
        with pytest.raises(FileExistsError):
            driver.request('POST','/api/chat')
        assert next(tmp_path.glob('*.cancel.json')).read_bytes() == b'protected canary'
        assert driver.local_http_observations[0]['cancelled_by_budget'] is False
    finally:
        driver.close()


def test_existing_hardlinked_progress_cannot_corrupt_protected_bytes(tmp_path, monkeypatch):
    from offerpilot.evaluation import core_actions
    import os
    victim = tmp_path/'protected'
    victim.write_bytes(b'protected canary')
    os.link(victim, tmp_path/'local-http-unit.jsonl')
    monkeypatch.setattr(core_actions, 'uuid4', lambda: SimpleNamespace(hex='unit'))
    with controlled_server(200) as (address, _, _, _, calls):
        driver = driver_for(address, tmp_path, lambda: None)
        try:
            with pytest.raises(DriverError, match='child failed'):
                driver.request('POST','/api/chat')
            assert victim.read_bytes() == b'protected canary' and calls == []
        finally:
            driver.close()


@pytest.mark.parametrize('status',[200,202,502])
def test_completed_private_terminal_wins_before_helper_exit_or_new_cancellation(tmp_path, monkeypatch, status):
    fake_cancel_worker(monkeypatch, completed_terminal_status=status)
    checks = []
    driver = driver_for('http://127.0.0.1:1', tmp_path, lambda: checks.append('cancel-check'))
    try:
        with pytest.raises(CoreHTTPStatusFailure) if status == 502 else contextlib.nullcontext():
            assert driver.request('POST','/api/chat') == {'message':'complete before exit'}
        row = driver.local_http_observations[0]
        assert checks == [] and row['body_complete'] is True and row['owned_tree_exited'] is True
        assert driver.observations[0]['response'] == {'message':'complete before exit'}
        assert row['terminal_observed_before_helper_exit'] is True
    finally:
        driver.close()


@pytest.mark.parametrize('status',[200,502])
def test_unexpected_exit_after_complete_terminal_still_blocks_success(tmp_path, monkeypatch, status):
    fake_cancel_worker(monkeypatch, completed_terminal_status=status, helper_exit_after_terminal=1)
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda:None)
    try:
        with pytest.raises(CoreHTTPStatusFailure if status == 502 else DriverError):
            driver.request('POST','/api/chat')
        row = driver.local_http_observations[0]
        assert row['unexpected_helper_exit'] == 1 and row['body_complete'] is True
        if status == 502:
            assert driver.observations[0]['response'] == {'message':'complete before exit'}
    finally:
        driver.close()


def test_known_failed_terminal_survives_cleanup_failure_with_body_retained(tmp_path, monkeypatch):
    fake_cancel_worker(monkeypatch, completed_terminal_status=502, cleanup_failure=True)
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda:None)
    try:
        with pytest.raises(CoreHTTPStatusFailure):
            driver.request('POST','/api/chat')
        assert driver.observations[0]['response'] == {'message':'complete before exit'}
        assert driver.local_http_observations[0]['cleanup_error_type'] == 'OSError'
    finally:
        with pytest.raises(DriverError,match='unverified'):
            driver.close()


@pytest.mark.parametrize('status',[200,502])
def test_cleanup_cannot_accept_late_success_or_erase_prior_observed_failure(tmp_path, monkeypatch, status):
    from offerpilot.evaluation import core_actions
    clock = [100.0]
    fake_cancel_worker(monkeypatch, completed_terminal_status=status,
                       on_close=lambda:clock.__setitem__(0,140.0))
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda:None)
    driver.deadline = 130.0
    monkeypatch.setattr(core_actions,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    try:
        with pytest.raises(CoreHTTPStatusFailure if status == 502 else TimeoutError):
            driver.request('POST','/api/chat')
        assert driver.local_http_observations[0]['body_complete'] is True
        if status == 502:
            assert driver.observations[0]['response'] == {'message':'complete before exit'}
    finally:
        driver.close()


def test_unknown_exit_after_complete_terminal_cannot_certify_success(tmp_path, monkeypatch):
    fake_cancel_worker(monkeypatch, completed_terminal_status=200, exit_capture_failure=True)
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda:None)
    try:
        with pytest.raises(DriverError):
            driver.request('POST','/api/chat')
        row = driver.local_http_observations[0]
        assert row['child_exit'] is None and row['exit_capture_error_type'] == 'RuntimeError'
    finally:
        driver.close()


@pytest.mark.parametrize('status',[200,502])
@pytest.mark.parametrize('late',[False,True])
def test_private_tail_requires_full_exit_drain_and_never_accepts_late_success(tmp_path, monkeypatch, status, late):
    from offerpilot.evaluation import core_actions
    clock = [100.0]
    fake_cancel_worker(monkeypatch, completed_terminal_status=status, pending_terminal_only=True,
                       before_timeout=(lambda:clock.__setitem__(0,250.0)) if late else None)
    driver = driver_for('http://127.0.0.1:1',tmp_path,lambda:None)
    driver.deadline = 400.0
    monkeypatch.setattr(core_actions,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    expected = CoreHTTPStatusFailure if status == 502 else TimeoutError if late else None
    try:
        with pytest.raises(expected) if expected is not None else contextlib.nullcontext():
            assert driver.request('POST','/api/chat') == {'message':'complete before exit'}
        row = driver.local_http_observations[0]
        assert row['progress_control_authenticated'] is True and not row.get('capture_error_type')
        assert row['result_observed_before_deadline'] is (not late)
        assert row['response_status'] == status
        if status == 502:
            assert driver.observations[0]['response'] == {'message':'complete before exit'}
    finally:
        driver.close()


@pytest.mark.parametrize('status',[200,502])
def test_normal_communicate_keeps_terminal_body_even_with_nonzero_exit(tmp_path, monkeypatch, status):
    fake_cancel_worker(monkeypatch, completed_terminal_status=status, normal_terminal_exit=1)
    driver = driver_for('http://127.0.0.1:1', tmp_path, lambda:None)
    try:
        with pytest.raises(CoreHTTPStatusFailure if status == 502 else DriverError):
            driver.request('POST','/api/chat')
        row = driver.local_http_observations[0]
        assert row['body_complete'] is True and row['unexpected_helper_exit'] == 1
        if status == 502:
            assert driver.observations[0]['response'] == {'message':'complete before exit'}
    finally:
        driver.close()
