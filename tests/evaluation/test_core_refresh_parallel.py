"""Actual request validation with controlled read branches; no trial/provider calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
import json

import pytest

from offerpilot.evaluation import core_release as release
from offerpilot.evaluation.core_release_reads import CommandReads, DependencyClosure
from offerpilot.evaluation.registry import digest
from tests.evaluation.test_core_governance import manifest as manifest
from tests.evaluation.test_core_refresh import refresh_case as refresh_case


def test_history_and_current_reads_overlap_and_capture_follows_both(refresh_case, monkeypatch):
    case = refresh_case
    request = case.prepare()
    entered = {name: Event() for name in ('history', 'current')}
    finished = {name: Event() for name in entered}
    history, current = case.refresh.audit_history, case.refresh.qualification

    def older(*args, **kwargs):
        entered['history'].set()
        assert entered['current'].wait(3), 'current read never overlapped history'
        value = history(*args, **kwargs)
        finished['history'].set()
        return value

    def newer(*args, **kwargs):
        entered['current'].set()
        assert entered['history'].wait(3)
        value = current(*args, **kwargs)
        finished['current'].set()
        return value

    captured = []

    def observe(*args):
        assert all(event.is_set() for event in finished.values())
        captured.append(args)

    monkeypatch.setattr(case.refresh, 'audit_history', older)
    monkeypatch.setattr(case.refresh, 'qualification', newer)
    _, _, value = case.refresh.check_request(request, datetime.now(timezone.utc), _capture=observe)
    assert value['activatable'] and len(captured) == 1
    assert captured[0][0] == case.run and len(captured[0][2]) == 114


def test_capture_waits_for_entire_qualification_return(refresh_case, monkeypatch):
    case = refresh_case
    request = case.prepare()
    current = case.refresh.qualification
    finished = Event()

    def newer(*args, **kwargs):
        value = current(*args, **kwargs)
        finished.set()
        return value

    def observe(*_):
        assert finished.is_set(), 'capture escaped before qualification returned'

    monkeypatch.setattr(case.refresh, 'qualification', newer)
    case.refresh.check_request(request, datetime.now(timezone.utc), _capture=observe)


@pytest.mark.parametrize('failed', ['history', 'current'])
def test_failure_waits_for_other_branch_cleanup(refresh_case, monkeypatch, failed):
    case = refresh_case
    request = case.prepare()
    entered = {name: Event() for name in ('history', 'current')}
    closed = {name: Event() for name in entered}
    failure_seen, release_other = Event(), Event()
    captured = []

    def controlled(name, original):
        def run(*args, **kwargs):
            entered[name].set()
            try:
                assert entered['current' if name == 'history' else 'history'].wait(3)
                if name == failed:
                    failure_seen.set()
                    raise ValueError(name + ' failed')
                assert release_other.wait(3)
                return original(*args, **kwargs)
            finally:
                closed[name].set()
        return run

    monkeypatch.setattr(case.refresh, 'audit_history', controlled('history', case.refresh.audit_history))
    monkeypatch.setattr(case.refresh, 'qualification', controlled('current', case.refresh.qualification))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(case.refresh.check_request, request, datetime.now(timezone.utc),
                             _capture=lambda *args: captured.append(args))
        try:
            assert failure_seen.wait(3)
            assert not future.done(), 'request returned while its other branch was still live'
            assert not captured
        finally:
            release_other.set()
        with pytest.raises(ValueError, match=failed + ' failed'):
            future.result(timeout=5)
    assert all(event.is_set() for event in closed.values()) and not captured


def test_both_errors_are_observed_with_history_primary(refresh_case, monkeypatch):
    case = refresh_case
    request = case.prepare()
    entered = {name: Event() for name in ('history', 'current')}
    older_error, newer_error = ValueError('history failed'), LookupError('current failed')

    def older(*_):
        entered['history'].set()
        assert entered['current'].wait(3)
        raise older_error

    def newer(*_, **__):
        entered['current'].set()
        assert entered['history'].wait(3)
        raise newer_error

    monkeypatch.setattr(case.refresh, 'audit_history', older)
    monkeypatch.setattr(case.refresh, 'qualification', newer)
    with pytest.raises(ValueError, match='history failed') as exc:
        case.refresh.check_request(request, datetime.now(timezone.utc), _capture=lambda *_: pytest.fail('early capture'))
    assert exc.value is older_error and exc.value.__cause__ is newer_error


@pytest.mark.parametrize('failure', ['saved_measurement', 'historical_proof'])
def test_successful_current_read_does_not_publish_before_saved_checks(refresh_case, monkeypatch, failure):
    case = refresh_case
    request = case.prepare()
    captured = []
    if failure == 'saved_measurement':
        current = case.refresh.qualification
        monkeypatch.setattr(case.refresh, 'qualification',
                            lambda *args, **kwargs: current(*args, **kwargs) | {'activatable': False})
    else:
        history = case.refresh.audit_history
        monkeypatch.setattr(case.refresh, 'audit_history',
                            lambda *args: history(*args) | {'proof': {'foreign': True}})
    with pytest.raises(ValueError):
        case.refresh.check_request(request, datetime.now(timezone.utc), _capture=lambda *args: captured.append(args))
    assert not captured


@pytest.mark.parametrize('bad_capture', ['missing', 'duplicate'])
def test_missing_or_duplicate_capture_fails_closed(refresh_case, monkeypatch, bad_capture):
    case = refresh_case
    request = case.prepare()
    current = case.refresh.qualification
    captured = []

    def newer(*args, _capture=None, **kwargs):
        if bad_capture == 'missing':
            return current(*args, **kwargs)
        value = current(*args, _capture=_capture, **kwargs)
        _capture(case.run, case.manifest, case.state['rows'])
        return value

    monkeypatch.setattr(case.refresh, 'qualification', newer)
    with pytest.raises(ValueError, match=bad_capture):
        case.refresh.check_request(request, datetime.now(timezone.utc), _capture=lambda *args: captured.append(args))
    assert not captured


@pytest.mark.parametrize('binding', ['directory', 'manifest', 'rows'])
def test_capture_with_foreign_binding_is_never_published(refresh_case, monkeypatch, binding):
    case = refresh_case
    request = case.prepare()
    current = case.refresh.qualification
    captured = []

    def newer(*args, _capture=None, **kwargs):
        def changed(directory, parsed, result):
            if binding == 'directory':
                directory = case.run.parent / 'foreign-run'
            elif binding == 'manifest':
                parsed = parsed.model_copy(update={'repeats': 2})
            else:
                result = [result[0].model_copy(update={'verdict': 'fail'}), *result[1:]]
            _capture(directory, parsed, result)
        return current(*args, _capture=changed, **kwargs)

    monkeypatch.setattr(case.refresh, 'qualification', newer)
    with pytest.raises(ValueError, match='capture binding differs'):
        case.refresh.check_request(request, datetime.now(timezone.utc),
                                   _capture=lambda *args: captured.append(args))
    assert not captured


def test_invalid_bound_input_is_rejected_before_either_branch(refresh_case, monkeypatch):
    case = refresh_case
    request = case.prepare()
    envelope = json.loads(request.read_bytes())
    envelope['request']['measurement']['sha256'] = '0' * 64
    envelope['request_hash'] = digest(envelope['request'])
    request.write_text(json.dumps(envelope), encoding='utf-8')
    monkeypatch.setattr(case.refresh, 'audit_history', lambda *_: pytest.fail('unbound history'))
    monkeypatch.setattr(case.refresh, 'qualification', lambda *_, **__: pytest.fail('unbound current'))
    with pytest.raises(ValueError, match='reference changed'):
        case.refresh.check_request(request, datetime.now(timezone.utc))


@pytest.mark.parametrize('failed', ['history', 'current', 'both'])
def test_branch_failure_closes_scoped_origins_without_target_or_partial_adoption(refresh_case, monkeypatch, failed):
    case = refresh_case
    (case.run / 'trial-results.json').write_text(json.dumps([
        row.model_dump(mode='json') for row in case.state['rows']]), encoding='utf-8')
    request = case.prepare()
    case.refresh.activate_refresh(request, case.authorize(request))
    envelope = json.loads(request.read_bytes())['request']

    def guard(*_):
        closure = DependencyClosure()
        closure.tree(case.run)
        return closure.freeze()

    monkeypatch.setattr(DependencyClosure, 'for_baseline', guard)
    scopes = []
    reader = case.refresh.read_run

    def observed(directory, *, _capture_origin=None):
        parsed, result = reader(directory)
        assert _capture_origin is not None
        for row in result:
            _capture_origin(directory, parsed, row, dict(errors={}, facts=dict(handshakes=[{'trial': list(row.identity())}])))
        return parsed, result

    def origins(parsed, result, directory, *, _origin=None):
        assert _origin is not None
        scopes.append(_origin)
        assert all(_origin.evidence(directory, parsed, row) for row in result)
        return dict(synthetic_origin_test=True)

    monkeypatch.setattr(case.refresh, 'read_run', observed)
    monkeypatch.setattr(case.refresh, 'source_origins', origins)
    older, newer = case.refresh.audit_history, case.refresh.qualification

    def history(*args):
        value = older(*args)
        if failed in ('history', 'both'):
            raise ValueError('history failed')
        return value

    def current(*args, **kwargs):
        value = newer(*args, **kwargs)
        if failed in ('current', 'both'):
            raise ValueError('current failed')
        return value

    monkeypatch.setattr(case.refresh, 'audit_history', history)
    monkeypatch.setattr(case.refresh, 'qualification', current)
    command = CommandReads()
    monkeypatch.setattr(release, 'CommandReads', lambda: command)
    monkeypatch.setattr(CommandReads, 'inputs', lambda *_: None)
    value = dict(inputs={key: {'path': envelope[key]['path']} for key in ('policy', 'review', 'experiment')},
                 baseline_store=str(case.store), baseline_id='original', baseline_record_hash='0' * 64,
                 runs=[dict(directory=str(case.run), manifest_hash=digest(case.manifest.model_dump(mode='json')), reuse=True)])
    monkeypatch.setattr(release, '_read_header', lambda *_: value)

    def plan(*_, check_targets=True):
        assert check_targets is False, 'untrusted target was executed before baseline trust'
        return case.manifest

    monkeypatch.setattr(release, 'read_plan', plan)
    monkeypatch.setattr(release, 'run_plan', lambda *_: pytest.fail('trial started after baseline error'))
    monkeypatch.setattr(release, 'gate_core', lambda *_: pytest.fail('gate ran after baseline error'))
    with pytest.raises(ValueError, match='history failed|current failed'):
        release.execute(case.run / 'synthetic-preflight-not-read.json')
    assert scopes and all(scope.closed for scope in scopes)
    assert command._runs == {} and command._baselines == {} and command._pending_runs == {}
    assert command._origin_scopes == [] and command._origin_baseline is None
